"""
6-4. HITL + 멀티에이전트 그래프 최종 통합 (Azure PdM 실데이터 기반).
6-1(라우팅) 구조에, 실제 고장(failures) 이력이 있는 진단에는 사람의 승인을 기다리는
HITL 체크포인트를 추가한다.
"""

import os
from typing import Literal, Annotated
import operator
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langsmith.wrappers import wrap_openai

from data import pdm_operations, pdm_telemetry
from rag.pump_manual import SIGNAL_TO_COMPONENT, ERROR_TO_COMPONENT, PUMP_MAINTENANCE_PROCEDURES
from core.harness import check_output_forbidden_words
import notify

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 라우팅/추출/판정 계열 노드 전부가 참조하는 단일 모델 상수. main.py의 DEFAULT_MODEL과
# 같은 OPENAI_MODEL 환경변수를 읽어서, .env 값 하나만 바꾸면 코드 수정 없이 전체가 바뀐다.
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")

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
    approved: bool | None = None
    work_order: str | None = None
    result: str | None = None


class RouteDecision(BaseModel):
    category: Literal["오류_진단", "정비_일정", "일반_문의"]
    reason: str


class IncidentExtraction(BaseModel):
    machine_id: int


def route_node(state: SupervisorState) -> dict:
    completion = client.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": (
                "사용자 문의를 아래 세 카테고리 중 하나로 분류하세요.\n"
                "- 오류_진단: 특정 설비 번호의 오류/증상에 대한 원인·조치 문의\n"
                "- 정비_일정: 다음 점검일 문의\n"
                "- 일반_문의: 그 외 일반적인 질문"
            )},
            {"role": "user", "content": "3번 설비에서 오류 났는데 뭐가 문제야?"},
            {"role": "assistant", "content": '{"category": "오류_진단", "reason": "특정 설비 번호를 언급하며 원인을 묻고 있음"}'},
            {"role": "user", "content": "15번 설비 다음 점검은 언제야?"},
            {"role": "assistant", "content": '{"category": "정비_일정", "reason": "특정 설비의 다음 점검일을 묻고 있음"}'},
            {"role": "user", "content": state.user_message},
        ],
        response_format=RouteDecision,
    )
    decision = completion.choices[0].message.parsed
    print(f"[라우터] {decision.category}")
    return {"category": decision.category}


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

    if failure:
        severity = "긴급"
    elif anomaly.get("has_anomaly"):
        severity = "주의"
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
    evidence_at = max(evidence_times) if evidence_times else None

    return {
        "machine_id": machine_id,
        "diagnosis": diagnosis_text,
        "severity": severity,
        "involved_components": list(component_evidence.keys()),
        "component_evidence": {comp: "; ".join(v) for comp, v in component_evidence.items()},
        "evidence_at": evidence_at,
    }


def diagnosis_node(state: SupervisorState) -> dict:
    """자연어 질문에서 설비 번호를 추출한 뒤 _diagnose_machine()으로 진단한다."""
    completion = client.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": "사용자 문의에서 설비 번호(machine_id, 정수)를 추출하세요."},
            {"role": "user", "content": state.user_message},
        ],
        response_format=IncidentExtraction,
    )
    machine_id = completion.choices[0].message.parsed.machine_id
    result = _diagnose_machine(machine_id)
    print(f"[진단] machine #{machine_id} -> {result['severity']}")
    return {k: v for k, v in result.items() if k != "evidence_at"}


def scan_all_machines() -> list[dict]:
    """전체 100대 설비를 LLM 없이 순수 데이터로 스캔한다. 완료 처리된 설비는
    그 이후 실제로 새 근거(evidence_at)가 생긴 경우에만 재등장한다."""
    from data import event_store

    completed_evidence_map = event_store.get_completed_evidence_map()
    detected = []
    for machine_id in range(1, 101):
        result = _diagnose_machine(machine_id, within_days=1)
        if result["severity"] not in ("긴급", "주의"):
            continue
        completed_evidence_at = completed_evidence_map.get(machine_id)
        if completed_evidence_at and result["evidence_at"] and result["evidence_at"] <= completed_evidence_at:
            continue  # 완료 처리 당시 근거보다 새 근거가 없음 - 재등장 안 시킴
        event_store.save_event(machine_id, result["severity"], result["diagnosis"], result["evidence_at"])
        notify.send_alert(f"[{result['severity']}] 설비 #{machine_id} 이상 감지\n{result['diagnosis']}")
        detected.append(result)
    return detected



def schedule_node(state: SupervisorState) -> dict:
    completion = client.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": "사용자 문의에서 설비 번호(machine_id, 정수)를 추출하세요."},
            {"role": "user", "content": state.user_message},
        ],
        response_format=IncidentExtraction,
    )
    machine_id = completion.choices[0].message.parsed.machine_id
    schedule_info = pdm_operations.estimate_next_maintenance(machine_id)

    completion2 = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": (
                f"설비 #{machine_id}의 정비 이력 데이터: {schedule_info}\n"
                "이 정보를 바탕으로 답하세요. 실제 오늘 날짜가 언제인지는 알 수 없으니, "
                "'현재 날짜'나 '오늘 기준으로' 같은 표현으로 임의의 날짜를 추측하거나 언급하지 마세요. "
                "데이터에 있는 마지막 점검일과 다음 점검 예정일만 그대로 전달하세요."
            )},
            {"role": "user", "content": state.user_message},
        ],
    )
    return {"machine_id": machine_id, "result": completion2.choices[0].message.content}


