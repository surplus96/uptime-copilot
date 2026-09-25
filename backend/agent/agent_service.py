"""
6-4. HITL + 멀티에이전트 그래프 최종 통합 (Azure PdM 실데이터 기반).
6-1(라우팅) 구조에, 실제 고장(failures) 이력이 있는 진단에는 사람의 승인을 기다리는
HITL 체크포인트를 추가한다.
"""

import logging
import operator
import re
import time
from typing import Annotated, Literal

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from langsmith.wrappers import wrap_openai
from pydantic import BaseModel

import cmms_client
import notify
from core import llm_provider
from core.harness import check_output_forbidden_words
from data import pdm_operations, pdm_telemetry, sim_query
from ml.predict import predict_failure_risk
from rag.pump_manual import ERROR_TO_COMPONENT, PUMP_MAINTENANCE_PROCEDURES, SIGNAL_TO_COMPONENT

load_dotenv()
logger = logging.getLogger(__name__)
client = llm_provider.get_client()

MODEL = llm_provider.get_model()
logger.info(f"agent_service: provider={llm_provider.get_provider_name()}, model={MODEL}")

def initialize_agent(langsmith_client, checkpointer) -> None:
    """main.py의 lifespan에서 호출: main.py와 같은 LangSmith Client(PII 익명화 포함)로
    이 모듈의 OpenAI 클라이언트를 감싸고, HITL 승인 대기 상태를 담을 체크포인터로 그래프를
    컴파일한다. InMemorySaver(프로세스 메모리)는 `uvicorn --reload`나 재시작마다 대기 중인
    승인이 전부 사라지므로, main.py가 넘겨주는 디스크 기반 SqliteSaver로 교체함.
    """
    global client, app
    client = wrap_openai(client, tracing_extra={"client": langsmith_client})
    app = graph.compile(checkpointer=checkpointer)

class SupervisorState(BaseModel):
    user_message: str
    category: str | None = None
    machine_id: int | None = None
    severity: str | None = None
    diagnosis: str | None = None
    involved_components: list[str] = []
    component_evidence: dict[str, str] = {}
    component_manuals: dict[str, str] = {}   # 부품별 매뉴얼 증상 설명
    component_actions: dict[str, str] = {}   # 부품별 표준 조치사항
    perspectives: Annotated[list[str], operator.add] = []
    perspective_assessments: Annotated[list[dict], operator.add] = []
    risk_probability: float | None = None
    risk_component: str | None = None
    priority: str | None = None
    recommend_shutdown: bool | None = None
    priority_reasons: list[str] = []
    approved: bool | None = None
    work_order: str | None = None
    result: str | None = None


class RouteDecision(BaseModel):
    category: Literal["오류_진단", "정비_일정", "일반_문의"]
    machine_id: int | None = None  # 언급된 설비 번호. 분류와 한 번에 뽑아서 별도 추출 호출을 없앤다
    reason: str


class PerspectiveAssessment(BaseModel):
    risk_level: Literal["낮음", "중간", "높음"]
    recommended_window: Literal["즉시", "24시간 이내", "1주 이내", "정기 점검 시"]
    rationale: str
    # requires_shutdown은 여기 없다 - _derive_requires_shutdown()이 risk_level/
    # recommended_window로부터 코드로 계산한다. 로컬 모델(Ollama)이 이 필드를 독립적으로
    # 채우면서 risk_level="중간"인데 requires_shutdown=true 같은 내적 모순을 실제로 냈다
    # (2026-09-25 실측, docs/decisions.md) - 세 번째 필드를 LLM에게 맡기는 대신 앞의 두
    # 필드로부터 결정론적으로 유도해서 모순 자체가 구조적으로 불가능하게 만든다.


def _derive_requires_shutdown(risk_level: str, recommended_window: str) -> bool:
    """위험도와 권장 조치 시점이 둘 다 최고 수준으로 일치할 때만 정지를 권고한다 - LLM이
    임의로 고르던 것보다 발동 조건이 좁아졌지만(보수적), '위험도 중간인데 정지 필요' 같은
    모순은 이 정의로는 애초에 나올 수 없다."""
    return risk_level == "높음" and recommended_window == "즉시"


