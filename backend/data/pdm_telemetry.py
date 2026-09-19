"""
telemetry 실시간 조회 계층. pdm_data_loader.py가 미리 만들어둔 SQLite DB(pdm_telemetry.db)를
쿼리한다. 이 파일은 적재를 하지 않는다 - DB가 이미 존재한다고 가정한다.
"""

import sqlite3
from pathlib import Path

import pandas as pd

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

def detect_anomaly(machine_id: int, recent_hours: int = 24, z_threshold: float = 3.0) -> dict:
    """설비 자신의과거 이력을 기준선으로, 최근 값이 통계적으로 벗어났는지(Z-score) 판정한다."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT datetime, volt, rotate, pressure, vibration FROM telemetry WHERE machineID = ? ORDER BY datetime",
        conn, params=(machine_id,), parse_dates=["datetime"],
    )
    conn.close()


    if len(df) <= recent_hours:
        return {"error": "이력이 부족해 기준선을 계산할 수 없습니다."}
    

    recent = df.tail(recent_hours)
    baseline = df.iloc[:-recent_hours]


    signals = ["volt", "rotate", "pressure", "vibration"]
    anomalies = {}
    for signal in signals:
        baseline_mean = baseline[signal].mean()
        baseline_std = baseline[signal].std()
        recent_mean = recent[signal].mean()
        z_score = (recent_mean - baseline_mean) / baseline_std if baseline_std > 0 else 0
        anomalies[signal] = {
            "z_score": round(float(z_score), 2),
            "is_anomaly": bool(abs(z_score) >= z_threshold),
            "baseline_mean": round(float(baseline_mean), 2),
            "recent_mean": round(float(recent_mean), 2),
        }


    flagged = [s for s, info in anomalies.items() if info["is_anomaly"]]
    return {
        "machine_id": machine_id,
        "has_anomaly": len(flagged) > 0,
        "flagged_signals": flagged,
        "details": anomalies,
        "as_of": str(df["datetime"].iloc[-1]),  # 이 설비의 가장 최근 텔레메트리 시각(데이터셋 타임라인 기준)
    }