"""오프라인 모드 가드 - §6-4(mro-copilot-upgrade-plan.md). 모듈이 아니라 목적지
기준: 이 상수 안에 있는 호스트는 오프라인에서도 항상 허용된다(로컬 호스트는
"외부"가 아니므로). pytest-socket 테스트도 이 상수를 그대로 재사용한다 - 허용
목록의 유일한 정의처를 하나로 유지하기 위함."""
import os

OFFLINE_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "host.docker.internal"}


def is_offline() -> bool:
    return os.getenv("OFFLINE") == "1"


def refuse_unsafe_startup_combo() -> None:
    """OFFLINE=1인데 LLM_PROVIDER=openai면 기동 자체를 거부한다 - 폐쇄망 데모 중
    조용히 외부로 나가는 것보다 시작 시점에 크게 실패하는 쪽을 택한다(계획서 §6-4)."""
    if is_offline() and os.getenv("LLM_PROVIDER", "openai") == "openai":
        raise RuntimeError(
            "OFFLINE=1인데 LLM_PROVIDER=openai입니다 - 폐쇄망 모드에서는 cloud LLM을 "
            "호출할 수 없습니다. LLM_PROVIDER=ollama로 설정하세요."
        )


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