def general_node(state: SupervisorState) -> dict:
    completion = client.chat.completions.create(
        model=MODEL,
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

    blocks = [build_section(comp) for comp in state.involved_components]
    return {"work_order": f"설비 #{state.machine_id}\n\n" + "\n\n".join(blocks)}


def safety_perspective_node(state: SupervisorState) -> dict:
    completion = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "당신은 현장 안전 담당자입니다. 아래 사고 상황의 안전 위험도를 2문장 이내로 평가하세요."},
            {"role": "user", "content": state.diagnosis},
        ],
    )
    return {"perspectives": [f"[안전] {completion.choices[0].message.content}"]}


def production_perspective_node(state: SupervisorState) -> dict:
    completion = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "당신은 생산 관리자입니다. 아래 사고 상황이 생산에 미치는 영향을 2문장 이내로 평가하세요."},
            {"role": "user", "content": state.diagnosis},
        ],
    )
    return {"perspectives": [f"[생산] {completion.choices[0].message.content}"]}


def maintenance_perspective_node(state: SupervisorState) -> dict:
    completion = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "당신은 정비 기술자입니다. 아래 사고 상황의 수리 난이도를 2문장 이내로 평가하세요."},
            {"role": "user", "content": state.diagnosis},
        ],
    )
    return {"perspectives": [f"[정비] {completion.choices[0].message.content}"]}


def approval_node(state: SupervisorState) -> dict:
    """HITL 체크포인트: 작성된 작업지시서 초안 + 관점 의견을 보여주고 승인을 기다린다."""
    perspectives_text = "\n".join(state.perspectives) if state.perspectives else ""
    extra = f"\n\n[관련 관점 의견]\n{perspectives_text}" if perspectives_text else ""
    decision = interrupt({
        "message": f"[승인 필요] 설비 #{state.machine_id}에서 긴급 상황 발생.\n\n{state.work_order}{extra}\n\n"
                f"이 작업지시서로 현장 책임자에게 즉시 보고를 진행할까요?",
        "work_order": state.work_order,
        "perspectives": state.perspectives,   # ← 추가
    })

    print(f"[승인 재개] 사람의 결정: {decision}")
    return {"approved": decision}


def finalize_node(state: SupervisorState) -> dict:
    if state.severity == "긴급":
        if state.approved:
            result = f"[긴급 승인됨]\n{state.work_order}\n\n-> 승인 처리되었습니다. 현장 책임자에게는 별도로 알려야 합니다."
            notify.send_alert(f"[긴급 승인] 설비 #{state.machine_id} 작업지시서 승인됨\n{state.work_order}")
        else:
            result = f"[긴급 반려됨]\n{state.work_order}\n\n-> 반려 처리되었습니다. 별도 조치는 이루어지지 않았습니다."
    elif state.severity == "주의":
        result = f"[사전 경보 - 예방 조치 권장]\n{state.work_order}"
    else:
        result = f"설비 #{state.machine_id}: {state.diagnosis}"
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
graph.add_node("approval", approval_node)
graph.add_node("finalize", finalize_node)
graph.add_node("safety", safety_perspective_node)
graph.add_node("production", production_perspective_node)
graph.add_node("maintenance", maintenance_perspective_node)
graph.add_node("merge_perspectives", lambda state: {})

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
graph.add_edge("merge_perspectives", "work_order")
graph.add_conditional_edges("work_order", needs_approval_condition, {
    "approval": "approval", "finalize": "finalize",
})
graph.add_edge("approval", "finalize")
graph.add_edge("finalize", END)
graph.add_edge("schedule", END)
graph.add_edge("general", END)


app = None  # main.py의 lifespan이 initialize_agent()를 호출할 때 SqliteSaver로 컴파일됨


def _validate_work_order(state_dict: dict) -> None:
    """작업지시서(긴급/주의)에도 최소한의 하네스 검증을 적용한다. 내용이 전부 결정론적으로
    조립되므로(component_evidence + PUMP_MAINTENANCE_PROCEDURES) 지어낼 여지가 없어
    Faithfulness/안전성 judge는 불필요한 지연·비용·불안정성만 추가한다. PII 형식 검사만 남긴다."""
    work_order = state_dict.get("work_order")
    if not work_order:
        return
    check_output_forbidden_words(work_order)


def start_agent(user_message: str, thread_id: str) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(SupervisorState(user_message=user_message), config=config)
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        _validate_work_order(payload)
        return {
            "status": "pending_approval",
            "message": payload["message"],
            "work_order": payload.get("work_order"),
            "perspectives": payload.get("perspectives", []),
        }
    _validate_work_order(result)
    return {"status": "done", "result": result["result"], "work_order": result.get("work_order")}


def resume_agent(thread_id: str, approved: bool) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(Command(resume=approved), config=config)
    _validate_work_order(result)
    return {"status": "done", "result": result["result"], "work_order": result.get("work_order")}
