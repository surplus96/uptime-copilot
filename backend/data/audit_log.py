"""MRO-FR-08: 감사 로그. LLM·도구 호출, 승인/반려, 외부 전송(CMMS push, Slack
알림), 오프라인 차단 이벤트를 기록한다(§6-3). event_store.py와 같은 패턴 -
DB_PATH 모듈 상수 + init/log/list 함수.

2026-09-29 CP-M2 교차 검토 지적 반영: 이 DB 파일(pdm_telemetry.db)은 시뮬레이터가
매 틱마다 쓰는 파일과 같아서, sqlite3의 기본 timeout(5초)을 넘겨 잠기면
OperationalError만 조용히 삼키고 아무 경고도 안 남겼다 - 실제로 5행 중 0행이
기록되는 걸 재현으로 확인했다는 지적. WAL 모드로 쓰기 충돌 자체를 줄이고, 잠겨도
경고 로그는 반드시 남긴다."""
import logging
import re
import sqlite3
from pathlib import Path

from data import runtime_dataset

logger = logging.getLogger(__name__)

DB_PATH = str(Path(__file__).parent.parent / "store" / "pdm_telemetry.db")
MAX_LIMIT = 1000


def init_audit_table() -> None:
    conn = sqlite3.connect(DB_PATH)
    # WAL은 파일 전체(다른 테이블 포함)에 적용되고 DB 파일에 영구히 남는다 - 이 앱이 같은
    # SQLite 파일을 여러 모듈이 공유해서 쓰는 구조라, 여기서 한 번 켜두면 전체적으로 쓰기
    # 충돌이 줄어든다(읽기가 쓰기를 막지 않음). 락 대기 시간은 연결마다 따로라(이 연결에만
    # 적용) 여기서 pragma로 정해봐야 다른 모듈에는 안 닿는다 - log_event()가 자기 연결에
    # 직접 timeout을 준다(2026-09-30 pipeline-optimizer 지적: 예전 busy_timeout pragma는
    # 아무 효과가 없었다).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            thread_id TEXT,
            event_type TEXT NOT NULL,
            target TEXT,
            summary TEXT,
            result TEXT,
            provider TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_thread_id ON audit_log(thread_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON audit_log(event_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(ts)")
    conn.commit()
    conn.close()


# 응답 검증용 PII_PATTERNS(주민번호/카드/휴대폰)에 더해 감사 로그에서만 추가로 가리는 형식.
# harness의 출력 검증에 넣으면 작업지시서에 이메일이 있다는 이유만으로 응답이 거부될 수
# 있어서, 저장 시점에만 적용한다(2026-09-30 security-reviewer: 이메일·국제번호·유선번호는
# 기존 패턴으로 안 가려졌다). 구분자 없는 숫자열은 오탐이 많아 다루지 않는다 - 이 목록은
# 완전하지 않다(이름·주소·계좌번호는 형식으로 못 잡는다).
_AUDIT_EXTRA_PII = {
    # (?<![\w.+-])로 시작 위치를 한 번만 시도하게 한다 - 앞 버전은 로컬 파트에서 모든 시작 위치를 다시
    # 시도해 이차 시간이었다(1만 자에 1.5초, GIL을 잡아 이벤트 루프까지 멈춤. 2026-09-30 실측).
    "이메일": re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "국제전화번호": re.compile(r"\+\d{1,3}[-\s]?\d{1,4}[-\s]?\d{3,4}[-\s]?\d{4}"),
    "유선전화번호": re.compile(r"\b0\d{1,2}[-\s]\d{3,4}[-\s]\d{4}\b"),
}
MAX_SUMMARY_LENGTH = 100


def _mask_pii(text: str | None) -> str | None:
    """감사 로그는 인증 없는 조회 API로 노출되고 보존 기한도 없다 - 사용자가 입력한
    원문(RAG 질문 등)에 PII가 섞여 있으면 그대로 영구 저장되던 문제(security-reviewer
    인계, 2026-09-29). 응답 검증과 같은 PII_PATTERNS에 감사 전용 패턴을 더해 가린다.
    모든 호출 지점을 일일이 고치는 대신 기록 직전 한 곳에서 처리한다."""
    if not text:
        return text
    from core.harness import PII_PATTERNS  # harness가 llm_provider를 끌어와서 지연 import

    for label, pattern in (*PII_PATTERNS.items(), *_AUDIT_EXTRA_PII.items()):
        text = pattern.sub(f"[{label} 마스킹]", text)
    return text


def _sanitize_summary(text: str | None) -> str | None:
    """마스킹을 먼저 하고 나서 길이를 자른다. 호출부가 먼저 잘라서(question[:50]) 넘기면
    50번째 글자에 걸친 주민번호 등이 패턴에 안 맞는 조각으로 남아 마스킹을 통과했다
    (2026-09-30 code-quality/security-reviewer, 실행으로 확인: '900101-'이 그대로 저장됨)."""
    text = _mask_pii(text)
    if text and len(text) > MAX_SUMMARY_LENGTH:
        text = text[:MAX_SUMMARY_LENGTH] + "…"
    return text


def log_event(
    event_type: str, *, thread_id: str | None = None, target: str | None = None,
    summary: str | None = None, result: str = "성공", provider: str | None = None,
) -> None:
    """감사 로그 기록 자체의 실패가 본 기능(진단/승인/알림)을 막으면 안 된다 -
    notify.py/cmms_client.py와 같은 원칙, 예외는 삼키고 로그만 남긴다. 단, 예전엔
    OperationalError만 잡아서 조용히 버렸다 - 이제 모든 예외를 잡되 반드시 경고
    로그를 남긴다(DB에는 못 적어도 로그에는 남아야 나중에 누락을 알 수 있다)."""
    try:
        summary, target = _sanitize_summary(summary), _mask_pii(target)
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute(
            "INSERT INTO audit_log (ts, thread_id, event_type, target, summary, result, provider) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (runtime_dataset.local_now().isoformat(timespec="seconds"), thread_id, event_type, target, summary, result, provider),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.warning(
            f"[감사 로그 기록 실패] event_type={event_type} thread_id={thread_id} target={target} - "
            "DB에 기록되지 않았습니다(본 기능은 계속 진행)", exc_info=True,
        )


def list_events(
    limit: int = 100, event_type: str | None = None, thread_id: str | None = None,
    since: str | None = None, until: str | None = None,
) -> list[dict]:
    """since/until은 log_event()가 쓰는 것과 같은 ISO 8601 문자열 형식(정렬 가능한
    텍스트라 문자열 비교로도 시간 순서가 맞는다). limit은 조회 API가 통제 없이
    전체를 긁어가지 못하게 MAX_LIMIT으로 상한을 둔다.

    2026-09-29 code-quality-reviewer 지적(M2, 실행으로 확인): min(limit, MAX_LIMIT)은
    음수를 못 거른다 - SQLite의 LIMIT -1은 "무제한"이라, limit=-1을 넘기면 오히려
    상한이 없어져서 테이블 전체가 나왔다. 하한도 같이 못박는다."""
    limit = max(1, min(limit, MAX_LIMIT))
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    clauses = []
    params: list[str] = []
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if thread_id:
        clauses.append("thread_id = ?")
        params.append(thread_id)
    if since:
        clauses.append("ts >= ?")
        params.append(since)
    if until:
        clauses.append("ts <= ?")
        params.append(until)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ?", (*params, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