_MACHINE_NUMBER_RE = re.compile(r"(설비|기계|장비)\s*\d+\s*번|\d+\s*번\s*(설비|기계|장비)")
# 2026-09-25 실측(docs/decisions.md): 설비 번호 없이 증상만 신고하는 문장("설비가
# 이상해요", "소음이 심해요")도 일반_문의로 오분류되면 general_node(DB 조회 없는
# 자유생성)로 빠진다. "오류"처럼 너무 넓은 단어는 "오류 코드 종류에는 어떤 게 있어?"
# 같은 순수 정보성 질문까지 잘못 낚아채므로(골든셋 g40), 실제 증상 신고 문형에만
# 맞는 구체적인 구절을 쓴다 - 이 목록은 완전하지 않고, 골든셋 평가가 새 빈틈을
# 찾을 때마다 넓혀가는 걸 전제로 한다(tests/eval/test_golden.py의
# misroute_to_ungrounded 회귀 검사 참고).
_SYMPTOM_PHRASES = ("이상해", "오류가", "소음이", "고장났", "고장이 났", "안 움직", "멈췄")
# 설비 번호 없는 점검/정비 일정 문의도 같은 이유로 새는 걸 실측함("점검은 보통 얼마나
# 자주 해야 해?" 같은 순수 정보성 질문(골든셋 g35)과 겹치지 않게, 특정 대상을 전제하는
# 구절만 쓴다.
_SCHEDULE_PHRASES = ("다음 점검", "정비 스케줄", "점검 일정", "정비 일정")


def _mentions_machine_trouble(message: str) -> bool:
    return bool(_MACHINE_NUMBER_RE.search(message)) or any(p in message for p in _SYMPTOM_PHRASES)


def _mentions_schedule_request(message: str) -> bool:
    return any(p in message for p in _SCHEDULE_PHRASES)


def route_node(state: SupervisorState) -> dict:
    completion = llm_provider.parse_with_retry(
        client,
        model=MODEL,
        reasoning_effort="none",
        messages=[
            {"role": "system", "content": (
                "사용자 문의를 아래 세 카테고리 중 하나로 분류하고, 언급된 설비 번호가 있으면 "
                "함께 추출하세요(없으면 machine_id는 null).\n"
                "- 오류_진단: 특정 설비 번호의 오류/증상에 대한 원인·조치 문의\n"
                "- 정비_일정: 다음 점검일 문의\n"
                "- 일반_문의: 그 외 일반적인 질문"
            )},
            {"role": "user", "content": "3번 설비에서 오류 났는데 뭐가 문제야?"},
            {"role": "assistant", "content": '{"category": "오류_진단", "machine_id": 3, "reason": "특정 설비 번호를 언급하며 원인을 묻고 있음"}'},
            {"role": "user", "content": "15번 설비 다음 점검은 언제야?"},
            {"role": "assistant", "content": '{"category": "정비_일정", "machine_id": 15, "reason": "특정 설비의 다음 점검일을 묻고 있음"}'},
            {"role": "user", "content": state.user_message},
        ],
        response_format=RouteDecision,
    )

    decision = completion.choices[0].message.parsed
    category = decision.category
    if category == "일반_문의" and _mentions_machine_trouble(state.user_message):
        logger.info(f"[라우터] LLM 판정({category})을 안전장치가 오류_진단으로 전환 (설비 이상 언급됨)")
        category = "오류_진단"
    elif category == "일반_문의" and _mentions_schedule_request(state.user_message):
        logger.info(f"[라우터] LLM 판정({category})을 안전장치가 정비_일정으로 전환 (점검 일정 언급됨)")
        category = "정비_일정"
    logger.info(f"[라우터] {category}")
    return {"category": category, "machine_id": decision.machine_id}


RISK_THRESHOLD = 0.5  # docs/model_card.md 7절: 이 값에서 이미 오경보 <=0.001/설비/일

_risk_model_warned = False

def _predict_risk_safe(machine_id: int) -> dict | None:
    """모델 파일이 없으면(python -m ml.train 미실행) None을 반환하고 Z-score로 대체한다 -
    위험도 모델은 선택 기능이라 없다고 진단 자체가 막히면 안 된다."""
    global _risk_model_warned
    try:
        return predict_failure_risk(machine_id)
    except FileNotFoundError:
        if not _risk_model_warned:
            logger.warning("위험도 모델 파일이 없어 Z-score만으로 판정합니다 (python -m ml.train 필요)")
            _risk_model_warned = True
        return None



