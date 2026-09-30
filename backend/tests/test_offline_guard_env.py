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
    """2026-09-29 code-quality-reviewer 후속 지적: 이 테스트가 실제 langsmith.Client()를
    만들면서 api.smith.langchain.com으로 연결을 시도했다(로컬/CI 샌드박스가 이를
    차단하는 로그로 확인됨) - 이 테스트의 목적은 "오프라인이 아니면 no-op을 안 쓰고
    진짜 Client를 만든다"는 분기 자체를 확인하는 것이지 langsmith SDK의 네트워크
    동작을 검증하는 게 아니므로, langsmith.Client를 가짜로 바꿔서 생성자가 네트워크에
    닿을 일이 없게 한다."""
    import langsmith

    class _FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(langsmith, "Client", _FakeClient)
    monkeypatch.delenv("OFFLINE", raising=False)
    client = offline_guard.get_langsmith_client(anonymizer=None)
    assert isinstance(client, _FakeClient)


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


@pytest.mark.parametrize("url", [
    "http://evil.example\\@localhost/mcp",        # urllib은 localhost, WHATWG 클라이언트는 evil.example
    "http://localhost@evil.example/mcp",          # userinfo로 허용 호스트를 위장
    "http://localhost:3100/mcp\n",                # 제어문자
    "http://localhost:3100/ mcp",                 # 공백
    "file:///etc/passwd",                          # http/https 외 스킴
    "ftp://localhost/mcp",
])
def test_is_allowed_url_rejects_ambiguous_urls_even_with_allowlisted_host(url):
    """security-reviewer 인계(2026-09-29): 허용 목록 검사(urllib)와 실제 접속(다른
    파서)이 서로 다른 호스트를 볼 수 있는 URL은 호스트가 허용 목록에 있어 보여도
    불허해야 한다."""
    assert offline_guard.is_ambiguous_url(url) is True
    assert offline_guard.is_allowed_url(url) is False


@pytest.mark.parametrize("url", [
    "http://localhost:3100/mcp",
    "http://127.0.0.1:3100/mcp",
    "http://host.docker.internal:3100/mcp",
    "HTTP://LOCALHOST:3100/mcp",                   # 스킴·호스트는 대소문자 무관
])
def test_is_allowed_url_accepts_plain_allowlisted_urls(url):
    assert offline_guard.is_ambiguous_url(url) is False
    assert offline_guard.is_allowed_url(url) is True


def test_is_allowed_url_rejects_lookalike_and_empty():
    assert offline_guard.is_allowed_url("http://127.0.0.1.nip.io/mcp") is False
    assert offline_guard.is_allowed_url("http://0.0.0.0/mcp") is False
    assert offline_guard.is_allowed_url(None) is False
    assert offline_guard.is_allowed_url("") is False


