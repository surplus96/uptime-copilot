"""
이상감지 스캔 결과를 저장/조회/삭제하는 계층. 스캐너(agent_service.scan_all_machines)가
찾아낸 이벤트를 사용자가 사이드바에서 확인하고 직접 처리(삭제)할 수 있게 한다.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = str(Path(__file__).parent.parent / "store" / "pdm_telemetry.db")


def init_event_table() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS detected_events (
            machine_id INTEGER PRIMARY KEY,
            severity TEXT,
            diagnosis TEXT,
            detected_at TEXT,
            evidence_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS completed_events (
            machine_id INTEGER,
            severity TEXT,
            diagnosis TEXT,
            detected_at TEXT,
            completed_at TEXT,
            evidence_at TEXT
        )
    """)
    # 기존 DB(컬럼 추가 전에 만들어진)를 위한 마이그레이션 - 데이터 보존.
    for table in ("detected_events", "completed_events"):
        existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "evidence_at" not in existing_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN evidence_at TEXT")
    conn.commit()
    conn.close()



def save_event(machine_id: int, severity: str, diagnosis: str, evidence_at: str | None) -> None:
    """이미 있는 설비면 최신 내용으로 덮어쓴다(machine_id가 PK라 자동 중복 방지).

    evidence_at은 판정에 실제로 쓰인 근거 시각(데이터셋 자체 시각)이다. 완료 처리 시
    이 값을 그대로 넘겨받아 completed_events에 저장해두고, 다음 스캔에서 '완료 처리 당시
    근거보다 더 새로운 근거가 있는가'를 비교하는 기준으로 쓴다 (벽시계/데이터셋 현재 시각과
    비교하면 안 된다 - 그러면 고정된 과거 시각인 고장 이력은 영원히 억제되고, 매 틱 갱신되는
    이상감지 근거는 새 근거 없이도 매번 되살아나는 비대칭이 생긴다).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO detected_events (machine_id, severity, diagnosis, detected_at, evidence_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (machine_id, severity, diagnosis, datetime.now().isoformat(timespec="seconds"), evidence_at),
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

def _dataset_now() -> str | None:
    """실제 벽시계가 아니라, 시뮬레이터가 매 틱 전진시키는 telemetry 자체의 최신 시각을
    '지금'의 기준으로 삼는다 - check_recent_failure에서 이미 겪었던 것과 같은
    데이터셋시간 vs 벽시계 혼동 함정을 피하기 위함. telemetry가 완전히 빈 상태
    (최초 부팅, 데이터 적재 전)면 None을 돌려준다 - 예전엔 -> str로 선언돼 있었지만
    실제로는 None이 나올 수 있었다(2026-09-22 mypy 도입 중 발견)."""
    from data import sim_query
    return sim_query.dataset_now()



def complete_events(machine_ids: list[int]) -> int:
    """선택된 이벤트를 완료 기록(completed_events)으로 옮기고 대기 목록에서 제거한다.

    completed_at은 '언제 처리했는지' 기록용 감사(audit) 정보일 뿐이고, 재등장 여부
    판단에는 쓰이지 않는다 - detected_events에 이미 저장돼 있던 evidence_at을 그대로
    이어받아 저장해서, 다음 스캔이 '완료 처리 당시와 같은 근거인지, 더 새로운 근거인지'를
    비교할 수 있게 한다.
    """
    if not machine_ids:
        return 0
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(machine_ids))
    rows = conn.execute(
        f"SELECT * FROM detected_events WHERE machine_id IN ({placeholders})", machine_ids
    ).fetchall()
    completed_at = _dataset_now()
    for r in rows:
        conn.execute(
            "INSERT INTO completed_events (machine_id, severity, diagnosis, detected_at, completed_at, evidence_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (r["machine_id"], r["severity"], r["diagnosis"], r["detected_at"], completed_at, r["evidence_at"]),
        )
    conn.execute(f"DELETE FROM detected_events WHERE machine_id IN ({placeholders})", machine_ids)
    conn.commit()
    conn.close()
    return len(rows)


def get_detected_evidence_map() -> dict[int, str]:
    """설비별 현재 detected_events에 남아있는 evidence_at - 스캔할 때마다 같은 근거로
    Slack에 또 알리는 걸 막는 기준점 (완료 억제 여부와는 별개 문제: 이건 '이미 목록에
    있고 근거도 그대로인데 알림만 또 나가는' 스팸을 막는 것)."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT machine_id, evidence_at FROM detected_events").fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def get_completed_evidence_map() -> dict[int, str]:
    """설비별 '완료 처리 당시 근거였던' 가장 최근 evidence_at - 그보다 새로운 근거가
    생긴 경우에만 재등장시키기 위한 기준점 (완료 처리 시각 자체와 비교하면 안 됨 -
    save_event의 docstring 참고)."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT machine_id, MAX(evidence_at) as evidence_at FROM completed_events GROUP BY machine_id"
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