def _diagnose_machine(machine_id: int, within_days: int = 30) -> dict:
    machine_info = pdm_operations.get_machine_info(machine_id)
    if "error" in machine_info:
        return {
            "machine_id": machine_id,
            "diagnosis": "등록되지 않은 번호입니다 (1~100번 설비만 존재).",
            "severity": "일반",
            "involved_components": [],
            "component_evidence": {},
            "evidence_at": None,
        }

    recent_errors = pdm_operations.get_recent_errors(machine_id, limit=3)
    failure = pdm_operations.check_recent_failure(machine_id, within_days=within_days)
    anomaly = pdm_telemetry.detect_anomaly(machine_id)
    risk = _predict_risk_safe(machine_id)
    top_risk_comp, top_risk_proba = (None, None)
    if risk:
        top_risk_comp = max(risk, key=lambda c: risk[c]["probability"])
        top_risk_proba = risk[top_risk_comp]["probability"]
    # CP-U2(2026-09-23, docs/decisions.md) 지적: 0.0을 기본값으로 쓰면 "모델이 없음"과
    # "모델이 0%라고 답함"이 구분이 안 된다 - None으로 유지하고, 비교는 이 플래그로만 한다.
    risk_alarm = top_risk_proba is not None and top_risk_proba >= RISK_THRESHOLD

    error_summary = (
        "; ".join(f"{e['datetime']} {e['errorID']}({e['description']})" for e in recent_errors)
        if recent_errors else "최근 오류 이력 없음"
    )
    diagnosis_text = f"최근 오류 이력: {error_summary}"
    if failure:
        diagnosis_text += f" / 실제 고장 이력: {failure['datetime']} {failure['component']}({failure['description']})"
    if anomaly.get("has_anomaly"):
        flagged_desc = ", ".join(anomaly["flagged_signals"])
        diagnosis_text += f" / 텔레메트리 이상 감지(사전 경보): {flagged_desc} 신호가 평소 대비 통계적으로 벗어남"
    if risk_alarm:
        # "고장확률"이라 쓰면 보정된 확률로 읽히지만 이 값은 보정되지 않은 모델 출력값이다
        # (priority_rule_node 주석 참조) - 캘리브레이션 없이 "72%"만 보여주면 현장에서
        # 실제 사고 발생 비율로 오독할 수 있어 라벨과 괄호로 명시한다 (interface-reviewer 지적).
        diagnosis_text += (
            f" / 예측 모델: 24시간 내 {top_risk_comp} 위험도 점수 {top_risk_proba:.0%}"
            "(모델 출력값, 보정되지 않은 값이라 실제 발생 확률과 다를 수 있음)"
        )

    model = machine_info.get("model")
    if model:
        vulnerable = pdm_operations.get_component_failure_stats(model)
        if vulnerable:
            top = vulnerable[0]
            diagnosis_text += (
                f" / 참고: {model} 기종은 설비당 평균 {top['failures_per_machine']}회로 "
                f"{top['component']}({top['component_description']}) 고장이 가장 잦음"
            )

    component_evidence: dict[str, list[str]] = {}
    for e in recent_errors:
        comp = ERROR_TO_COMPONENT.get(e["errorID"])
        if comp:
            component_evidence.setdefault(comp, []).append(f"{e['datetime']} {e['errorID']}({e['description']})")
    if failure:
        component_evidence.setdefault(failure["component"], []).append(
            f"실제 고장 발생: {failure['datetime']} {failure['description']}"
        )
    if anomaly.get("has_anomaly"):
        for signal in anomaly["flagged_signals"]:
            comp = SIGNAL_TO_COMPONENT.get(signal)
            if comp:
                component_evidence.setdefault(comp, []).append(
                    f"텔레메트리 이상(사전 경보): {signal} 신호가 통계적으로 벗어남"
                )
    if risk_alarm and top_risk_comp:
        top_feature_names = ", ".join(f["feature"] for f in risk[top_risk_comp]["top_features"])
        component_evidence.setdefault(top_risk_comp, []).append(
            f"예측 모델: 24시간 내 위험도 점수 {top_risk_proba:.0%}(보정되지 않은 값), 주요 근거: {top_feature_names}"
        )

    if failure:
        severity = "긴급"
    elif risk_alarm:
        severity = "주의"  # 위험도 모델 근거 (계획서 3-6: Z-score는 보조 근거로 격하)
    elif risk is None and anomaly.get("has_anomaly"):
        severity = "주의"  # 모델 파일이 없을 때만 Z-score로 대체(하위 호환)
    else:
        severity = "일반"

    # 근거시각: 실제로 severity 판정에 쓰인 것들 중 가장 최근 것
    evidence_times = []
    if recent_errors:
        evidence_times.append(recent_errors[0]["datetime"])   # 이미 최신순 정렬됨
    if failure:
        evidence_times.append(failure["datetime"])
    if anomaly.get("has_anomaly"):
        evidence_times.append(anomaly["as_of"])
    if risk_alarm:
        evidence_times.append(sim_query.dataset_now())

    evidence_at = max(evidence_times) if evidence_times else None

    return {
        "machine_id": machine_id,
        "diagnosis": diagnosis_text,
        "severity": severity,
        "involved_components": list(component_evidence.keys()),
        "component_evidence": {comp: "; ".join(v) for comp, v in component_evidence.items()},
        "evidence_at": evidence_at,
        "risk_probability": top_risk_proba,
        "risk_component": top_risk_comp,
    }


