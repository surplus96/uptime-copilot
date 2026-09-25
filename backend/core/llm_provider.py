"""
LLM 제공자 어댑터: `LLM_PROVIDER` 환경변수 하나로 OpenAI <-> Ollama(OpenAI 호환)를
전환한다. Ollama도 OpenAI Python SDK로 그대로 붙을 수 있지만(base_url만 바꾸면 됨),
`reasoning_effort` 같은 OpenAI 전용 파라미터를 보내면 거부하는 모델이 있어 제공자별로
걸러낸다.
"""
import os

from openai import OpenAI


def get_api_key() -> str:
    if _provider() == "ollama":
        return "ollama"  # Ollama는 실제로 안 쓰지만 SDK가 빈 값을 거부해서 더미 값
    return os.getenv("OPENAI_API_KEY") or "sk-not-set"


def get_base_url() -> str | None:
    if _provider() == "ollama":
        return os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    return None  # None이면 OpenAI SDK가 자기 기본 엔드포인트를 씀

def _provider() -> str:
    return os.getenv("LLM_PROVIDER", "openai")


def get_provider_name() -> str:
    """로그·상태 표시용 - 지금 실제로 어느 제공자가 선택돼 있는지 (`"openai"` | `"ollama"`)."""
    return _provider()


def get_client() -> OpenAI:
    """제공자에 맞는 OpenAI SDK 클라이언트를 만든다. agent_service.py/main.py가
    module-level에서 한 번만 호출해서 재사용한다 - 매 요청마다 새로 만들지 않는다."""
    if _provider() == "ollama":
        return OpenAI(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            api_key="ollama",  # OpenAI SDK가 빈 문자열은 거부해서 더미 값을 넣는다 - Ollama는 실제로 안 씀
        )
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY") or "sk-not-set", timeout=30.0)


def get_model() -> str:
    if _provider() == "ollama":
        return os.getenv("OLLAMA_MODEL", "qwen3:8b")
    return os.getenv("OPENAI_MODEL", "gpt-5.6-luna")


# 제공자별로 API가 거부하는 파라미터 - 여기 추가하면 filter_kwargs()가 자동으로 걸러낸다.
_PROVIDER_UNSUPPORTED_PARAMS = {
    "ollama": {"reasoning_effort"},
}


def filter_kwargs(**kwargs) -> dict:
    """현재 제공자가 지원 안 하는 파라미터를 제거한다. 호출부는
    `**llm_provider.filter_kwargs(reasoning_effort="none", ...)`처럼 그대로 펼쳐 쓴다."""
    unsupported = _PROVIDER_UNSUPPORTED_PARAMS.get(_provider(), set())
    return {k: v for k, v in kwargs.items() if k not in unsupported}


def parse_with_retry(client, *, model: str, messages: list, response_format, **kwargs):
    """구조화 출력(`chat.completions.parse`)을 제공자별 파라미터 필터링과 함께 호출하고,
    실패하면 그대로 1회 재시도한다 - Ollama의 JSON 스키마 강제가 OpenAI보다 불안정할 수
    있어서, 네트워크/일시적 오류로 인한 실패를 흡수한다."""
    kwargs = filter_kwargs(**kwargs)
    try:
        return client.chat.completions.parse(model=model, messages=messages, response_format=response_format, **kwargs)
    except Exception:
        return client.chat.completions.parse(model=model, messages=messages, response_format=response_format, **kwargs)
