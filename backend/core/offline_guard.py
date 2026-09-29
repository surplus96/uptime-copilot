"""오프라인 모드 가드 - §6-4(mro-copilot-upgrade-plan.md). 모듈이 아니라 목적지
기준: 이 상수 안에 있는 호스트는 오프라인에서도 항상 허용된다(로컬 호스트는
"외부"가 아니므로). pytest-socket 테스트도 이 상수를 그대로 재사용한다 - 허용
목록의 유일한 정의처를 하나로 유지하기 위함.

2026-09-29 CP-M2 교차 검토 지적 반영: 이전 버전은 LangSmith 3개 지점만 no-op으로
바꿨을 뿐, "허용 목록 밖은 실제로 막는다"는 원안의 핵심을 런타임에 강제하지
않았다 - 테스트에서만 검증되고 실제 기동 경로에는 적용 안 됨(CMMS_MCP_URL을
외부 주소로 잘못 설정해도 그대로 나감), HF_HUB_OFFLINE도 세팅 안 해서 임베딩
모델 로딩이 매번 huggingface.co에 접속함. 이 파일이 실제 강제 지점이 되도록
`enforce_offline_env()`/`validate_allowed_endpoints()`를 추가했다."""
import os
from urllib.parse import urlparse

OFFLINE_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "host.docker.internal"}


def is_offline() -> bool:
    # "1"만 인식하던 걸 완화 - true/yes/on도 같은 의미로 받아들인다(대소문자 무관).
    # 값 자체가 없거나 "0"/"false" 등이면 여전히 온라인으로 취급한다.
    return os.getenv("OFFLINE", "").strip().lower() in ("1", "true", "yes", "on")


def _hostname(url: str | None) -> str | None:
    if not url:
        return None
    return urlparse(url).hostname


def enforce_offline_env() -> None:
    """OFFLINE이면 이 프로세스가 앞으로 만들 모든 서브시스템(HF, LangChain 자동
    트레이싱)이 스스로 온라인 시도를 안 하도록 환경변수 자체를 덮어쓴다. main.py가
    다른 어떤 것도 import하기 전에 제일 먼저 호출해야 한다 - HuggingFace/LangChain은
    import 시점에 이미 이 값을 읽어가는 경우가 있어서, 늦게 부르면 늦다."""
    if not is_offline():
        return
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    # .env가 LANGCHAIN_TRACING_V2=true를 남겨둔 상태라도, 오프라인에서는 LangChain의
    # 전역 자동 트레이싱이 우리 wrap_openai/traceable no-op을 거치지 않고 자기
    # 방식대로 API를 호출할 수 있다 - 값 자체를 여기서 강제로 끈다.
    os.environ["LANGCHAIN_TRACING_V2"] = "false"


def validate_allowed_endpoints() -> None:
    """오프라인인데 OLLAMA_BASE_URL/CMMS_MCP_URL이 허용 목록 밖 호스트를 가리키면
    기동을 거부한다. 허용 목록은 "코드가 알아서 안전하게 처리하겠다"는 뜻이 아니라
    "이 호스트들만 진짜로 안전하다고 검증됐다"는 뜻이라, 밖이면 조용히 봐주지 않고
    바로 실패시킨다."""
    if not is_offline():
        return
    checks = {
        "OLLAMA_BASE_URL": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        "CMMS_MCP_URL": os.getenv("CMMS_MCP_URL"),
    }
    for var, url in checks.items():
        host = _hostname(url)
        if host is not None and host not in OFFLINE_ALLOWED_HOSTS:
            raise RuntimeError(
                f"OFFLINE=1인데 {var}={url}의 호스트({host})가 허용 목록"
                f"({sorted(OFFLINE_ALLOWED_HOSTS)}) 밖입니다 - 폐쇄망 모드에서는 "
                f"이 주소로 나갈 수 없습니다."
            )


def refuse_unsafe_startup_combo() -> None:
    """OFFLINE이면 LLM_PROVIDER가 openai(정규화된 값 기준 - llm_provider._provider()와
    동일 로직 재사용)일 때 기동 자체를 거부하고, 허용 목록 밖 엔드포인트도 함께
    검사한다. 폐쇄망 데모 중 조용히 외부로 나가는 것보다 시작 시점에 크게 실패하는
    쪽을 택한다(계획서 §6-4)."""
    from core import llm_provider

    if is_offline() and llm_provider.get_provider_name() == "openai":
        raise RuntimeError(
            "OFFLINE=1인데 LLM_PROVIDER=openai입니다 - 폐쇄망 모드에서는 cloud LLM을 "
            "호출할 수 없습니다. LLM_PROVIDER=ollama로 설정하세요."
        )
    validate_allowed_endpoints()


def get_langsmith_client(anonymizer):
    """오프라인이면 실제 langsmith.Client()를 아예 만들지 않고 None을 준다 - 호출부
    (rag_service.py 등)가 이미 `if langsmith_client is not None`으로 트레이싱을
    선택적으로 켜는 구조라, None을 주는 것만으로 트레이싱 경로 전체가 자연히 꺼진다."""
    if is_offline():
        return None
    from langsmith import Client
    return Client(anonymizer=anonymizer)


def wrap_openai(client, **kwargs):
    """오프라인이면 감싸지 않고 원래 client를 그대로 돌려준다(no-op)."""
    if is_offline():
        return client
    from langsmith.wrappers import wrap_openai as _wrap_openai
    return _wrap_openai(client, **kwargs)


def traceable(*dargs, **dkwargs):
    """오프라인이면 아무 것도 안 하는 데코레이터. main.py의 @traceable(...) 자리를
    그대로 대체한다."""
    if is_offline():
        def _noop_decorator(fn):
            return fn
        return _noop_decorator
    from langsmith import traceable as _traceable
    return _traceable(*dargs, **dkwargs)
