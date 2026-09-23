import pytest
from langgraph.checkpoint.memory import InMemorySaver

import agent.agent_service as agent_service


class _FakeMessage:
    def __init__(self, parsed=None, content=None):
        self.parsed = parsed
        self.content = content


class _FakeCompletion:
    def __init__(self, message):
        self.choices = [type("C", (), {"message": message})()]


class _FakeCompletions:
    def parse(self, *, response_format, **kwargs):
        assert response_format is agent_service.RouteDecision
        return _FakeCompletion(_FakeMessage(
            parsed=agent_service.RouteDecision(category="오류_진단", reason="테스트")
        ))

    def create(self, **kwargs):
        return _FakeCompletion(_FakeMessage(content="테스트 관점 평가"))


class _FakeClient:
    chat = type("Chat", (), {"completions": _FakeCompletions()})()


def _patch_common(monkeypatch, severity, machine_id=1):
    monkeypatch.setattr(agent_service, "client", _FakeClient())
    monkeypatch.setattr(agent_service, "_extract_machine_id", lambda msg: machine_id)
    monkeypatch.setattr(agent_service, "_diagnose_machine", lambda mid, **kw: {
        "machine_id": mid,
        "diagnosis": f"테스트 {severity} 진단",
        "severity": severity,
        "involved_components": ["comp1"],
        "component_evidence": {"comp1": "테스트 근거"},
        "evidence_at": "2026-01-01T00:00:00",
    })
    agent_service.app = agent_service.graph.compile(checkpointer=InMemorySaver())

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



def test_urgent_severity_routes_to_pending_approval(monkeypatch):
    _patch_common(monkeypatch, "긴급")
    result = agent_service.start_agent("1번 설비 이상해", "test-thread-urgent")
    assert result["status"] == "pending_approval"


def test_caution_severity_routes_to_finalized_work_order(monkeypatch):
    _patch_common(monkeypatch, "주의")
    result = agent_service.start_agent("1번 설비 이상해", "test-thread-caution")
    assert result["status"] == "done"
    assert result["work_order"] is not None


def test_normal_severity_routes_straight_to_end(monkeypatch):
    _patch_common(monkeypatch, "일반")
    result = agent_service.start_agent("1번 설비 이상해", "test-thread-normal")
    assert result["status"] == "done"
    assert "테스트 일반 진단" in result["result"]

def test_diagnose_machine_uses_risk_model_for_caution(monkeypatch):
    import agent.agent_service as svc

    monkeypatch.setattr(svc.pdm_operations, "get_machine_info", lambda mid: {"model": "model1", "age": 5})
    monkeypatch.setattr(svc.pdm_operations, "get_recent_errors", lambda mid, limit=3: [])
    monkeypatch.setattr(svc.pdm_operations, "check_recent_failure", lambda mid, within_days=30: None)
    monkeypatch.setattr(svc.pdm_telemetry, "detect_anomaly", lambda mid: {"has_anomaly": False})
    monkeypatch.setattr(svc.sim_query, "dataset_now", lambda: "2026-01-01T00:00:00")
    monkeypatch.setattr(svc, "_predict_risk_safe", lambda mid: {
        "comp2": {"probability": 0.87, "top_features": [{"feature": "error3_count_24h", "value": 3, "contribution": 0.5}]},
        "comp1": {"probability": 0.1, "top_features": []},
        "comp3": {"probability": 0.05, "top_features": []},
        "comp4": {"probability": 0.02, "top_features": []},
    })

    result = svc._diagnose_machine(1)

    assert result["severity"] == "주의"
    assert result["evidence_at"] is not None  # 추가: None이면 알림이 영원히 억제됨
    assert "comp2" in result["component_evidence"]
    assert "87%" in result["diagnosis"]



def test_diagnose_machine_falls_back_to_zscore_when_model_missing(monkeypatch):
    import agent.agent_service as svc

    monkeypatch.setattr(svc.pdm_operations, "get_machine_info", lambda mid: {"model": "model1", "age": 5})
    monkeypatch.setattr(svc.pdm_operations, "get_recent_errors", lambda mid, limit=3: [])
    monkeypatch.setattr(svc.pdm_operations, "check_recent_failure", lambda mid, within_days=30: None)
    monkeypatch.setattr(svc.pdm_telemetry, "detect_anomaly", lambda mid: {
        "has_anomaly": True, "flagged_signals": ["volt"], "as_of": "2026-01-01T00:00:00",
    })
    monkeypatch.setattr(svc, "_predict_risk_safe", lambda mid: None)

    result = svc._diagnose_machine(1)

    assert result["severity"] == "주의"


def test_diagnose_machine_real_failure_always_urgent_regardless_of_risk(monkeypatch):
    import agent.agent_service as svc

    monkeypatch.setattr(svc.pdm_operations, "get_machine_info", lambda mid: {"model": "model1", "age": 5})
    monkeypatch.setattr(svc.pdm_operations, "get_recent_errors", lambda mid, limit=3: [])
    monkeypatch.setattr(svc.pdm_operations, "check_recent_failure", lambda mid, within_days=30: {
        "datetime": "2026-01-01T00:00:00", "component": "comp1", "description": "테스트 고장",
    })
    monkeypatch.setattr(svc.pdm_telemetry, "detect_anomaly", lambda mid: {"has_anomaly": False})
    monkeypatch.setattr(svc, "_predict_risk_safe", lambda mid: {
        c: {"probability": 0.01, "top_features": []} for c in ("comp1", "comp2", "comp3", "comp4")
    })

    result = svc._diagnose_machine(1)

    assert result["severity"] == "긴급"
