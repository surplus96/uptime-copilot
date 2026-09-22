"""
telemetry 실시간 조회 계층. pdm_data_loader.py가 미리 만들어둔 SQLite DB(pdm_telemetry.db)를
쿼리한다. 이 파일은 적재를 하지 않는다 - DB가 이미 존재한다고 가정한다.
"""

import sqlite3
from pathlib import Path
import pandas as pd

from data import sim_query

TELEMETRY_COLUMNS = ["datetime", "volt", "rotate", "pressure", "vibration"]
_BASELINE_CACHE: dict[int, dict[str, tuple[float, float]]] = {}
DB_PATH = str(Path(__file__).parent.parent / "store" / "pdm_telemetry.db")


def get_recent_telemetry(machine_id: int, hours: int = 24) -> list[dict]:
    """특정 설비의 최근 N시간 센서 데이터를 조회한다."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT datetime, volt, rotate, pressure, vibration FROM telemetry "
        "WHERE machineID = ? ORDER BY datetime DESC LIMIT ?",
        (machine_id, hours),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _baseline(machine_id: int) -> dict[str, tuple[float, float]]:
    """원본 telemetry에서만 계산해 캐시한다 - 원본은 안 바뀌므로 프로세스 생애 동안 1회면
    충분하고, 시뮬레이션이 만드는 열화 데이터가 기준선에 섞여 들어가는 걸 막는다."""
    if machine_id not in _BASELINE_CACHE:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(
            "SELECT volt, rotate, pressure, vibration FROM telemetry WHERE machineID = ?",
            conn, params=(machine_id,),
        )
        conn.close()
        _BASELINE_CACHE[machine_id] = {
            sig: (float(df[sig].mean()), float(df[sig].std())) for sig in ["volt", "rotate", "pressure", "vibration"]
        }
    return _BASELINE_CACHE[machine_id]


def detect_anomaly(machine_id: int, recent_hours: int = 24, z_threshold: float = 3.0) -> dict:
    """설비 자신의 과거 이력을 기준선으로, 최근 값이 통계적으로 벗어났는지(Z-score) 판정한다."""
    rows = sim_query.recent_rows("telemetry", "sim_telemetry", TELEMETRY_COLUMNS, machine_id, recent_hours)
    if len(rows) < recent_hours:
        return {"error": "이력이 부족해 기준선을 계산할 수 없습니다."}

    baseline = _baseline(machine_id)
    signals = ["volt", "rotate", "pressure", "vibration"]
    anomalies = {}
    for i, signal in enumerate(signals, start=1):  # 0번 컬럼은 datetime
        values = [r[i] for r in rows]
        recent_mean = sum(values) / len(values)
        baseline_mean, baseline_std = baseline[signal]
        z_score = (recent_mean - baseline_mean) / baseline_std if baseline_std else 0
        anomalies[signal] = {
            "z_score": round(float(z_score), 2),
            "is_anomaly": bool(abs(z_score) >= z_threshold),
            "baseline_mean": round(baseline_mean, 2),
            "recent_mean": round(recent_mean, 2),
        }

    flagged = [s for s, info in anomalies.items() if info["is_anomaly"]]
    return {
        "machine_id": machine_id,
        "has_anomaly": len(flagged) > 0,
        "flagged_signals": flagged,
        "details": anomalies,
        "as_of": str(rows[-1][0]),
    }