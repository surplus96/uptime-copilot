"""
LLM 제공자 어댑터: `LLM_PROVIDER` 환경변수 하나로 OpenAI <-> Ollama(OpenAI 호환)를
전환한다. Ollama도 OpenAI Python SDK로 그대로 붙을 수 있지만(base_url만 바꾸면 됨),
`reasoning_effort` 같은 OpenAI 전용 파라미터를 보내면 거부하는 모델이 있어 제공자별로
걸러낸다.
"""
import os

from openai import APITimeoutError, OpenAI


def get_api_key() -> str:
    if _provider() == "ollama":
        return "ollama"  # Ollama는 실제로 안 쓰지만 SDK가 빈 값을 거부해서 더미 값
    return os.getenv("OPENAI_API_KEY") or "sk-not-set"


def get_base_url() -> str | None:
    if _provider() == "ollama":
        return os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    return None  # None이면 OpenAI SDK가 자기 기본 엔드포인트를 씀

_KNOWN_PROVIDERS = {"openai", "ollama"}


def _provider() -> str:
    # 2026-09-29 CP-M2 교차 검토 지적: 대소문자만 정규화했더니 "Ollama"는 고쳐졌지만
    # "olama"/"ollma" 같은 다른 오타는 여전히 "openai도 ollama도 아닌 값"이 되어
    # 아무 데서도 안 걸리고 조용히 openai 분기로 빠졌다(code-quality-reviewer가
    # 실제 실행으로 재현: OFFLINE=1 LLM_PROVIDER=olama에서도 기동 거부가 안 되고
    # 실제 OpenAI API로 나감). 화이트리스트 방식으로 바꿔서, 아는 값이 아니면
    # 아예 기동 초기(이 함수의 첫 호출 시점)에 실패하게 한다 - "모르는 값이면
    # 안전한 쪽으로 추측"이 아니라 "모르는 값이면 즉시 크게 실패"를 택한다.
    value = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    if value not in _KNOWN_PROVIDERS:
        raise RuntimeError(
            f"LLM_PROVIDER={value!r}는 알 수 없는 값입니다 - {sorted(_KNOWN_PROVIDERS)} 중 하나여야 합니다."
        )
    return value


def get_provider_name() -> str:
    """로그·상태 표시용 - 지금 실제로 어느 제공자가 선택돼 있는지 (`"openai"` | `"ollama"`)."""
    return _provider()


OLLAMA_TIMEOUT_SECONDS = 300.0


def get_client() -> OpenAI:
    """제공자에 맞는 OpenAI SDK 클라이언트를 만든다. agent_service.py/main.py가
    module-level에서 한 번만 호출해서 재사용한다 - 매 요청마다 새로 만들지 않는다."""
    if _provider() == "ollama":
        # 2026-09-30 pipeline-optimizer 지적(실행으로 확인): timeout을 안 주면 SDK 기본
        # 읽기 타임아웃 600초가 적용되고, SDK 자체 재시도(max_retries=2)에 parse_with_retry의
        # 재시도가 곱해져 5xx/타임아웃에서 호출당 HTTP 6회까지 쌓였다 - 멈춘 Ollama 하나가
        # 워커와 단일 추론 슬롯을 호출당 최대 60분 붙잡을 수 있다. 문서화된 가장 긴 단일
        # 호출(관점 3개 직렬화 시 ~60초)보다 넉넉한 300초로 상한을 두고, 재시도는
        # parse_with_retry 한 겹으로만 둔다(최대 2회).
        return OpenAI(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            api_key="ollama",  # OpenAI SDK가 빈 문자열은 거부해서 더미 값을 넣는다 - Ollama는 실제로 안 씀
            timeout=OLLAMA_TIMEOUT_SECONDS,
            max_retries=0,
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
    except APITimeoutError:
        # 타임아웃은 다시 시도하지 않는다: Ollama 클라이언트의 timeout(300초)이 재시도와 곱해져 호출
        # 하나가 최대 600초 워커와 단일 추론 슬롯을 붙잡는다(2026-09-30 code-quality-reviewer).
        # 멈춘 서버에 같은 요청을 한 번 더 보내도 나아지지 않는다.
        raise
    except Exception:
        return client.chat.completions.parse(model=model, messages=messages, response_format=response_format, **kwargs)
