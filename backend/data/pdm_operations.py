"""
PdM 실시간 운영 조회 계층: machines는 정적이라 pandas로 유지하고,
errors/maint/failures는 SQLite에서 매번 조회한다 - 이벤트 시뮬레이터가 추가하는
새 행을 재시작 없이 바로 인식하기 위함이다.
"""
import sqlite3
from pathlib import Path

import pandas as pd

from data import sim_query

DATA_DIR = str(Path(__file__).parent.parent.parent / "archive")
DB_PATH = str(Path(__file__).parent.parent / "store" / "pdm_telemetry.db")

_machines_cache: pd.DataFrame | None = None


def _machines() -> pd.DataFrame:
    """PdM_machines.csv는 archive/ 데이터셋(다운로드 필요)에 있어 import 시점엔
    없을 수 있다 - 실제로 쓰일 때까지 로딩을 미뤄서 데이터 없이도 이 모듈을 import할
    수 있게 한다 (CI/테스트에서 필요)."""
    global _machines_cache
    if _machines_cache is None:
        _machines_cache = pd.read_csv(f"{DATA_DIR}/PdM_machines.csv")
    return _machines_cache

COMPONENT_DESCRIPTIONS = {
    "comp1": "전동기(모터) 구동부 - 전압 계통과 관련된 핵심 부품",
    "comp2": "회전자/베어링 - 회전속도 이상과 관련된 부품",
    "comp3": "유압/공압 계통 부품 - 압력 이상과 관련",
    "comp4": "진동 저감 장치 - 기계적 진동/불균형과 관련",
}

ERROR_DESCRIPTIONS = {
    "error1": "전압 이상 경고 - 전원 공급 불안정 가능성",
    "error2": "회전속도 이상 경고 - 회전자 계통 이상 가능성",
    "error3": "압력 이상 경고 - 유압/공압 계통 이상 가능성",
    "error4": "진동 이상 경고 - 기계적 불균형 또는 베어링 마모 가능성",
    "error5": "복합 이상 경고 - 여러 센서값의 복합적 이상 패턴(이 데이터에서는 진동 저감 장치 고장 전조로 가장 자주 관측)",
}


def _query_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(sql, conn, params=params, parse_dates=["datetime"])
    conn.close()
    return df


def get_machine_info(machine_id: int) -> dict:
    row = _machines()[_machines()["machineID"] == machine_id]
    return row.iloc[0].to_dict() if not row.empty else {"error": f"machineID {machine_id} 없음"}

RECENT_ERROR_DAYS = 30  # check_recent_failure의 within_days 기본값과 같은 기준


def get_recent_errors(machine_id: int, limit: int = 3, within_days: int = RECENT_ERROR_DAYS) -> list[dict]:
    """이 설비의 최근 오류(가동 지속되는 경고성) 이력 + 의미를 함께 반환한다.

    기준 시각(원본+시뮬레이션 전체의 최신 텔레메트리) 대비 within_days일 이내의 것만 보여준다.
    예전엔 "시뮬레이션 고장이 기록된 설비만 시뮬레이션 데이터로 한정"했는데, 그러면 같은
    설비·같은 시점이어도 고장 기록 전(주의)에는 10년 전 원본 오류가 증상에 섞이고 기록
    후(긴급)에는 빠졌다(2026-10-01 브라우저 테스트). 이제 고장 여부와 무관하게 기간으로만
    거른다. 기준 시각을 알 수 없으면(텔레메트리가 없으면) '최근'을 판단할 수 없으므로 비운다."""
    now = sim_query.dataset_now()
    if now is None:
        return []
    rows = sim_query.recent_rows("errors", "sim_errors", ["datetime", "errorID"], machine_id, limit)
    now_ts = pd.Timestamp(now)
    rows = [(dt, eid) for dt, eid in rows if (now_ts - pd.Timestamp(dt)).days <= within_days]
    return [
        {"datetime": str(dt), "errorID": eid, "description": ERROR_DESCRIPTIONS.get(eid, "알 수 없음")}
        for dt, eid in reversed(rows)
    ]


def check_recent_failure(machine_id: int, within_days: int = 30) -> dict | None:
    """최근 N일 내 실제 고장(failures) 기록이 있으면 반환한다 - HITL '긴급' 판정 기준."""
    rows = sim_query.recent_rows("failures", "sim_failures", ["datetime", "failure"], machine_id, 1)
    if not rows:
        return None
    latest_dt, latest_failure = rows[-1]
    dataset_now = sim_query.dataset_now()
    days_ago = (pd.Timestamp(dataset_now) - pd.Timestamp(latest_dt)).days
    if days_ago > within_days:
        return None
    return {
        "datetime": str(latest_dt),
        "component": latest_failure,
        "description": COMPONENT_DESCRIPTIONS.get(latest_failure, "알 수 없음"),
    }


def estimate_next_maintenance(machine_id: int) -> dict:
    """정비 이력의 평균 주기를 계산해서 다음 예상 점검일을 산출한다."""
    rows = sim_query.all_rows("maint", "sim_maint", ["datetime"], machine_id)
    if len(rows) < 2:
        return {"next_due_estimate": "이력 부족으로 추정 불가"}
    dates = pd.to_datetime([r[0] for r in rows])
    intervals = pd.Series(dates).diff().dropna()
    avg_interval = intervals.mean()
    last_date = dates[-1]
    return {
        "last_maintenance": str(last_date),
        "average_interval_days": round(avg_interval.total_seconds() / 86400, 1),
        "next_due_estimate": str(last_date + avg_interval),
    }


def get_component_failure_stats(model: str | None = None) -> list[dict]:
    """모델별 부품 고장 통계를 설비 1대당 평균 고장 횟수로 정규화해서 반환한다."""
    failures_df = _query_df("SELECT machineID, failure FROM failures")
    merged = failures_df.merge(_machines()[["machineID", "model"]], on="machineID")
    counts = merged.groupby(["model", "failure"]).size().reset_index(name="count")

    machine_counts = _machines().groupby("model").size()
    counts["failures_per_machine"] = counts.apply(
        lambda r: round(r["count"] / machine_counts[r["model"]], 3), axis=1
    )
    counts = counts.sort_values("failures_per_machine", ascending=False)

    if model:
        counts = counts[counts["model"] == model]

    return [
        {
            "model": r["model"],
            "component": r["failure"],
            "component_description": COMPONENT_DESCRIPTIONS.get(r["failure"], "알 수 없음"),
            "total_failures": int(r["count"]),
            "failures_per_machine": r["failures_per_machine"],
        }
        for _, r in counts.iterrows()
    ]
