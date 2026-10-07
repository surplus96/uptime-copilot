from types import SimpleNamespace

import pytest

from agent import agent_service


def test_two_component_manual_becomes_eight_tasks_with_separate_evidence():
    state = agent_service.SupervisorState(
        user_message="점검", machine_id=90, severity="긴급", priority="P1", recommend_shutdown=True,
        involved_components=["comp1", "comp2"],
        component_evidence={"comp1": "전압 오류", "comp2": "회전속도 오류"},
        parts_status={"comp1": {"on_hand": 2, "min_stock": 3, "lead_time_days": 14,
                                "needs_procurement": True, "shortage": True, "eol_soon": False}},
    )
    state = state.model_copy(update=agent_service.manual_lookup_node(state))
    output = agent_service.work_order_node(state)
    payload = output["cmms_payload"]
    assert len(payload["tasks"]) == 8
    assert "권선 절연저항 측정" not in payload["description"]
    assert "권선 절연저항 측정" in output["work_order"]
    assert "전압 오류" in payload["tasks"][0]["notes"]
    assert "회전속도 오류" not in payload["tasks"][0]["notes"]
    assert "합성 부품 정보" in payload["tasks"][0]["notes"]
    assert "회전속도 오류" in payload["tasks"][4]["notes"]


def test_old_approval_checkpoint_uses_approved_steps():
    state = agent_service.SupervisorState(
        user_message="점검", machine_id=90, severity="긴급", involved_components=["comp1"],
        component_actions={"comp1": "1. 승인된 점검 2. 승인된 측정"},
    )
    payload = agent_service._cmms_payload(state)
    assert [task["label"] for task in payload["tasks"]] == ["[comp1] 01 승인된 점검", "[comp1] 02 승인된 측정"]


@pytest.mark.parametrize("approved,severity,next_nodes", [(False, "긴급", ()), (True, "주의", ()), (True, "긴급", ("approval",))])
def test_retry_requires_completed_urgent_approval(monkeypatch, approved, severity, next_nodes):
    app = SimpleNamespace(get_state=lambda config: SimpleNamespace(next=next_nodes, values={
        "approved": approved, "severity": severity,
    }))
    monkeypatch.setattr(agent_service, "app", app)
    with pytest.raises(ValueError, match="완료된 긴급 승인"):
        agent_service.retry_cmms_delivery("thread")


def test_retry_uses_original_approved_order_without_replaying_graph(monkeypatch):
    updates = []
    calls = []
    state = {"user_message": "점검", "approved": True, "severity": "긴급", "machine_id": 90,
             "work_order": "승인된 원문", "cmms_payload": {"title": "제목", "tasks": [{"label": "점검"}]}}
    app = SimpleNamespace(get_state=lambda config: SimpleNamespace(next=(), values=state),
                          update_state=lambda *args, **kwargs: updates.append((args, kwargs)))
    monkeypatch.setattr(agent_service, "app", app)
    monkeypatch.setattr(agent_service.cmms_client, "delivery_record", lambda *args: {
        "status": "partial_tasks", "work_order_id": 123, "display_id": "WO000123"})
    monkeypatch.setattr(agent_service.cmms_client, "push_work_order", lambda *args, **kwargs: calls.append((args, kwargs)) or "sent")
    result = agent_service.retry_cmms_delivery("thread")
    assert result["status"] == "sent"
    assert calls[0][1]["delivery_key"] == "thread"
    assert calls[0][1]["payload"] == state["cmms_payload"]
    assert updates[0][1]["as_node"] == "finalize"
