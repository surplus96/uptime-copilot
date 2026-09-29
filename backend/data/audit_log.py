"""MRO-FR-08: 감사 로그. LLM·도구 호출, 승인/반려, 외부 전송(CMMS push, Slack
알림), 오프라인 차단 이벤트를 기록한다(§6-3). event_store.py와 같은 패턴 -
DB_PATH 모듈 상수 + init/log/list 함수."""
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = str(Path(__file__).parent.parent / "store" / "pdm_telemetry.db")


def init_audit_table() -> None:
    conn = sqlite3.connect(DB_PATH)
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
    conn.commit()
    conn.close()


def log_event(
    event_type: str, *, thread_id: str | None = None, target: str | None = None,
    summary: str | None = None, result: str = "성공", provider: str | None = None,
) -> None:
    """감사 로그 기록 자체의 실패가 본 기능(진단/승인/알림)을 막으면 안 된다 -
    notify.py/cmms_client.py와 같은 원칙, 예외는 삼키고 로그만 남긴다."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO audit_log (ts, thread_id, event_type, target, summary, result, provider) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), thread_id, event_type, target, summary, result, provider),
        )
        conn.commit()
        conn.close()
    except sqlite3.OperationalError:
        pass


def list_events(limit: int = 100, event_type: str | None = None) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if event_type:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE event_type = ? ORDER BY id DESC LIMIT ?", (event_type, limit)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
