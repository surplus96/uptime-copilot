"""원본 데이터(읽기 전용)와 시뮬레이션 데이터(sim_*)를 합쳐 조회하는 공용 헬퍼.
두 테이블 모두 (machineID, datetime) 인덱스가 있어서, "최근 N개"류 조회는 전체 스캔
대신 각 테이블에서 인덱스로 N개씩만 가져와 병합하는 방식으로 비용을 거의 상수로 유지한다.
sim_* 테이블이 아직 없어도(시뮬레이터를 한 번도 안 켰을 때) 원본만으로 조용히 동작한다.
"""
import sqlite3
from pathlib import Path

DB_PATH = str(Path(__file__).parent.parent / "store" / "pdm_telemetry.db")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def recent_rows(table: str, sim_table: str, columns: list[str], machine_id: int, limit: int) -> list[tuple]:
    """설비 하나의 최근 `limit`개 행을 원본+시뮬레이션 합쳐 오래된→최신 순으로 반환한다."""
    conn = sqlite3.connect(DB_PATH)
    cols = ", ".join(columns)
    rows = conn.execute(
        f'SELECT {cols} FROM {table} WHERE "machineID"=? ORDER BY datetime DESC LIMIT ?',
        (machine_id, limit),
    ).fetchall()
    if _table_exists(conn, sim_table):
        rows += conn.execute(
            f'SELECT {cols} FROM {sim_table} WHERE "machineID"=? ORDER BY datetime DESC LIMIT ?',
            (machine_id, limit),
        ).fetchall()
    conn.close()
    rows.sort(key=lambda r: r[0])
    return rows[-limit:]


def all_rows(table: str, sim_table: str, columns: list[str], machine_id: int) -> list[tuple]:
    """설비 하나의 전체 이력을 원본+시뮬레이션 합쳐 오래된→최신 순으로 반환한다.
    정비 기록처럼 설비당 행 수가 원래 적은 테이블 전용이다 - telemetry에는 쓰지 않는다."""
    conn = sqlite3.connect(DB_PATH)
    cols = ", ".join(columns)
    rows = conn.execute(f'SELECT {cols} FROM {table} WHERE "machineID"=? ORDER BY datetime', (machine_id,)).fetchall()
    if _table_exists(conn, sim_table):
        rows += conn.execute(f'SELECT {cols} FROM {sim_table} WHERE "machineID"=? ORDER BY datetime', (machine_id,)).fetchall()
    conn.close()
    rows.sort(key=lambda r: r[0])
    return rows


def dataset_now() -> str | None:
    """원본+시뮬레이션 전체에서 가장 최신 telemetry 시각. event_store._dataset_now()가 위임한다."""
    conn = sqlite3.connect(DB_PATH)
    values = [conn.execute("SELECT MAX(datetime) FROM telemetry").fetchone()[0]]
    if _table_exists(conn, "sim_telemetry"):
        values.append(conn.execute("SELECT MAX(datetime) FROM sim_telemetry").fetchone()[0])
    conn.close()
    values = [v for v in values if v]
    return max(values) if values else None

def sim_only_now() -> str | None:
    """sim_telemetry에만 있는 최신 시각 - 아직 한 틱도 안 돌았으면 None.
    _tick_once()가 리셋 이후 첫 틱의 기준 시각을 정할 때 쓴다. dataset_now()처럼
    원본 데이터까지 같이 보면 안 되는 이유: 원본의 2016년 시각을 그대로 이어받으면,
    시뮬레이터가 전진시키는 '지금'이 원본의 오래된 고장 기록과 다시 가까워져서
    check_recent_failure()가 그 기록들을 '방금 발생'으로 오인하는 버그가 있었다
    (2026-09-22 실제 재현·확인 - #15/#64/#90/#95가 2015-12-31 원본 고장 기록인데
    '긴급'으로 재등장)."""
    conn = sqlite3.connect(DB_PATH)
    value = None
    if _table_exists(conn, "sim_telemetry"):
        value = conn.execute("SELECT MAX(datetime) FROM sim_telemetry").fetchone()[0]
    conn.close()
    return value


def sim_only_rows(sim_table: str, columns: list[str], machine_id: int, limit: int) -> list[tuple]:
    """원본과 절대 안 섞고 sim_table에서만 조회한다."""
    conn = sqlite3.connect(DB_PATH)
    rows = []
    cols = ", ".join(columns)
    if _table_exists(conn, sim_table):
        rows = conn.execute(
            f'SELECT {cols} FROM {sim_table} WHERE "machineID"=? ORDER BY datetime DESC LIMIT ?',
            (machine_id, limit),
        ).fetchall()
    conn.close()
    rows.sort(key=lambda r: r[0])
    return rows[-limit:] if limit else rows


def machine_has_sim_failure(machine_id: int) -> bool:
    """이 설비가 시뮬레이션으로 고장 처리된 적이 있는지 - '지금 시뮬레이터가 추적 중인
    설비'인지 판단하는 기준. 참이면 증상 텍스트도 시뮬레이션 데이터만(없으면 빈 채로)
    보여주고 원본 옛날 데이터로 채우지 않는다."""
    conn = sqlite3.connect(DB_PATH)
    has = False
    if _table_exists(conn, "sim_failures"):
        has = conn.execute(
            'SELECT 1 FROM sim_failures WHERE "machineID"=? LIMIT 1', (machine_id,)
        ).fetchone() is not None
    conn.close()
    return has