def test_validate_allowed_endpoints_rejects_ambiguous_url_when_offline(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("CMMS_MCP_URL", "http://evil.example\\@localhost/mcp")
    with pytest.raises(RuntimeError, match="모호한 URL"):
        offline_guard.validate_allowed_endpoints()


@pytest.mark.parametrize("value", ["y", "enabled", "2", "onn", "disable"])
def test_validate_offline_flag_rejects_unknown_values(monkeypatch, value):
    """2026-09-30 security-reviewer: OFFLINE=y 같은 오타는 조용히 온라인으로 동작했다(실제로
    import 시점에 LangSmith 접속이 시도됨) - 모르는 값이면 기동 초기에 크게 실패해야 한다."""
    monkeypatch.setenv("OFFLINE", value)
    with pytest.raises(RuntimeError, match="알 수 없는 값"):
        offline_guard.validate_offline_flag()
    with pytest.raises(RuntimeError, match="알 수 없는 값"):
        offline_guard.enforce_offline_env()  # main.py가 제일 먼저 부르는 진입점에서도 거부


@pytest.mark.parametrize("value", ["1", "true", "YES", "on", "0", "false", "no", "off", ""])
def test_validate_offline_flag_accepts_known_values(monkeypatch, value):
    monkeypatch.setenv("OFFLINE", value)
    offline_guard.validate_offline_flag()


def test_offline_refuses_proxy_env_that_would_route_allowlisted_hosts(monkeypatch):
    """프록시 변수가 있으면 httpx가 localhost/host.docker.internal로 가는 요청도 프록시로
    보낸다(프롬프트와 CMMS 토큰이 프록시로 나감) - 허용 목록 호스트가 NO_PROXY로 제외되지
    않았다면 기동을 거부한다."""
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.example:3128")
    with pytest.raises(RuntimeError, match="프록시"):
        offline_guard.validate_allowed_endpoints()

    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")  # host.docker.internal이 빠짐
    with pytest.raises(RuntimeError, match="host.docker.internal"):
        offline_guard.validate_allowed_endpoints()


def test_offline_allows_proxy_env_when_allowlisted_hosts_are_excluded(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.example:3128")
    monkeypatch.setenv("NO_PROXY", "localhost, 127.0.0.1 ,host.docker.internal")
    offline_guard.validate_allowed_endpoints()
    monkeypatch.setenv("NO_PROXY", "*")
    offline_guard.validate_allowed_endpoints()


def test_proxy_env_is_ignored_when_online(monkeypatch):
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.example:3128")
    offline_guard.validate_allowed_endpoints()


@pytest.mark.parametrize("url", ["http://[localhost]/mcp", "http://localhost:3100:80/mcp", "http://localhost/\x7f"])
def test_urls_the_parser_cannot_read_are_treated_as_ambiguous(url):
    """urllib이 예외를 던지거나 클라이언트마다 다르게 읽을 수 있는 URL은 fail-closed."""
    assert offline_guard.is_ambiguous_url(url) is True
    assert offline_guard.is_allowed_url(url) is False


def test_proxy_guard_agrees_with_urllib_when_lowercase_no_proxy_is_set_but_empty(monkeypatch):
    """2026-09-30 code-quality-reviewer(실행으로 확인): 대문자 NO_PROXY에 허용 호스트를 전부 넣어도
    소문자 no_proxy가 *빈 값*이면 urllib(httpx가 쓰는 원천)은 프록시를 거친다고 판단한다 - 예전 검사는
    `NO_PROXY or no_proxy`를 직접 읽어서 이 경우를 통과시켰다."""
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp.example:3128")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,host.docker.internal")
    monkeypatch.setenv("no_proxy", "")
    with pytest.raises(RuntimeError, match="프록시"):
        offline_guard.validate_allowed_endpoints()


def test_proxy_guard_recognises_mixed_case_proxy_variable_names(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("Http_Proxy", "http://proxy.corp.example:3128")  # 혼합 대소문자 - 예전 목록은 놓쳤다
    with pytest.raises(RuntimeError, match="프록시"):
        offline_guard.validate_allowed_endpoints()


def test_error_type_name_handles_module_paths_and_non_ascii_but_not_data_like_tokens():
    from agent.agent_service import _error_type_name

    assert _error_type_name("openai.APITimeoutError('Request timed out.')") == "openai.APITimeoutError"
    assert _error_type_name("설비오류('상세')") == "설비오류"
    assert _error_type_name("1bad('x')") == "Error"            # 식별자가 아님(숫자로 시작) - 일반 표기로
    assert _error_type_name("a b.c('x')") == "Error"           # 공백이 섞인 토큰
    assert _error_type_name("no parentheses here") == "Error"
    # 정규식은 통과하지만 점으로 나눈 부분이 식별자가 아닌 경우 - isidentifier 검증이 잡아야 한다
    assert _error_type_name("pkg..Err('x')") == "Error"          # 빈 부분
    assert _error_type_name("pkg.1Err('x')") == "Error"          # 숫자로 시작하는 부분


def test_offline_rejects_a_remote_ollama_base_url(monkeypatch):
    """가장 비싼 공백(2026-09-30 test-engineer, OG27): OLLAMA_BASE_URL 검사를 통째로 지워도 모든 테스트가
    통과했다 - 프롬프트가 외부 호스트로 나가도 아무것도 실패하지 않는다."""
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.example.com:11434/v1")
    with pytest.raises(RuntimeError, match="OLLAMA_BASE_URL"):
        offline_guard.validate_allowed_endpoints()
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434/v1")
    offline_guard.validate_allowed_endpoints()  # 허용 목록 안이면 통과


def test_offline_refuses_all_proxy_alone(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("ALL_PROXY", "socks5://proxy.corp.example:1080")
    with pytest.raises(RuntimeError, match="프록시"):
        offline_guard.validate_allowed_endpoints()


@pytest.mark.parametrize("url", ["http://localhost\\evil.example/", "http://localhost/\x01", "http://localhost/\x1f"])
def test_backslash_and_non_space_control_characters_are_ambiguous(url):
    """백슬래시 검사와 공백이 아닌 제어문자(< 0x20) 검사를 각각 따로 못박는다 - 예전 테스트는 URL이
    `@`도 함께 담고 있거나 공백류만 써서 각 절을 지워도 통과했다."""
    assert offline_guard.is_ambiguous_url(url) is True
