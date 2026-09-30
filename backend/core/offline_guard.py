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
from urllib import request as urllib_request
from urllib.parse import urlparse

OFFLINE_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "host.docker.internal"}


_OFFLINE_TRUE = ("1", "true", "yes", "on")
_OFFLINE_FALSE = ("", "0", "false", "no", "off")


def is_offline() -> bool:
    # "1"만 인식하던 걸 완화 - true/yes/on도 같은 의미로 받아들인다(대소문자 무관).
    # 값 자체가 없거나 "0"/"false" 등이면 온라인으로 취급한다. 그 밖의 값은 여기서 예외를
    # 던지지 않는다(알림 전송마다 호출되는 함수라 런타임에 터지면 안 된다) - 대신 기동
    # 시점에 validate_offline_flag()가 거부한다.
    return os.getenv("OFFLINE", "").strip().lower() in _OFFLINE_TRUE


def validate_offline_flag() -> None:
    """알 수 없는 OFFLINE 값(y, enabled, 2 ...)은 is_offline()에서 조용히 온라인으로
    취급돼서, 운영자가 오프라인이라고 믿는 채로 LangSmith 업로드·HF 접속·Slack 전송이
    그대로 일어났다 - 실제로 OFFLINE=y에서 import 시점에 LangSmith 접속이 시도되는 걸
    소켓을 막고 재현했다(2026-09-30 security-reviewer). LLM_PROVIDER처럼 모르는 값이면
    "안전한 쪽으로 추측"하지 않고 기동 초기에 크게 실패시킨다."""
    value = os.getenv("OFFLINE", "").strip().lower()
    if value not in _OFFLINE_TRUE and value not in _OFFLINE_FALSE:
        raise RuntimeError(
            f"OFFLINE={value!r}는 알 수 없는 값입니다 - 켜려면 {list(_OFFLINE_TRUE)}, "
            f"끄려면 {list(_OFFLINE_FALSE)[1:]} 또는 비워두세요. 오타로 조용히 온라인이 되는 걸 막기 위해 거부합니다."
        )


def _hostname(url: str | None) -> str | None:
    if not url:
        return None
    return urlparse(url).hostname


def is_ambiguous_url(url: str) -> bool:
    """파서마다 호스트를 다르게 읽을 수 있는 URL인지(security-reviewer 인계, 2026-09-29).
    예: `http://evil.example\\@localhost/`를 urllib은 localhost로 읽지만 WHATWG 규칙을
    따르는 HTTP 클라이언트는 `\\`를 `/`로 취급해 evil.example로 접속한다 - 허용 목록
    검사(urllib)와 실제 접속(httpx)이 서로 다른 호스트를 보게 된다. 서버 설정값에
    userinfo·백슬래시·공백·제어문자가 들어갈 정당한 이유가 없으므로 전부 거부한다."""
    if "\\" in url or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return True
    try:
        parsed = urlparse(url)
        parsed.port  # noqa: B018 - 잘못된 포트("localhost:3100:80")는 여기서 ValueError
    except ValueError:
        # `http://[localhost]/`처럼 urllib 자체가 못 읽는 URL - 클라이언트마다 다르게 읽을 수
        # 있으므로 fail-closed(2026-09-30 security-reviewer 실행 확인).
        return True
    return "@" in parsed.netloc or parsed.scheme.lower() not in ("http", "https")


def is_allowed_url(url: str | None) -> bool:
    """허용 목록 판정의 유일한 진입점 - 모호한 URL은 호스트와 무관하게 불허."""
    if not url or is_ambiguous_url(url):
        return False
    return urlparse(url).hostname in OFFLINE_ALLOWED_HOSTS