def diagnosis_node(state: SupervisorState) -> dict:
    """route_node가 분류와 함께 뽑아둔 설비 번호(state.machine_id)로 진단한다 - 별도
    추출 호출을 하지 않는다(2026-09-25, 라우팅+추출 호출 병합으로 지연 절감)."""
    if state.machine_id is None:
        return {"severity": "일반", "diagnosis": "몇 번 설비인지 알려주시겠어요? 예: '3번 설비 상태가 이상해요'"}
    result = _diagnose_machine(state.machine_id)
    logger.info(f"[진단] machine #{state.machine_id} -> {result['severity']}")
    return {k: v for k, v in result.items() if k != "evidence_at"}


def scan_machines(machine_ids) -> tuple[list[dict], list[str]]:
    """주어진 설비 목록만 스캔한다. 결과와, '진짜 새로운' 알림 문구 목록을 함께 돌려준다 -
    개별 발송할지 묶어서 보낼지는 호출부가 정한다."""
    from data import event_store

    completed_evidence_map = event_store.get_completed_evidence_map()
    already_detected_map = event_store.get_detected_evidence_map()
    detected = []
    alerts = []
    for machine_id in machine_ids:
        result = _diagnose_machine(machine_id, within_days=1)
        if result["severity"] not in ("긴급", "주의"):
            continue
        completed_evidence_at = completed_evidence_map.get(machine_id)
        if completed_evidence_at and result["evidence_at"] and result["evidence_at"] <= completed_evidence_at:
            continue
        is_genuinely_new = already_detected_map.get(machine_id) != result["evidence_at"]
        event_store.save_event(machine_id, result["severity"], result["diagnosis"], result["evidence_at"])
        if is_genuinely_new:
            alerts.append(f"[{result['severity']}] 설비 #{machine_id} 이상 감지\n{result['diagnosis']}")
        detected.append(result)
    return detected, alerts


def scan_all_machines() -> list[dict]:
    """전체 100대 설비를 LLM 없이 순수 데이터로 스캔한다. 완료 처리된 설비는
    그 이후 실제로 새 근거(evidence_at)가 생긴 경우에만 재등장한다."""
    detected, alerts = scan_machines(range(1, 101))
    for text in alerts:
        notify.send_alert(text)
        time.sleep(1)
    return detected


