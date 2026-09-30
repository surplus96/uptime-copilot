"""MRO-FR-07: OFFLINE=1에서 OFFLINE_ALLOWED_HOSTS(offline_guard.py) 밖으로 나가는
소켓 연결이 실제로 0회임을 pytest-socket으로 증명한다.

§6-4가 지적한 함정: 기존 테스트들처럼 notify/cmms_client를 몽키패치해두면 애초에
소켓을 열 코드가 없어 "항상 통과하는" 가짜 증명이 된다. 여기서는 LLM 호출만 가짜로
두고(비결정성·비용 때문에), notify.send_alert()/cmms_client.push_work_order()/
전체 LangGraph 시나리오는 실제 코드 경로를 그대로 태운다 - 가드를 깜빡한 새 호출
지점이 생겨도 이 테스트가 실제로 잡아내야 의미가 있다.

2026-09-29 CP-M2 교차 검토 지적: "CI엔 HF 캐시가 없어서 제외한다"는 원래 사유는
틀렸다 - 이 파일은 임베딩 모델을 전혀 안 건드린다(LLM은 전부 가짜, RAG 경로는
테스트 대상이 아님). 그래서 offline_e2e 마커는 남겨두되(선택 실행용) CI 기본
경로에 포함시켰다. host.docker.internal이 CI 러너(순수 우분투, Docker 아님)에서
해석 안 되는 건 맞지만, `pytest_socket.resolve_hostnames()`가 `socket.gaierror`를
관용적으로 처리(빈 집합 반환, 예외 안 냄)하고, 실제 연결 시도도 빠르게 실패할 뿐
멈추지 않는다는 걸 직접 확인했다 - 아래 테스트들의 assert는 "연결이 우리 가드에
막혔는지"만 보므로, DNS 해석 실패로 인한 다른 예외도 "안 막혔다"는 같은 결론을
낸다."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pytest_socket import SocketConnectBlockedError

import agent.agent_service as agent_service
import cmms_client
import notify
from core import offline_guard

# 2026-09-28 직접 겪은 함정: disable_socket()과 socket_allow_hosts()는 같이 쓰는
# 게 아니라 서로 다른 매커니즘이다. disable_socket()은 socket.socket 생성 자체를
# 예외 없이 전부 막아버리고(getaddrinfo도 무조건 막음), socket_allow_hosts()는
# connect() 호출 시점의 목적지만 허용 목록과 비교한다 - 같이 쓰면 disable_socket()이
# 먼저 소켓 생성을 막아서 socket_allow_hosts()의 허용 로직까지 도달하지도 못한다.
# pytest-socket이 실제로 의도한 조합 방식은 `allow_hosts` 마커 하나뿐 - 내부적으로
# socket_allow_hosts()만 호출하고 disable_socket()은 아예 안 건드린다.
pytestmark = [
    pytest.mark.offline_e2e,
    pytest.mark.allow_hosts(list(offline_guard.OFFLINE_ALLOWED_HOSTS)),
]


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch):
    # LLM_PROVIDER=openai + OFFLINE=1 조합은 기동 자체를 거부하는 별도 대상이라
    # (main.py, refuse_unsafe_startup_combo) 여기서는 ollama로 둔다 - 실제 LLM
    # 호출은 어차피 아래에서 전부 가짜로 대체하므로 provider 값 자체는 테스트
    # 로직에 영향이 없다.
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")


# ---------- 1. notify.py: Slack은 허용 목록 밖 - 오프라인이면 아예 소켓을 안 연다 ----------

def test_send_alert_does_not_open_socket_when_offline(monkeypatch):
    """notify.send_alert()를 몽키패치 없이 실제로 호출한다 - 오프라인 가드가 없다면
    hooks.slack.com으로 나가려는 소켓 연결 자체가 SocketBlockedError로 막혀야
    맞는데, 그건 "막혔다"를 증명할 뿐 "가드가 먼저 스스로 스킵했다"는 증명이 아니다.
    여기서는 예외 없이 조용히 반환되는 것까지 확인해서, 가드가 소켓을 열기도
    전에 되돌아갔음을 보인다."""
    monkeypatch.setattr(notify, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/SECRET/PATH/HERE")
    notify.send_alert("오프라인 테스트")  # 예외도, SocketBlockedError도 없이 그냥 반환돼야 함


# ---------- 2. cmms_client.py: CMMS(host.docker.internal)는 허용 목록 안 ----------

def test_cmms_host_is_reachable_through_the_allowlist_when_offline(monkeypatch):
    """CMMS는 Slack과 다르다 - §6-4 설계상 오프라인이어도 차단 대상이 아니다(이
    배포에서 host.docker.internal은 "외부"가 아니므로). 실제로 열려 있는 CMMS가
    없어도 되고, 이 테스트가 확인할 건 "연결 시도 자체가 허용 목록을 통과하는가"
    뿐이다 - 그래서 아무도 안 듣는 포트로 붙여서, 실패하더라도
    SocketConnectBlockedError(가드가 막음)가 아니라 연결 거부(가드는 통과, 상대가
    없어서 실패)여야 한다는 걸 확인한다.

    2026-09-30 정확히 무엇을 증명하는가(build-doctor/docs-reviewer/security-reviewer가 "CI에서는
    공허하다"고 지적해서 변이로 직접 확인했다):
    - 증명함: OFFLINE_ALLOWED_HOSTS에서 host.docker.internal을 빼면 이 테스트가 실패한다 -
      런타임 가드(cmms_client.push_work_order)가 예외 없이 "blocked_offline"을 반환하고, 그러면
      아래 pytest.raises가 걸린다. 이 검증은 DNS와 무관해서 CI에서도 유효하다.
    - 증명 못 함: 소켓 층. CI 러너는 순수 우분투라 host.docker.internal이 DNS 단계에서 실패해
      connect()까지 가지 못하므로, 아래 SocketConnectBlockedError 검사는 CI에서 도달하지 않는다
      (Docker 안에서 돌려야 실제로 검사됨). 또 pytest-socket은 connect()만 보므로 프록시·DNS·
      리다이렉트 경로는 이 테스트로 증명되지 않는다."""
    monkeypatch.setattr(cmms_client, "CMMS_MCP_URL", "http://host.docker.internal:19999/mcp")
    monkeypatch.setattr(cmms_client, "CMMS_MCP_TOKEN", "dummy-token")

    with pytest.raises(Exception) as exc_info:
        cmms_client.push_work_order(84, "테스트 작업지시서")

    assert not isinstance(exc_info.value, SocketConnectBlockedError), (
        f"CMMS(host.docker.internal)가 오프라인 허용 목록에서 막혔다 - "
        f"실제 예외: {type(exc_info.value).__name__}: {exc_info.value}"
    )


# ---------- 3. 전체 시나리오: 진단 -> 작업지시서 -> 승인 -> CMMS/Slack(설정 안 됨 -> 자체 no-op) ----------

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
            parsed=agent_service.RouteDecision(category="오류_진단", machine_id=1, reason="오프라인 테스트")
        ))


class _FakeClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _FakeCompletions()})()


def test_full_urgent_scenario_makes_no_disallowed_socket_call(monkeypatch):
    """진단(긴급) -> 작업지시서 -> 승인 -> finalize(Slack 알림 + CMMS push 시도)까지
    전체 그래프를 실제로 돌린다. LLM 클라이언트만 가짜로 두고(route + 3관점 평가 -
    3관점은 RouteDecision이 아닌 다른 response_format이라 _FakeCompletions.parse의
    assert에 걸려 AssertionError가 나는데, _assess_perspective가 이미 모든
    예외를 안전한 기본값으로 흡수하는 구조라 그래프가 끊기지 않는다 - 기존
    test_urgent_severity_routes_to_pending_approval과 같은 전제), notify/
    cmms_client/parts_check(sqlite, 소켓 무관)는 전부 실제 코드를 그대로 태운다.
    SLACK_WEBHOOK_URL/CMMS_MCP_URL을 이 테스트에서 따로 설정하지 않으므로(기본
    개발 환경과 동일하게 미설정) 둘 다 자체 가드로 조용히 스킵된다 - "설정 안 된
    통합은 소켓조차 안 연다"는 걸 그래프 전체 경로로 다시 확인하는 것."""
    monkeypatch.setattr(agent_service, "client", _FakeClient())
    monkeypatch.setattr(agent_service, "_diagnose_machine", lambda mid, **kw: {
        "machine_id": mid,
        "diagnosis": "오프라인 테스트 긴급 진단",
        "severity": "긴급",
        "involved_components": ["comp1"],
        "component_evidence": {"comp1": "오프라인 테스트 근거"},
        "evidence_at": "2026-01-01T00:00:00",
    })
    agent_service.app = agent_service.graph.compile(checkpointer=InMemorySaver())

    started = agent_service.start_agent("1번 설비 이상해", "offline-test-thread")
    assert started["status"] == "pending_approval"

    finished = agent_service.resume_agent("offline-test-thread", approved=True)
    assert finished["status"] == "done"
    assert "승인 처리되었습니다" in finished["result"]
