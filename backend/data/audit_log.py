"""MRO-FR-08: 감사 로그. LLM·도구 호출, 승인/반려, 외부 전송(CMMS push, Slack
알림), 오프라인 차단 이벤트를 기록한다(§6-3). event_store.py와 같은 패턴 -
DB_PATH 모듈 상수 + init/log/list 함수.

2026-09-29 CP-M2 교차 검토 지적 반영: 이 DB 파일(pdm_telemetry.db)은 시뮬레이터가
매 틱마다 쓰는 파일과 같아서, sqlite3의 기본 timeout(5초)을 넘겨 잠기면
OperationalError만 조용히 삼키고 아무 경고도 안 남겼다 - 실제로 5행 중 0행이
기록되는 걸 재현으로 확인했다는 지적. WAL 모드로 쓰기 충돌 자체를 줄이고, 잠겨도
경고 로그는 반드시 남긴다."""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = str(Path(__file__).parent.parent / "store" / "pdm_telemetry.db")
MAX_LIMIT = 1000


def init_audit_table() -> None:
    conn = sqlite3.connect(DB_PATH)
    # WAL은 파일 전체(다른 테이블 포함)에 적용되는 DB 레벨 설정이다 - 이 앱이 같은
    # SQLite 파일을 여러 모듈이 공유해서 쓰는 구조라, 여기서 한 번 켜두면 전체적으로
    # 쓰기 충돌이 줄어든다(읽기가 쓰기를 막지 않음). busy_timeout도 같이 늘려서
    # 짧은 순간의 락 정도는 예외 대신 대기로 넘어가게 한다.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
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


def log_event(
    event_type: str, *, thread_id: str | None = None, target: str | None = None,
    summary: str | None = None, result: str = "성공", provider: str | None = None,
) -> None:
    """감사 로그 기록 자체의 실패가 본 기능(진단/승인/알림)을 막으면 안 된다 -
    notify.py/cmms_client.py와 같은 원칙, 예외는 삼키고 로그만 남긴다. 단, 예전엔
    OperationalError만 잡아서 조용히 버렸다 - 이제 모든 예외를 잡되 반드시 경고
    로그를 남긴다(DB에는 못 적어도 로그에는 남아야 나중에 누락을 알 수 있다)."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute(
            "INSERT INTO audit_log (ts, thread_id, event_type, target, summary, result, provider) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), thread_id, event_type, target, summary, result, provider),
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
    전체를 긁어가지 못하게 MAX_LIMIT으로 상한을 둔다."""
    limit = min(limit, MAX_LIMIT)
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