def schedule_node(state: SupervisorState) -> dict:
    """route_node가 분류와 함께 뽑아둔 설비 번호(state.machine_id)를 쓴다 - 별도 추출
    호출을 하지 않는다(2026-09-25, 라우팅+추출 호출 병합으로 지연 절감)."""
    if state.machine_id is None:
        return {"machine_id": None, "result": "몇 번 설비의 정비 일정인지 알려주시겠어요?"}
    schedule_info = pdm_operations.estimate_next_maintenance(state.machine_id)

    completion2 = client.chat.completions.create(
        model=MODEL,
        **llm_provider.filter_kwargs(reasoning_effort="none"),
        max_completion_tokens=300,
        messages=[
            {"role": "system", "content": (
                f"설비 #{state.machine_id}의 정비 이력 데이터: {schedule_info}\n"
                "이 정보를 바탕으로 답하세요. 실제 오늘 날짜가 언제인지는 알 수 없으니, "
                "'현재 날짜'나 '오늘 기준으로' 같은 표현으로 임의의 날짜를 추측하거나 언급하지 마세요. "
                "데이터에 있는 마지막 점검일과 다음 점검 예정일만 그대로 전달하세요."
            )},
            {"role": "user", "content": state.user_message},
        ],
    )
    return {"machine_id": state.machine_id, "result": completion2.choices[0].message.content}


def general_node(state: SupervisorState) -> dict:
    completion = client.chat.completions.create(
        model=MODEL,
        **llm_provider.filter_kwargs(reasoning_effort="none"),
        max_completion_tokens=500,
        messages=[
            {"role": "system", "content": "제조 설비 관련 일반적인 질문에 간단히 답하세요."},
            {"role": "user", "content": state.user_message},
        ],
    )
    return {"result": completion.choices[0].message.content}

def manual_lookup_node(state: SupervisorState) -> dict:
    """부품별 표준 매뉴얼 근거(증상 설명)와 조치사항을 코드로 직접 조회한다. 매뉴얼 문서가
    작아 RAG 검색이 부품을 정밀하게 구분하지 못하는 문제가 있어, 이미 정확히 알고 있는 데이터
    (PUMP_MAINTENANCE_PROCEDURES)를 직접 사용한다."""
    component_manuals = {}
    component_actions = {}
    for comp in state.involved_components:
        procedure = PUMP_MAINTENANCE_PROCEDURES.get(comp, {})
        component_manuals[comp] = procedure.get("symptom", "")
        steps = procedure.get("steps", [])
        component_actions[comp] = " ".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    return {"component_manuals": component_manuals, "component_actions": component_actions}


def validate_work_order_node(state: SupervisorState) -> dict:
    """work_order가 승인 요청·Slack·CMMS 전송에 쓰이기 전에 반드시 통과해야 하는 검증
    관문. 그래프 노드로 만들어서, 검증 실패 시 예외가 여기서 그래프 실행을 멈추므로
    approval_node/finalize_node는 구조적으로 절대 도달할 수 없다."""
    check_output_forbidden_words(state.work_order)
    return {}


def work_order_node(state: SupervisorState) -> dict:
    """부품별 작업지시서 블록을 코드로 결정론적으로 조립한다. [증상]은 diagnosis_node가 이미
    확정한 실제 근거(component_evidence)를, [매뉴얼 근거]/[조치사항]은 manual_lookup_node가
    조회해둔 표준 절차 데이터를 그대로 사용한다 - LLM이 사실을 재서술하지 않으므로
    사실 왜곡(hallucination)이 구조적으로 발생할 수 없다."""
    def build_section(component: str) -> str:
        return (
            f"[부품] {component}\n"
            f"[증상] {state.component_evidence.get(component, '')}\n"
            f"[매뉴얼 근거] {state.component_manuals.get(component, '')}\n"
            f"[조치사항] {state.component_actions.get(component, '')}\n"
            f"[긴급도] {state.severity}"
        )

    header = f"설비 #{state.machine_id}"
    if state.priority:
        # priority_rule_node는 severity="긴급"일 때만 실행되므로, "주의" 건에는 이 블록이 안 붙는다.
        header += (
            f"\n[우선순위] {state.priority}"
            f"\n[정지 권고] {'예' if state.recommend_shutdown else '아니오'}"
            f"\n[근거 관점] {'; '.join(state.priority_reasons)}"
        )

    blocks = [build_section(comp) for comp in state.involved_components]
    return {"work_order": f"{header}\n\n" + "\n\n".join(blocks)}

