import pytest

import agent.agent_service as agent_service


def test_diagnosis_node_asks_for_machine_id_when_missing(monkeypatch):
    monkeypatch.setattr(agent_service, "_extract_machine_id", lambda msg: None)

    state = agent_service.SupervisorState(user_message="설비가 이상해")
    result = agent_service.diagnosis_node(state)

    assert result["severity"] == "일반"
    assert "설비" in result["diagnosis"]


def test_validate_work_order_node_raises_on_forbidden_content(monkeypatch):
    def _raise(text):
        raise ValueError("forbidden content detected")
    monkeypatch.setattr(agent_service, "check_output_forbidden_words", _raise)

    state = agent_service.SupervisorState(user_message="x", work_order="설비 #1\n\n[부품] 베어링")
    with pytest.raises(ValueError):
        agent_service.validate_work_order_node(state)

def test_validate_work_order_runs_before_approval_and_finalize():
    graph = agent_service.graph
    assert "validate_work_order" in graph.nodes
    # work_order -> validate_work_order 뒤에야 approval/finalize로 갈라진다
    assert "needs_approval_condition" in graph.branches["validate_work_order"]
    assert "needs_approval_condition" not in graph.branches.get("work_order", {})

