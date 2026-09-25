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
    def __init__(self, machine_id=1):
        self._machine_id = machine_id

    def parse(self, *, response_format, **kwargs):
        assert response_format is agent_service.RouteDecision
        return _FakeCompletion(_FakeMessage(
            parsed=agent_service.RouteDecision(category="오류_진단", machine_id=self._machine_id, reason="테스트")
        ))

    def create(self, **kwargs):
        return _FakeCompletion(_FakeMessage(content="테스트 관점 평가"))


class _FakeClient:
    def __init__(self, machine_id=1):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(machine_id)})()


def _patch_common(monkeypatch, severity, machine_id=1):
    # route_node가 분류와 함께 machine_id도 한 번에 뽑으므로(2026-09-25 호출 병합),
    # 이 가짜 클라이언트의 RouteDecision 응답에 machine_id를 실어서 diagnosis_node가
    # state.machine_id로 그대로 받게 한다 - 더 이상 별도 추출 함수를 몽키패치하지 않는다.
    monkeypatch.setattr(agent_service, "client", _FakeClient(machine_id))
    monkeypatch.setattr(agent_service, "_diagnose_machine", lambda mid, **kw: {
        "machine_id": mid,
        "diagnosis": f"테스트 {severity} 진단",
        "severity": severity,
        "involved_components": ["comp1"],
        "component_evidence": {"comp1": "테스트 근거"},
        "evidence_at": "2026-01-01T00:00:00",
    })
    agent_service.app = agent_service.graph.compile(checkpointer=InMemorySaver())

def test_diagnosis_node_asks_for_machine_id_when_missing():
    # machine_id는 이제 route_node가 미리 채워서 state로 넘겨준다(2026-09-25 호출 병합) -
    # diagnosis_node는 그 값을 그대로 볼 뿐이라, 채워지지 않은 기본값(None)으로 충분하다.
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
    monkeypatch.setattr(svc.pdm_operations, "get_component_failure_stats", lambda model: [])
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
    assert "error3_count_24h" in result["component_evidence"]["comp2"]  # 3-7: 주요 근거 포함


def test_diagnose_machine_falls_back_to_zscore_when_model_missing(monkeypatch):
    import agent.agent_service as svc

    monkeypatch.setattr(svc.pdm_operations, "get_machine_info", lambda mid: {"model": "model1", "age": 5})
    monkeypatch.setattr(svc.pdm_operations, "get_recent_errors", lambda mid, limit=3: [])
    monkeypatch.setattr(svc.pdm_operations, "check_recent_failure", lambda mid, within_days=30: None)
    monkeypatch.setattr(svc.pdm_operations, "get_component_failure_stats", lambda model: [])
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
    monkeypatch.setattr(svc.pdm_operations, "get_component_failure_stats", lambda model: [])
    monkeypatch.setattr(svc.pdm_telemetry, "detect_anomaly", lambda mid: {"has_anomaly": False})
    monkeypatch.setattr(svc, "_predict_risk_safe", lambda mid: {
        c: {"probability": 0.01, "top_features": []} for c in ("comp1", "comp2", "comp3", "comp4")
    })

    result = svc._diagnose_machine(1)

    assert result["severity"] == "긴급"


def _make_perspective_completions(risk_level, recommended_window):
    class _FakeParsedCompletions:
        def parse(self, *, response_format, **kwargs):
            assert response_format is agent_service.PerspectiveAssessment
            parsed = agent_service.PerspectiveAssessment(
                risk_level=risk_level, recommended_window=recommended_window, rationale="테스트 근거",
            )
            msg = type("M", (), {"parsed": parsed, "refusal": None})()
            return type("C", (), {"choices": [type("Ch", (), {"message": msg})()]})()
    return _FakeParsedCompletions()


def test_assess_perspective_returns_structured_fields(monkeypatch):
    fake_client = type("Client", (), {
        "chat": type("Chat", (), {"completions": _make_perspective_completions("높음", "즉시")})()
    })()
    monkeypatch.setattr(agent_service, "client", fake_client)

    result = agent_service._assess_perspective("안전", "테스트 프롬프트", "테스트 진단")

    assert result["perspectives"] == ["[안전] 테스트 근거"]
    assert result["perspective_assessments"][0]["risk_level"] == "높음"
    assert result["perspective_assessments"][0]["requires_shutdown"] is True


def test_assess_perspective_cannot_produce_contradictory_shutdown(monkeypatch):
    # 2026-09-25 로컬 모델(Ollama) 실측: risk_level="중간"인데 requires_shutdown=true인
    # 내적 모순 출력이 나온 적이 있다(docs/decisions.md). PerspectiveAssessment에서
    # requires_shutdown 필드 자체를 없애고 코드가 유도하므로, risk_level이 "중간"이면서
    # recommended_window도 "즉시"가 아니면 requires_shutdown은 항상 False여야 한다 -
    # LLM이 뭐라고 "말하고 싶어 하든" 애초에 그 필드를 설정할 방법이 없다.
    fake_client = type("Client", (), {
        "chat": type("Chat", (), {"completions": _make_perspective_completions("중간", "24시간 이내")})()
    })()
    monkeypatch.setattr(agent_service, "client", fake_client)

    result = agent_service._assess_perspective("정비", "테스트 프롬프트", "테스트 진단")

    assert result["perspective_assessments"][0]["risk_level"] == "중간"
    assert result["perspective_assessments"][0]["requires_shutdown"] is False