_DEFAULT_ASSESSMENT = {
    "risk_level": "중간",
    "recommended_window": "24시간 이내",
    "rationale": "(자동 평가 실패 - 사람이 직접 판단 필요)",
    "is_fallback": True,  # CP-U2 지적: 정직한 "중간" 판정과 구분해야 priority_rule이 오판 안 함
}


def _assess_perspective(label: str, system_prompt: str, diagnosis: str) -> dict:
    """세 관점 노드(안전/생산/정비)가 공유하는 평가 로직. 구조화 출력이 거부되거나
    예외가 나면 '위험도 중간·24시간 이내 조치'라는 보수적 기본값으로 대체한다 - 관점이
    아예 빠지는 것보다 사람이 알아챌 수 있는 형태로 안전하게 죽인다."""
    try:
        completion = llm_provider.parse_with_retry(
            client,
            model=MODEL,
            reasoning_effort="none",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": diagnosis},
            ],
            response_format=PerspectiveAssessment,
        )

        message = completion.choices[0].message
        if message.refusal or message.parsed is None:
            raise ValueError(f"관점 평가 모델이 응답을 거부함: {message.refusal}")
        assessment = message.parsed.model_dump()
        assessment["is_fallback"] = False
    except Exception as e:
        logger.warning(f"[{label}] 관점 평가 실패, 안전한 기본값으로 대체: {e}")
        assessment = dict(_DEFAULT_ASSESSMENT)

    assessment["requires_shutdown"] = _derive_requires_shutdown(
        assessment["risk_level"], assessment["recommended_window"]
    )
    return {
        "perspectives": [f"[{label}] {assessment['rationale']}"],
        "perspective_assessments": [{"label": label, **assessment}],
    }


def safety_perspective_node(state: SupervisorState) -> dict:
    return _assess_perspective(
        "안전", "당신은 현장 안전 담당자입니다. 아래 사고 상황의 안전 위험도를 평가하세요.", state.diagnosis
    )


def production_perspective_node(state: SupervisorState) -> dict:
    return _assess_perspective(
        "생산", "당신은 생산 관리자입니다. 아래 사고 상황이 생산에 미치는 영향을 평가하세요.", state.diagnosis
    )


def maintenance_perspective_node(state: SupervisorState) -> dict:
    return _assess_perspective(
        "정비", "당신은 정비 기술자입니다. 아래 사고 상황의 수리 난이도를 평가하세요.", state.diagnosis
    )


def priority_rule_node(state: SupervisorState) -> dict:
    """3관점(안전/생산/정비) + 위험도 모델을 결정론 규칙으로 종합해 우선순위(P1~P3)와
    정지 권고를 낸다. LLM 호출 없음 - 전부 이미 state에 있는 값으로 계산.

    CP-U2 교차 검토(2026-09-23, docs/decisions.md) 반영:
    - 평가 실패(is_fallback)로 대체된 관점은 '정직한 중간'과 구분해서 판정에서 제외한다.
    - 관점을 과반 투표로 세지 않는다 - 셋 다 같은 모델·같은 입력에서 프롬프트만 다르게
      뽑힌 것이라 독립적인 세 명의 판단이 아니다. 안전 관점의 '높음'은 다른 두 관점이
      반박해도 격하되지 않는다.
    - 위험도 모델 확률은 보정되지 않은 값(학습 시 is_unbalance=True)이라 세부 구간으로
      나누지 않고, 이미 검증된 RISK_THRESHOLD 하나로 on/off 경보로만 쓴다.
    - 모델 경보 단독으로는 정지 권고를 내지 않는다(model_card.md 8절 일반화 한계 경고) -
      정지 권고는 관점 판단에서만 나오고, 모델 경보는 우선순위를 올리는 '근거'로만 반영.
    - 유효한 관점 평가가 2개 미만이면(평가 대부분 실패) P3로 방치하지 않고 P2를 하한으로
      한다 - 이 노드는 severity='긴급'(실제 고장 확인됨)일 때만 도니까, "판정 근거가
      부족하다"는 이유로 우선순위 목록 맨 아래로 밀려나면 안 된다.
    """
    valid = [p for p in state.perspective_assessments if not p.get("is_fallback")]
    shutdown = [p["label"] for p in valid if p["requires_shutdown"]]
    high = [p["label"] for p in valid if p["risk_level"] == "높음"]
    immediate = [p["label"] for p in valid if p["recommended_window"] == "즉시"]
    model_alarm = state.risk_probability is not None and state.risk_probability >= RISK_THRESHOLD
    insufficient = len(valid) < 2

    reasons = []
    if shutdown:
        reasons.append(f"{'·'.join(shutdown)} 관점 정지 필요")
    if "안전" in high:
        reasons.append("안전 관점 위험도 높음")
    if model_alarm:
        reasons.append(f"위험도 모델 경보 ({state.risk_component} {state.risk_probability:.0%})")
    if high and "안전" not in high:
        reasons.append(f"{'·'.join(label for label in high if label != '안전')} 관점 위험도 높음")
    if immediate:
        reasons.append(f"{'·'.join(immediate)} 관점 즉시 조치 권장")
    if insufficient:
        reasons.append(f"관점 평가 {3 - len(valid)}건 실패 - 판정 근거 부족")

    if shutdown or "안전" in high or model_alarm:
        priority = "P1"
    elif high or immediate or insufficient:
        priority = "P2"
    else:
        priority = "P3"

    return {
        "priority": priority,
        "recommend_shutdown": bool(shutdown),  # 정지 권고는 관점 판단에서만 나온다 - 모델 단독 경보로는 안 냄
        "priority_reasons": reasons or ["특이 사항 없음"],
    }


