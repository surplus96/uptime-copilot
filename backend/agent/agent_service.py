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
from langgraph.checkpoint.memory import InMemorySaver

from data import pdm_operations, pdm_telemetry

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class SupervisorState(BaseModel):
    user_message: str
    category: str | None = None
    machine_id: int | None = None
    severity: str | None = None
    diagnosis: str | None = None
    perspectives: Annotated[list[str], operator.add] = []
    approved: bool | None = None
    result: str | None = None


class RouteDecision(BaseModel):
    category: Literal["오류_진단", "정비_일정", "일반_문의"]
    reason: str


class IncidentExtraction(BaseModel):
    machine_id: int


def route_node(state: SupervisorState) -> dict:
    completion = client.chat.completions.parse(
        model="gpt-4o-mini",
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


def diagnosis_node(state: SupervisorState) -> dict:
    """설비 번호를 추출하고, 실제 이력 데이터 + 텔레메트리 이상탐지 + 기종별 통계로 진단한다."""
    completion = client.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "사용자 문의에서 설비 번호(machine_id, 정수)를 추출하세요."},
            {"role": "user", "content": state.user_message},
        ],
        response_format=IncidentExtraction,
    )
    machine_id = completion.choices[0].message.parsed.machine_id

    machine_info = pdm_operations.get_machine_info(machine_id)
    recent_errors = pdm_operations.get_recent_errors(machine_id, limit=3)
    failure = pdm_operations.check_recent_failure(machine_id, within_days=30)
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

    if failure:
        severity = "긴급"
    elif anomaly.get("has_anomaly"):
        severity = "주의"
    else:
        severity = "일반"
    print(f"[진단] machine #{machine_id} -> {severity}")
    return {"machine_id": machine_id, "diagnosis": diagnosis_text, "severity": severity}


def schedule_node(state: SupervisorState) -> dict:
    completion = client.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "사용자 문의에서 설비 번호(machine_id, 정수)를 추출하세요."},
            {"role": "user", "content": state.user_message},
        ],
        response_format=IncidentExtraction,
    )
    machine_id = completion.choices[0].message.parsed.machine_id
    schedule_info = pdm_operations.estimate_next_maintenance(machine_id)

    completion2 = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": f"설비 #{machine_id}의 정비 이력 데이터: {schedule_info}\n이 정보를 바탕으로 답하세요."},
            {"role": "user", "content": state.user_message},
        ],
    )
    return {"machine_id": machine_id, "result": completion2.choices[0].message.content}


def general_node(state: SupervisorState) -> dict:
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "제조 설비 관련 일반적인 질문에 간단히 답하세요."},
            {"role": "user", "content": state.user_message},
        ],
    )
    return {"result": completion.choices[0].message.content}


def safety_perspective_node(state: SupervisorState) -> dict:
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "당신은 현장 안전 담당자입니다. 아래 사고 상황의 안전 위험도를 2문장 이내로 평가하세요."},
            {"role": "user", "content": state.diagnosis},
        ],
    )
    return {"perspectives": [f"[안전] {completion.choices[0].message.content}"]}


def production_perspective_node(state: SupervisorState) -> dict:
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "당신은 생산 관리자입니다. 아래 사고 상황이 생산에 미치는 영향을 2문장 이내로 평가하세요."},
            {"role": "user", "content": state.diagnosis},
        ],
    )
    return {"perspectives": [f"[생산] {completion.choices[0].message.content}"]}


def maintenance_perspective_node(state: SupervisorState) -> dict:
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "당신은 정비 기술자입니다. 아래 사고 상황의 수리 난이도를 2문장 이내로 평가하세요."},
            {"role": "user", "content": state.diagnosis},
        ],
    )
    return {"perspectives": [f"[정비] {completion.choices[0].message.content}"]}


def approval_node(state: SupervisorState) -> dict:
    """HITL 체크포인트: 병렬로 모인 3개 관점을 같이 보여주고 승인을 기다린다."""
    perspectives_text = "\n".join(state.perspectives)
    decision = interrupt({
        "message": f"[승인 필요] 설비 #{state.machine_id}에서 긴급 상황 발생.\n\n{state.diagnosis}\n\n{perspectives_text}\n\n"
                   f"현장 책임자에게 즉시 보고를 진행할까요?",
        "diagnosis": state.diagnosis,
    })
    print(f"[승인 재개] 사람의 결정: {decision}")
    return {"approved": decision}


def finalize_node(state: SupervisorState) -> dict:
    if state.severity == "긴급":
        if state.approved:
            result = f"[긴급 승인됨] 설비 #{state.machine_id}: {state.diagnosis} -> 현장 책임자에게 즉시 보고되었습니다."
        else:
            result = f"[긴급 반려됨] 설비 #{state.machine_id}: {state.diagnosis} -> 보고가 보류되었습니다."
    else:
        result = f"설비 #{state.machine_id}: {state.diagnosis}"
    return {"result": result}


def route_condition(state: SupervisorState) -> str:
    return {"오류_진단": "diagnosis", "정비_일정": "schedule", "일반_문의": "general"}[state.category]


def severity_condition(state: SupervisorState) -> list[str]:
    """긴급이면 3개 관점 평가로 팬아웃, 아니면 바로 finalize."""
    return ["safety", "production", "maintenance"] if state.severity == "긴급" else ["finalize"]


graph = StateGraph(SupervisorState)
graph.add_node("route", route_node)
graph.add_node("diagnosis", diagnosis_node)
graph.add_node("schedule", schedule_node)
graph.add_node("general", general_node)
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
    "safety": "safety", "production": "production", "maintenance": "maintenance", "finalize": "finalize",
})
graph.add_edge("safety", "merge_perspectives")
graph.add_edge("production", "merge_perspectives")
graph.add_edge("maintenance", "merge_perspectives")
graph.add_edge("merge_perspectives", "approval")
graph.add_edge("approval", "finalize")
graph.add_edge("finalize", END)
graph.add_edge("schedule", END)
graph.add_edge("general", END)

app = graph.compile(checkpointer=InMemorySaver())


def start_agent(user_message: str, thread_id: str) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(SupervisorState(user_message=user_message), config=config)
    if "__interrupt__" in result:
        return {"status": "pending_approval", "message": result["__interrupt__"][0].value["message"]}
    return {"status": "done", "result": result["result"]}


def resume_agent(thread_id: str, approved: bool) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(Command(resume=approved), config=config)
    return {"status": "done", "result": result["result"]}
