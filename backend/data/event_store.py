"""
이상감지 스캔 결과를 저장/조회/삭제하는 계층. 스캐너(agent_service.scan_all_machines)가
찾아낸 이벤트를 사용자가 사이드바에서 확인하고 직접 처리(삭제)할 수 있게 한다.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = str(Path(__file__).parent / "pdm_telemetry.db")


def init_event_table() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS detected_events (
            machine_id INTEGER PRIMARY KEY,
            severity TEXT,
            diagnosis TEXT,
            detected_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS completed_events (
            machine_id INTEGER,
            severity TEXT,
            diagnosis TEXT,
            detected_at TEXT,
            completed_at TEXT
        )
    """)
    conn.commit()
    conn.close()



def save_event(machine_id: int, severity: str, diagnosis: str) -> None:
    """이미 있는 설비면 최신 내용으로 덮어쓴다(machine_id가 PK라 자동 중복 방지)."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO detected_events (machine_id, severity, diagnosis, detected_at) VALUES (?, ?, ?, ?)",
        (machine_id, severity, diagnosis, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()


def list_events(limit: int = 10) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT COUNT(*) FROM detected_events").fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM detected_events ORDER BY severity, detected_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return {"total": total, "events": [dict(r) for r in rows]}


def complete_events(machine_ids: list[int]) -> int:
    """선택된 이벤트를 완료 기록(completed_events)으로 옮기고 대기 목록에서 제거한다."""
    if not machine_ids:
        return 0
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(machine_ids))
    rows = conn.execute(
        f"SELECT * FROM detected_events WHERE machine_id IN ({placeholders})", machine_ids
    ).fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    for r in rows:
        conn.execute(
            "INSERT INTO completed_events (machine_id, severity, diagnosis, detected_at, completed_at) VALUES (?, ?, ?, ?, ?)",
            (r["machine_id"], r["severity"], r["diagnosis"], r["detected_at"], now),
        )
    conn.execute(f"DELETE FROM detected_events WHERE machine_id IN ({placeholders})", machine_ids)
    conn.commit()
    conn.close()
    return len(rows)


def delete_events(machine_ids: list[int]) -> int:
    """선택된 이벤트를 기록 없이 그냥 삭제한다 (오탐/테스트 정리용)."""
    if not machine_ids:
        return 0
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(machine_ids))
    cur = conn.execute(f"DELETE FROM detected_events WHERE machine_id IN ({placeholders})", machine_ids)
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted
