import sqlite3
from pathlib import Path

from data.sim_engine import MachineSim

DB_PATH = str(Path(__file__).parent.parent / "store" / "pdm_telemetry.db")


def init_sim_tables() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS sim_telemetry ("datetime" TIMESTAMP, "machineID" INTEGER, volt REAL, rotate REAL, pressure REAL, vibration REAL)')
    conn.execute('CREATE TABLE IF NOT EXISTS sim_errors ("datetime" TIMESTAMP, "machineID" INTEGER, "errorID" TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS sim_failures ("datetime" TIMESTAMP, "machineID" INTEGER, "failure" TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS sim_maint ("datetime" TIMESTAMP, "machineID" INTEGER, "comp" TEXT)')
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sim_state (
            machine_id INTEGER PRIMARY KEY, state TEXT, signal TEXT, direction INTEGER,
            drift_sigma REAL, lead_hours INTEGER, elapsed INTEGER, errors_emitted INTEGER
        )""")
    conn.execute("CREATE TABLE IF NOT EXISTS sim_control (key TEXT PRIMARY KEY, value TEXT)")
    for table in ("sim_telemetry", "sim_errors", "sim_failures", "sim_maint"):
        conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_machine ON {table}("machineID", "datetime")')
    conn.commit()
    conn.close()


def load_states() -> dict[int, MachineSim]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT machine_id, state, signal, direction, drift_sigma, lead_hours, elapsed, errors_emitted FROM sim_state"
    ).fetchall()
    conn.close()
    states = {i: MachineSim(machine_id=i) for i in range(1, 101)}
    for r in rows:
        states[r[0]] = MachineSim(*r)
    return states


def save_states(states: dict[int, MachineSim], conn: sqlite3.Connection | None = None) -> None:
    """conn을 넘기면 그 커넥션의 트랜잭션에 그대로 편승한다(커밋/닫기는 호출자 책임) -
    _tick_once()가 텔레메트리 기록과 상태 저장을 한 트랜잭션으로 묶을 때 쓴다
    (2026-09-22 code-quality-reviewer 지적: 따로 커밋되면 그 사이에 죽었을 때
    텔레메트리는 남고 상태는 전 틱으로 되돌아가는 불일치가 생긴다). conn 없이
    부르면 기존처럼 자체 커넥션을 열고 바로 커밋한다."""
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)
    conn.executemany(
        "INSERT OR REPLACE INTO sim_state VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(m.machine_id, m.state, m.signal, m.direction, m.drift_sigma, m.lead_hours, m.elapsed, m.errors_emitted)
         for m in states.values()],
    )
    if own_conn:
        conn.commit()
        conn.close()