def enforce_offline_env() -> None:
    """OFFLINE이면 이 프로세스가 앞으로 만들 모든 서브시스템(HF, LangChain 자동
    트레이싱)이 스스로 온라인 시도를 안 하도록 환경변수 자체를 덮어쓴다. main.py가
    다른 어떤 것도 import하기 전에 제일 먼저 호출해야 한다 - HuggingFace/LangChain은
    import 시점에 이미 이 값을 읽어가는 경우가 있어서, 늦게 부르면 늦다."""
    validate_offline_flag()
    if not is_offline():
        return
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    # .env가 LANGCHAIN_TRACING_V2=true를 남겨둔 상태라도, 오프라인에서는 LangChain의
    # 전역 자동 트레이싱이 우리 wrap_openai/traceable no-op을 거치지 않고 자기
    # 방식대로 API를 호출할 수 있다 - 값 자체를 여기서 강제로 끈다.
    # 2026-09-29 code-quality-reviewer 지적(M3, 실행으로 확인): langsmith SDK의
    # get_env_var()는 LANGSMITH_* 접두사를 LANGCHAIN_*보다 먼저 본다 - LANGCHAIN_
    # 값만 껐을 때 LANGSMITH_TRACING_V2가 세팅돼 있으면 tracing_is_enabled()가
    # 계속 True였다(직접 재현). 둘 다 끈다.
    for var in ("LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "LANGSMITH_TRACING_V2", "LANGSMITH_TRACING"):
        os.environ[var] = "false"


def validate_no_proxy_bypass() -> None:
    """프록시 환경변수가 있으면 httpx/httpx2가 허용 목록 호스트(localhost 등)로 가는 요청도
    프록시로 보낸다 - 프롬프트와 CMMS 베어러 토큰이 프록시로 나가고, 로컬 프록시는 소켓
    허용 목록 검사도 통과해서 CI 증명이 이 환경에서는 공허해진다(2026-09-30
    security-reviewer가 두 클라이언트 모두 실행으로 확인).

    판정은 직접 구현하지 않고 urllib의 함수를 쓴다(httpx가 같은 원천을 쓴다). 처음에는
    `NO_PROXY or no_proxy`를 직접 읽었는데, 대문자 NO_PROXY에 허용 호스트를 다 넣고 소문자
    no_proxy를 *빈 값*으로 두면 urllib은 프록시를 거친다고 판단하는데 이 검사는 통과시켰고,
    `Http_Proxy` 같은 혼합 대소문자도 놓쳤다(2026-09-30 code-quality-reviewer, 실행으로 확인).
    한계: 환경변수만 본다 - macOS/Windows의 OS 수준 프록시 설정은 검사하지 않는다(Docker의 Linux
    컨테이너에서는 환경변수가 전부다)."""
    if not is_offline():
        return
    proxies = {k: v for k, v in urllib_request.getproxies_environment().items() if k in ("http", "https", "all") and v}
    if not proxies:
        return
    # proxy_bypass_environment는 CPython에 오래전부터 있고 httpx가 프록시 제외를 판단하는 것과 같은 원천이지만
    # typeshed에는 노출돼 있지 않다 - 직접 재구현하면 이번에 고친 불일치(NO_PROXY/no_proxy 우선순위)가
    # 재발하므로 호출 지점에서만 좁게 ignore한다.
    env_proxies = urllib_request.getproxies_environment()
    leaking = sorted(
        h for h in OFFLINE_ALLOWED_HOSTS
        if not urllib_request.proxy_bypass_environment(h, env_proxies)  # type: ignore[attr-defined]
    )
    if leaking:
        raise RuntimeError(
            "OFFLINE=1인데 프록시 환경변수(HTTP_PROXY/HTTPS_PROXY/ALL_PROXY, 대소문자 무관)가 설정돼 "
            f"있고 NO_PROXY가 허용 목록 호스트 {leaking}를 제외하지 않습니다 - 이 호스트로 가는 요청도 "
            "프록시를 거치게 됩니다. 프록시 변수를 해제하거나 NO_PROXY에 허용 목록 호스트를 넣으세요"
            "(소문자 no_proxy가 빈 값으로 설정돼 있으면 그것이 우선합니다)."
        )


def validate_allowed_endpoints() -> None:
    """오프라인인데 OLLAMA_BASE_URL/CMMS_MCP_URL이 허용 목록 밖 호스트를 가리키면
    기동을 거부한다. 허용 목록은 "코드가 알아서 안전하게 처리하겠다"는 뜻이 아니라
    "이 호스트들만 진짜로 안전하다고 검증됐다"는 뜻이라, 밖이면 조용히 봐주지 않고
    바로 실패시킨다."""
    if not is_offline():
        return
    validate_no_proxy_bypass()
    checks = {
        "OLLAMA_BASE_URL": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        "CMMS_MCP_URL": os.getenv("CMMS_MCP_URL"),
    }
    for var, url in checks.items():
        if url and is_ambiguous_url(url):
            raise RuntimeError(
                f"OFFLINE=1인데 {var}가 모호한 URL입니다(userinfo·백슬래시·공백 또는 "
                f"http/https 외 스킴) - 허용 목록 검사를 우회할 수 있어 거부합니다."
            )
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
