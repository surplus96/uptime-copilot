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
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")  # .env가 이렇게 켜둔 상황을 흉내
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

    offline_guard.enforce_offline_env()

    import os
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_enforce_offline_env_noop_when_online(monkeypatch):
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

    offline_guard.enforce_offline_env()

    import os
    assert "HF_HUB_OFFLINE" not in os.environ
