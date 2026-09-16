"""
실시간 런타임 시뮬레이터: 정적 데이터셋(2015~2016)만으로는 "지금 새 데이터가 들어온다"는
상황을 재현할 수 없어서, 매 틱마다 각 설비의 과거 통계 분포에서 새로운 텔레메트리 값을 뽑아
추가하고, 소확률로 오류/고장 이벤트도 주입하는 시뮬레이터. 시나리오 1(실시간 자동 감지) 실습용.
"""
import random
import sqlite3
from datetime import timedelta
from pathlib import Path

import pandas as pd

DB_PATH = str(Path(__file__).parent / "pdm_telemetry.db")
DATA_DIR = str(Path(__file__).parent.parent.parent / "archive")

_machines = pd.read_csv(f"{DATA_DIR}/PdM_machines.csv")

SIGNALS = ["volt", "rotate", "pressure", "vibration"]
SIGNAL_TO_ERROR = {"volt": "error1", "rotate": "error2", "pressure": "error3", "vibration": "error4"}
SIGNAL_TO_COMPONENT = {"volt": "comp1", "rotate": "comp2", "pressure": "comp3", "vibration": "comp4"}

ANOMALY_PROBABILITY = 0.05  # 설비 1대당 이 틱에서 이상이 발생할 확률
FAILURE_PROBABILITY = 0.2   # 이상 이벤트 중 실제 고장으로까지 이어질 확률


def _get_last_timestamp(conn) -> pd.Timestamp:
    row = conn.execute("SELECT MAX(datetime) FROM telemetry").fetchone()
    return pd.Timestamp(row[0])


def _all_machine_baselines(conn) -> dict[int, dict]:
    """설비별 신호 평균/표준편차를 한 번의 쿼리로 전부 계산한다."""
    df = pd.read_sql_query("SELECT machineID, volt, rotate, pressure, vibration FROM telemetry", conn)
    grouped = df.groupby("machineID")[SIGNALS].agg(["mean", "std"])
    baselines = {}
    for machine_id, row in grouped.iterrows():
        baselines[int(machine_id)] = {sig: (row[(sig, "mean")], row[(sig, "std")]) for sig in SIGNALS}
    return baselines
def generate_tick(hours: int = 1) -> dict:
    """hours시간 분량의 텔레메트리를 한 번에 생성한다. 이상이 걸린 설비는 생성 기간 내내
    같은 신호가 같은 방향으로 지속적으로 벗어나게 해서(점진적 열화를 흉내), detect_anomaly()의
    24시간 윈도우 안에서 실제로 감지 가능하게 만든다."""
    conn = sqlite3.connect(DB_PATH)
    last_ts = _get_last_timestamp(conn)
    baselines = _all_machine_baselines(conn)

    # 이번 배치에서 이상이 발생할 설비/신호/방향을 미리 확정 - 매 시간 다시 안 뽑아야 지속성이 생김
    anomaly_plan: dict[int, tuple[str, int]] = {}
    for machine_id in _machines["machineID"]:
        if random.random() < ANOMALY_PROBABILITY:
            anomaly_plan[int(machine_id)] = (random.choice(SIGNALS), random.choice([-1, 1]))

    telemetry_rows, error_rows, failure_rows = [], [], []
    for h in range(1, hours + 1):
        new_ts = last_ts + timedelta(hours=h)
        for machine_id in _machines["machineID"]:
            machine_id = int(machine_id)
            baseline = baselines.get(machine_id, {sig: (0, 1) for sig in SIGNALS})
            info = anomaly_plan.get(machine_id)
            anomaly_signal, anomaly_direction = info if info else (None, None)

            row = {"datetime": new_ts, "machineID": machine_id}
            for sig in SIGNALS:
                mean, std = baseline[sig]
                std = std if std and std > 0 else 1
                if sig == anomaly_signal:
                    offset = random.uniform(4, 6) * std * anomaly_direction
                    row[sig] = mean + offset
                else:
                    row[sig] = random.gauss(mean, std)
            telemetry_rows.append(row)

            if anomaly_signal:
                error_rows.append({"datetime": new_ts, "machineID": machine_id, "errorID": SIGNAL_TO_ERROR[anomaly_signal]})

    for machine_id, (signal, _direction) in anomaly_plan.items():
        if random.random() < FAILURE_PROBABILITY:
            failure_rows.append({
                "datetime": last_ts + timedelta(hours=hours),
                "machineID": machine_id,
                "failure": SIGNAL_TO_COMPONENT[signal],
            })

    pd.DataFrame(telemetry_rows).to_sql("telemetry", conn, if_exists="append", index=False)
    if error_rows:
        pd.DataFrame(error_rows).to_sql("errors", conn, if_exists="append", index=False)
    if failure_rows:
        pd.DataFrame(failure_rows).to_sql("failures", conn, if_exists="append", index=False)

    conn.commit()
    conn.close()

    return {
        "hours_generated": hours,
        "last_timestamp": str(last_ts + timedelta(hours=hours)),
        "machines_updated": len(_machines),
        "anomaly_machines": list(anomaly_plan.keys()),
        "new_errors": len(error_rows),
        "new_failures": len(failure_rows),
    }


