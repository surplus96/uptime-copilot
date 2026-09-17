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

def _dataset_now() -> str:
    """실제 벽시계가 아니라, 시뮬레이터가 매 틱 전진시키는 telemetry 자체의 최신 시각을
    '지금'의 기준으로 삼는다 - check_recent_failure에서 이미 겪었던 것과 같은
    데이터셋시간 vs 벽시계 혼동 함정을 피하기 위함."""
    conn = sqlite3.connect(DB_PATH)
    now = conn.execute("SELECT MAX(datetime) FROM telemetry").fetchone()[0]
    conn.close()
    return now


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
    now = _dataset_now()   # <- datetime.now() 였던 부분을 데이터셋 자체 시각으로 교체
    for r in rows:
        conn.execute(
            "INSERT INTO completed_events (machine_id, severity, diagnosis, detected_at, completed_at) VALUES (?, ?, ?, ?, ?)",
            (r["machine_id"], r["severity"], r["diagnosis"], r["detected_at"], now),
        )
    conn.execute(f"DELETE FROM detected_events WHERE machine_id IN ({placeholders})", machine_ids)
    conn.commit()
    conn.close()
    return len(rows)


def get_completed_at_map() -> dict[int, str]:
    """설비별 '가장 최근' 완료 처리 시각 - 그 이후 새 근거가 생긴 경우에만 재등장시키기 위한 기준점."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT machine_id, MAX(completed_at) as completed_at FROM completed_events GROUP BY machine_id"
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


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