def approval_node(state: SupervisorState) -> dict:
    """HITL 체크포인트: 작성된 작업지시서 초안 + 관점 의견을 보여주고 승인을 기다린다."""
    perspectives_text = "\n".join(state.perspectives) if state.perspectives else ""
    extra = f"\n\n[관련 관점 의견]\n{perspectives_text}" if perspectives_text else ""
    decision = interrupt({
        "message": f"[승인 필요] 설비 #{state.machine_id}에서 긴급 상황 발생.\n\n{state.work_order}{extra}\n\n"
                f"이 작업지시서로 현장 책임자에게 즉시 보고를 진행할까요?",
        "work_order": state.work_order,
        "perspectives": state.perspectives,
    })

    logger.info(f"[승인 재개] 사람의 결정: {decision}")
    return {"approved": decision}


def finalize_node(state: SupervisorState) -> dict:
    if state.severity == "긴급":
        if state.approved:
            result = f"[긴급 승인됨]\n{state.work_order}\n\n-> 승인 처리되었습니다. 현장 책임자에게는 별도로 알려야 합니다."
            notify.send_alert(f"[긴급 승인] 설비 #{state.machine_id} 작업지시서 승인됨\n{state.work_order}")
            try:
                cmms_client.push_work_order(state.machine_id, state.work_order)
            except Exception as e:
                logger.error(f"[CMMS push 실패] {e}")
        else:
            result = f"[긴급 반려됨]\n{state.work_order}\n\n-> 반려 처리되었습니다. 별도 조치는 이루어지지 않았습니다."
    elif state.severity == "주의":
        result = f"[사전 경보 - 예방 조치 권장]\n{state.work_order}"
    else:
        result = state.diagnosis if state.machine_id is None else f"설비 #{state.machine_id}: {state.diagnosis}"
    return {"result": result}


def route_condition(state: SupervisorState) -> str:
    return {"오류_진단": "diagnosis", "정비_일정": "schedule", "일반_문의": "general"}[state.category]


def severity_condition(state: SupervisorState) -> str:
    """긴급/주의는 매뉴얼 조회를 거치고, 일반/미등록은 바로 종료."""
    return "manual_lookup" if state.severity in ("긴급", "주의") else "finalize"


def after_manual_condition(state: SupervisorState) -> list[str]:
    """긴급만 3관점 병렬 평가로 팬아웃, 주의는 바로 작업지시서로."""
    return ["safety", "production", "maintenance"] if state.severity == "긴급" else ["work_order"]

def needs_approval_condition(state: SupervisorState) -> str:
    """긴급(실제 고장 근거)만 사람 승인을 거친다."""
    return "approval" if state.severity == "긴급" else "finalize"



graph = StateGraph(SupervisorState)
graph.add_node("route", route_node)
graph.add_node("diagnosis", diagnosis_node)
graph.add_node("schedule", schedule_node)
graph.add_node("general", general_node)
graph.add_node("manual_lookup", manual_lookup_node)
graph.add_node("work_order", work_order_node)
graph.add_node("validate_work_order", validate_work_order_node)
graph.add_node("approval", approval_node)
graph.add_node("finalize", finalize_node)
graph.add_node("safety", safety_perspective_node)
graph.add_node("production", production_perspective_node)
graph.add_node("maintenance", maintenance_perspective_node)
graph.add_node("merge_perspectives", lambda state: {})
graph.add_node("priority_rule", priority_rule_node)

graph.add_edge(START, "route")
graph.add_conditional_edges("route", route_condition, {
    "diagnosis": "diagnosis", "schedule": "schedule", "general": "general",
})
graph.add_conditional_edges("diagnosis", severity_condition, {
    "manual_lookup": "manual_lookup", "finalize": "finalize",
})
graph.add_conditional_edges("manual_lookup", after_manual_condition, {
    "safety": "safety", "production": "production", "maintenance": "maintenance", "work_order": "work_order",
})
graph.add_edge("safety", "merge_perspectives")
graph.add_edge("production", "merge_perspectives")
graph.add_edge("maintenance", "merge_perspectives")
graph.add_edge("merge_perspectives", "priority_rule")
graph.add_edge("priority_rule", "work_order")
graph.add_edge("work_order", "validate_work_order")
graph.add_conditional_edges("validate_work_order", needs_approval_condition, {
    "approval": "approval", "finalize": "finalize",
})
graph.add_edge("approval", "finalize")
graph.add_edge("finalize", END)
graph.add_edge("schedule", END)
graph.add_edge("general", END)


app = None  # main.py의 lifespan이 initialize_agent()를 호출할 때 SqliteSaver로 컴파일됨


def _validate_output(state_dict: dict) -> None:
    """work_order(긴급/주의 작업지시서)와 result(일반 문의/정비 일정 답변) 둘 다에 최소한의
    하네스 검증을 적용한다. work_order는 전부 결정론적으로 조립되므로(component_evidence +
    PUMP_MAINTENANCE_PROCEDURES) 지어낼 여지가 없어 Faithfulness/안전성 judge는 불필요한
    지연·비용·불안정성만 추가한다 - PII 형식 검사만으로 충분하다. 반면 result는 general_node/
    schedule_node가 LLM으로 자유 생성한 텍스트라 같은 PII 검사가 실제로 의미 있는데, 예전엔
    work_order 필드만 봐서 이 두 경로가 검사를 완전히 피해갔다 (code-quality-reviewer 지적,
    2026-09-18)."""
    for text in (state_dict.get("work_order"), state_dict.get("result")):
        if text:
            check_output_forbidden_words(text)


def start_agent(user_message: str, thread_id: str) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(SupervisorState(user_message=user_message), config=config)
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        _validate_output(payload)
        return {
            "status": "pending_approval",
            "message": payload["message"],
            "work_order": payload.get("work_order"),
            "perspectives": payload.get("perspectives", []),
        }
    _validate_output(result)
    return {"status": "done", "result": result["result"], "work_order": result.get("work_order")}


def resume_agent(thread_id: str, approved: bool) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(Command(resume=approved), config=config)
    _validate_output(result)
    return {"status": "done", "result": result["result"], "work_order": result.get("work_order")}

def check_pending(thread_id: str) -> dict | None:
    """타임아웃 등으로 잃어버린 pending_approval을 thread_id로 복구 조회한다.
    app.invoke를 다시 하지 않고 체크포인트 상태만 읽는다 - 재실행/중복 side-effect 없음."""
    config = {"configurable": {"thread_id": thread_id}}
    state = app.get_state(config)
    if not state.next:  # interrupt 대기 중이 아니면 next가 비어있음
        return None
    for task in state.tasks:
        for pending in task.interrupts:
            payload = pending.value
            _validate_output(payload)
            return {
                "status": "pending_approval",
                "message": payload["message"],
                "work_order": payload.get("work_order"),
                "perspectives": payload.get("perspectives", []),
            }
    return None
