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
