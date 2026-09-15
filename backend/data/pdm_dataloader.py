"""
데이터셋 적재: telemetry는 SQLite로(용량이 크므로), 나머지는 pandas로 가볍게 로드한다.
"""

import sqlite3
from pathlib import Path

import pandas as pd

DATA_DIR = str(Path(__file__).parent.parent.parent / "archive")
DB_PATH = str(Path(__file__).parent / "pdm_telemetry.db")


def load_telemetry_to_sqlite():
    """87만 행짜리 telemetry.csv를 SQLite로 1회 적재한다."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_csv(f"{DATA_DIR}/PdM_telemetry.csv", parse_dates=["datetime"])
    df.to_sql("telemetry", conn, if_exists="replace", index=False)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_machine ON telemetry(machineID)")
    conn.commit()
    conn.close()
    print(f"telemetry {len(df)}행 적재 완료 -> {DB_PATH}")


def load_small_tables():
    machines = pd.read_csv(f"{DATA_DIR}/PdM_machines.csv")
    errors = pd.read_csv(f"{DATA_DIR}/PdM_errors.csv", parse_dates=["datetime"])
    maint = pd.read_csv(f"{DATA_DIR}/PdM_maint.csv", parse_dates=["datetime"])
    failures = pd.read_csv(f"{DATA_DIR}/PdM_failures.csv", parse_dates=["datetime"])
    return machines, errors, maint, failures


if __name__ == "__main__":
    load_telemetry_to_sqlite()
    machines, errors, maint, failures = load_small_tables()
    print(f"machines: {len(machines)}, errors: {len(errors)}, maint: {len(maint)}, failures: {len(failures)}")

    # 확인용: machineID=1의 최근 오류/정비 이력 몇 개
    print("\n[machineID=1] 최근 오류 3건:")
    print(errors[errors["machineID"] == 1].tail(3).to_string(index=False))
    print("\n[machineID=1] 최근 정비 3건:")
    print(maint[maint["machineID"] == 1].tail(3).to_string(index=False))
