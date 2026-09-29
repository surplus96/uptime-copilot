"""MRO-FR-07 CP-M2 교차 검토 후속: offline_guard.py의 환경변수 정규화·검증 로직
단위 테스트. 소켓/네트워크가 전혀 필요 없는 순수 로직이라 offline_e2e 마커 없이
CI 기본 경로에서 항상 돈다 - test_offline_guard.py(실제 소켓 차단 증명)와는
목적이 다르다."""
import pytest

from core import offline_guard


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on"])
def test_is_offline_accepts_truthy_variants(monkeypatch, value):
    monkeypatch.setenv("OFFLINE", value)
    assert offline_guard.is_offline() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_is_offline_rejects_falsy_variants(monkeypatch, value):
    monkeypatch.setenv("OFFLINE", value)
    assert offline_guard.is_offline() is False


def test_is_offline_false_when_unset(monkeypatch):
    monkeypatch.delenv("OFFLINE", raising=False)
    assert offline_guard.is_offline() is False


def test_refuse_unsafe_startup_combo_normalizes_provider_case(monkeypatch):
    """2026-09-29 CP-M2 교차 검토 지적 회귀: LLM_PROVIDER=Ollama(대문자)가 "openai"와
    정확히 안 맞아서 거부를 통과한 뒤 실제로는 llm_provider._provider()가 여전히
    "ollama"가 아닌 걸로 취급돼 실제 OpenAI API로 나가던 문제. 이제 정규화를 거쳐
    올바르게 ollama로 인식돼야 한다(거부되지 않아야 함 - 이게 정상 동작)."""
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "Ollama")
    offline_guard.refuse_unsafe_startup_combo()  # 예외 없이 통과해야 함

    from core import llm_provider
    assert llm_provider.get_provider_name() == "ollama"


def test_refuse_unsafe_startup_combo_blocks_openai_case_insensitively(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "OpenAI")
    with pytest.raises(RuntimeError, match="OFFLINE=1"):
        offline_guard.refuse_unsafe_startup_combo()


def test_validate_allowed_endpoints_blocks_remote_cmms_when_offline(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("CMMS_MCP_URL", "https://cmms.example.com/mcp")
    with pytest.raises(RuntimeError, match="CMMS_MCP_URL"):
        offline_guard.validate_allowed_endpoints()


def test_validate_allowed_endpoints_allows_docker_internal_cmms_when_offline(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("CMMS_MCP_URL", "http://host.docker.internal:3100/mcp")
    offline_guard.validate_allowed_endpoints()  # 예외 없이 통과해야 함


def test_validate_allowed_endpoints_noop_when_online(monkeypatch):
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.setenv("CMMS_MCP_URL", "https://cmms.example.com/mcp")
    offline_guard.validate_allowed_endpoints()  # 오프라인 아니면 검사 자체를 안 함


def test_enforce_offline_env_forces_hf_and_langchain_vars(monkeypatch):
    # enforce_offline_env()는 os.environ을 직접 쓴다(monkeypatch를 거치지 않음) -
    # monkeypatch.delenv()로 지워둔 상태에서 함수가 새로 값을 세팅하면, monkeypatch는
    # 그 세팅을 모르니 테스트가 끝나도 원복이 안 되고 다음 테스트로 새어나간다(직접
    # 겪은 테스트 격리 버그, 2026-09-29) - finally에서 반드시 직접 지운다.
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")  # .env가 이렇게 켜둔 상황을 흉내
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

    import os
    try:
        offline_guard.enforce_offline_env()
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
        assert os.environ["LANGCHAIN_TRACING_V2"] == "false"
    finally:
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)


def test_enforce_offline_env_noop_when_online(monkeypatch):
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

    offline_guard.enforce_offline_env()


# ---------- LangSmith 3개 no-op 가드 - 2026-09-29 CP-M2 교차 검토가 지목한 직접 테스트 없음 ----------

def test_get_langsmith_client_returns_none_when_offline(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    assert offline_guard.get_langsmith_client(anonymizer=None) is None


def test_get_langsmith_client_returns_real_client_when_online(monkeypatch):
    monkeypatch.delenv("OFFLINE", raising=False)
    client = offline_guard.get_langsmith_client(anonymizer=None)
    assert client is not None
    assert type(client).__name__ == "Client"


def test_wrap_openai_is_passthrough_when_offline(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    sentinel = object()
    assert offline_guard.wrap_openai(sentinel) is sentinel


def test_wrap_openai_actually_wraps_when_online(monkeypatch):
    monkeypatch.delenv("OFFLINE", raising=False)

    # langsmith.wrappers.wrap_openai()는 client.chat.completions뿐 아니라 최상위
    # client.completions(레거시 API)까지 실제 메서드 참조를 들여다보고 감싼다 - 손으로
    # 만든 가짜로는 정확한 모양을 맞추기 어려워서, 실제 호출은 안 하는 진짜 OpenAI SDK
    # 클라이언트(더미 URL·키)를 그대로 감싼다.
    from openai import OpenAI
    real_client = OpenAI(api_key="test-key", base_url="http://localhost:1")
    original_create = real_client.chat.completions.create

    result = offline_guard.wrap_openai(real_client)

    assert result is real_client  # wrap_openai는 같은 클라이언트 인스턴스를 제자리에서 감싼다
    assert result.chat.completions.create is not original_create  # 메서드가 실제로 교체됐어야 함


def test_traceable_is_noop_decorator_when_offline(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")

    @offline_guard.traceable(name="테스트")
    def fn(x):
        return x * 2

    assert fn(3) == 6  # 데코레이터가 아무 것도 안 바꾸고 원함수 그대로 동작해야 함


def test_traceable_wraps_when_online(monkeypatch):
    monkeypatch.delenv("OFFLINE", raising=False)

    @offline_guard.traceable(name="테스트")
    def fn(x):
        return x * 2

    assert fn(3) == 6  # 실제 langsmith.traceable로 감싸져도 반환값 자체는 그대로여야 함

    import os
    assert "HF_HUB_OFFLINE" not in os.environ
