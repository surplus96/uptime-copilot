import pytest

import core.llm_provider as llm_provider


def test_get_client_uses_ollama_base_url(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

    client = llm_provider.get_client()

    assert "localhost:11434" in str(client.base_url)


def test_get_client_uses_openai_by_default(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    client = llm_provider.get_client()

    assert "api.openai.com" in str(client.base_url)


def test_get_model_defaults_per_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert llm_provider.get_model() == "qwen3:8b"

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert llm_provider.get_model() == "gpt-5.6-luna"


def test_filter_kwargs_strips_reasoning_effort_for_ollama(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    result = llm_provider.filter_kwargs(reasoning_effort="none", max_completion_tokens=150)

    assert "reasoning_effort" not in result
    assert result["max_completion_tokens"] == 150


def test_filter_kwargs_keeps_reasoning_effort_for_openai(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    result = llm_provider.filter_kwargs(reasoning_effort="none")

    assert result["reasoning_effort"] == "none"


def test_parse_with_retry_retries_once_then_raises(monkeypatch):
    calls = []

    class _FailingCompletions:
        def parse(self, **kwargs):
            calls.append(kwargs)
            raise ValueError("일시적 실패")

    fake_client = type("Client", (), {"chat": type("Chat", (), {"completions": _FailingCompletions()})()})()

    import pytest
    with pytest.raises(ValueError):
        llm_provider.parse_with_retry(fake_client, model="m", messages=[], response_format=object)

    assert len(calls) == 2  # 최초 1회 + 재시도 1회


def test_ollama_client_has_bounded_timeout_and_no_sdk_retries(monkeypatch):
    """2026-09-30 pipeline-optimizer(실행으로 확인): timeout 없이 SDK 기본(600초 읽기)에 SDK
    자체 재시도 2회가 parse_with_retry와 곱해져 호출당 HTTP 6회, 멈춘 Ollama 하나가 최대
    60분을 붙잡았다."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    client = llm_provider.get_client()
    assert client.timeout == llm_provider.OLLAMA_TIMEOUT_SECONDS == 300.0
    assert client.max_retries == 0


def test_judge_call_filters_provider_unsupported_params(monkeypatch):
    """harness의 judge 호출만 reasoning_effort를 필터 없이 보냈다 - Ollama가 거부하면 판정이
    두 번 실패한 뒤 fail-open해서 모든 /rag/query가 verified=False가 된다."""
    from core import harness

    seen = {}

    def make_client():
        class _Completions:
            def parse(self, **kwargs):
                seen.update(kwargs)
                raise RuntimeError("stop after capturing the kwargs")
        return type("C", (), {"chat": type("Ch", (), {"completions": _Completions()})()})()

    for provider, expected in (("ollama", False), ("openai", True)):
        seen.clear()
        monkeypatch.setenv("LLM_PROVIDER", provider)
        with pytest.raises(RuntimeError):
            harness._call_judge(make_client(), "프롬프트")
        assert ("reasoning_effort" in seen) is expected, provider


def test_parse_with_retry_does_not_retry_timeouts_but_retries_other_errors():
    """2026-09-30 code-quality-reviewer: 타임아웃(300초)을 재시도하면 호출 하나가 600초 워커와 단일 추론
    슬롯을 붙잡는다. 일반 오류(구조화 출력 실패 등)는 그대로 한 번 재시도한다."""
    import openai

    class _Timeout(openai.APITimeoutError):
        def __init__(self):
            Exception.__init__(self, "timed out")

    def make(error):
        calls = {"n": 0}

        class _C:
            def parse(self, **kw):
                calls["n"] += 1
                raise error

        return type("C", (), {"chat": type("Ch", (), {"completions": _C()})()})(), calls

    client, calls = make(_Timeout())
    with pytest.raises(openai.APITimeoutError):
        llm_provider.parse_with_retry(client, model="m", messages=[], response_format=object)
    assert calls["n"] == 1

    client, calls = make(ValueError("스키마 불일치"))
    with pytest.raises(ValueError):
        llm_provider.parse_with_retry(client, model="m", messages=[], response_format=object)
    assert calls["n"] == 2