def test_assess_perspective_falls_back_on_failure(monkeypatch):
    class _FakeFailingCompletions:
        def parse(self, *, response_format, **kwargs):
            raise ValueError("모델 호출 실패")

    fake_client = type("Client", (), {"chat": type("Chat", (), {"completions": _FakeFailingCompletions()})()})()
    monkeypatch.setattr(agent_service, "client", fake_client)

    result = agent_service._assess_perspective("생산", "테스트 프롬프트", "테스트 진단")

    assert result["perspective_assessments"][0]["risk_level"] == "중간"
    assert result["perspective_assessments"][0]["requires_shutdown"] is False
    assert result["perspective_assessments"][0]["is_fallback"] is True  # CP-U2: 정직한 중간과 구분


def _assessment(label, risk_level="낮음", window="24시간 이내", shutdown=False, is_fallback=False):
    return {
        "label": label, "risk_level": risk_level, "recommended_window": window,
        "requires_shutdown": shutdown, "rationale": "테스트", "is_fallback": is_fallback,
    }


def test_priority_rule_safety_high_alone_forces_p1(monkeypatch):
    state = agent_service.SupervisorState(
        user_message="x",
        perspective_assessments=[
            _assessment("안전", risk_level="높음"),
            _assessment("생산"),
            _assessment("정비"),
        ],
    )
    result = agent_service.priority_rule_node(state)
    assert result["priority"] == "P1"
    assert result["recommend_shutdown"] is False
    assert any("안전" in r for r in result["priority_reasons"])


def test_priority_rule_no_signal_at_all_is_p3(monkeypatch):
    state = agent_service.SupervisorState(
        user_message="x",
        perspective_assessments=[_assessment("안전"), _assessment("생산"), _assessment("정비")],
        risk_probability=None,
    )
    result = agent_service.priority_rule_node(state)
    assert result["priority"] == "P3"
    assert result["recommend_shutdown"] is False


def test_priority_rule_model_alarm_alone_is_p1_without_shutdown(monkeypatch):
    state = agent_service.SupervisorState(
        user_message="x",
        perspective_assessments=[_assessment("안전"), _assessment("생산"), _assessment("정비")],
        risk_probability=0.9,
        risk_component="comp2",
    )
    result = agent_service.priority_rule_node(state)
    assert result["priority"] == "P1"
    assert result["recommend_shutdown"] is False  # 모델 단독 경보로는 정지 권고 안 냄
    assert any("모델" in r for r in result["priority_reasons"])


def test_priority_rule_all_perspectives_failed_floors_at_p2(monkeypatch):
    state = agent_service.SupervisorState(
        user_message="x",
        perspective_assessments=[
            _assessment("안전", is_fallback=True),
            _assessment("생산", is_fallback=True),
            _assessment("정비", is_fallback=True),
        ],
        risk_probability=None,
    )
    result = agent_service.priority_rule_node(state)
    assert result["priority"] == "P2"  # P3로 방치하지 않음 - CP-U2 지적
    assert any("판정 근거 부족" in r for r in result["priority_reasons"])


def test_priority_rule_shutdown_vote_sets_recommend_shutdown(monkeypatch):
    state = agent_service.SupervisorState(
        user_message="x",
        perspective_assessments=[
            _assessment("안전", shutdown=True),
            _assessment("생산"),
            _assessment("정비"),
        ],
    )
    result = agent_service.priority_rule_node(state)
    assert result["priority"] == "P1"
    assert result["recommend_shutdown"] is True


def test_priority_rule_wired_between_merge_and_work_order():
    graph = agent_service.graph
    assert "priority_rule" in graph.nodes


def test_work_order_includes_priority_block_when_set():
    state = agent_service.SupervisorState(
        user_message="x", machine_id=1, severity="긴급",
        involved_components=["comp1"],
        component_evidence={"comp1": "테스트 증상"},
        priority="P1", recommend_shutdown=True, priority_reasons=["안전 관점 정지 필요"],
    )
    result = agent_service.work_order_node(state)
    assert "[우선순위] P1" in result["work_order"]
    assert "[정지 권고] 예" in result["work_order"]
    assert "안전 관점 정지 필요" in result["work_order"]


def test_work_order_omits_priority_block_for_caution():
    state = agent_service.SupervisorState(
        user_message="x", machine_id=1, severity="주의",
        involved_components=["comp1"],
        component_evidence={"comp1": "테스트 증상"},
    )
    result = agent_service.work_order_node(state)
    assert "[우선순위]" not in result["work_order"]
