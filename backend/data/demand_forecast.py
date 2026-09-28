"""
MRO-FR-02: 부품 수요 예측. mro-copilot-upgrade-plan.md §6-2 공식을 구현한다 -
예방 교체율(전체 설비) + 고장 교체(경보 있는 설비는 검증기간 정밀도, 없는 설비는
학습기간 기본 고장율)로 나눠서 이중계산을 피한다.
"""

from pathlib import Path

import joblib
import pandas as pd
from sklearn.metrics import precision_score

from ml.build_features import load_all_features
from ml.predict import predict_failure_risk
from ml.train import MODEL_DIR, _time_split

ARCHIVE_DIR = Path(__file__).parent.parent.parent / "archive"
TRAIN_START, TRAIN_END = pd.Timestamp("2015-01-01"), pd.Timestamp("2015-09-01")
N_MACHINES = 100
RISK_THRESHOLD = 0.5
COMPONENTS = ["comp1", "comp2", "comp3", "comp4"]


def _training_period_rates() -> dict:
    """comp별 예방_교체_일일비율 + 고장_교체_일일비율(무조건부)을 학습기간(1~8월)에서
    구한다. 분모는 '설비-일' 전체(100대 x 학습기간 일수).

    2026-09-28 교차 검토로 두 번 고친 지점: 처음엔 이 무조건부 고장 비율을 '경보 없는'
    설비에 그대로 썼다가, 진짜 조건부 확률(P(고장|비경보), 검증기간 실측 사실상 0)보다
    최대 700만 배 크다는 걸 지적받아 과대예측 문제로 확인됐다. 그런데 조건부 비율로
    통째로 바꾸자 이번엔 "미래에 알람이 뜰 수 있는 날들"의 위험을 전혀 반영 못 해
    22~32% 과소예측이 났다. 결론: 이 무조건부 비율은 **버릴 게 아니라 용도가 다르다** -
    "오늘(1일차)처럼 실제 알람 상태를 아는 날"에는 조건부 비율(_validation_rates())을,
    "내일 이후(미래, 알람 상태를 모르는 날)"에는 이 무조건부 비율을 쓴다."""
    maint = pd.read_csv(ARCHIVE_DIR / "PdM_maint.csv", parse_dates=["datetime"])
    fail = pd.read_csv(ARCHIVE_DIR / "PdM_failures.csv", parse_dates=["datetime"])
    maint = maint[(maint["datetime"] >= TRAIN_START) & (maint["datetime"] < TRAIN_END)]

    # 부품까지 일치해야 '고장 후 교체' - P-1에서 겪은 함정(부품 무관 병합) 재발 방지
    merged = maint.merge(fail.rename(columns={"failure": "comp"}), on=["datetime", "machineID", "comp"], how="left", indicator=True)
    is_post_failure = merged["_merge"] == "both"

    machine_days = N_MACHINES * (TRAIN_END - TRAIN_START).days
    rates = {}
    for comp in COMPONENTS:
        comp_rows = merged[merged["comp"] == comp]
        n_failure = is_post_failure[merged["comp"] == comp].sum()
        n_preventive = len(comp_rows) - n_failure
        rates[comp] = {
            "preventive_rate": n_preventive / machine_days,
            "baseline_failure_rate": n_failure / machine_days,
        }
    return rates


def _validation_rates() -> tuple[dict, dict]:
    """검증기간(9~10월)에서 P(고장|경보)와 P(고장|비경보)를 같은 표본·같은 기간에서
    같이 잰다. 학습기간으로 재면 모델이 자기 학습 데이터로 자기를 채점하는 셈이라
    부풀려진다(§6-2 지적) - 반드시 검증기간이어야 하고, 두 값 다 같은 원칙을 적용해야
    일관성이 있다(2026-09-28 교차 검토: 이전엔 P(고장|비경보) 자리에 학습기간
    무조건부 비율을 썼던 게 실제 오류였음).

    원본 피처는 3시간 간격으로 24시간 앞을 내다보는 라벨이 달려 있어 인접 행끼리
    창이 21시간 겹친다 - 그대로 평균 내면 '설비-일' 단위인 preventive_rate와 단위가
    안 맞는다. 그래서 설비·날짜별 첫 행만 골라 '하루에 한 번' 표본으로 맞춘다."""
    df = load_all_features()
    _, val, _ = _time_split(df)
    val = val.copy()
    val["date"] = val["datetime"].dt.date
    daily = val.sort_values("datetime").groupby(["machineID", "date"]).first().reset_index()

    p_fail_given_alarm = {}
    p_fail_given_no_alarm = {}
    for comp in COMPONENTS:
        bundle = joblib.load(MODEL_DIR / f"model_{comp}.pkl")
        clf, feature_cols = bundle["model"], bundle["feature_cols"]
        x = daily.copy()
        x["model"] = x["model"].astype("category").cat.set_categories(bundle["model_categories"])
        proba = clf.predict_proba(x[feature_cols])[:, 1]
        alarmed = proba >= RISK_THRESHOLD
        label = daily[f"label_fail_{comp}_24h"]

        p_fail_given_alarm[comp] = precision_score(label, alarmed.astype(int), zero_division=0)
        p_fail_given_no_alarm[comp] = label[~alarmed].mean() if (~alarmed).any() else 0.0
    return p_fail_given_alarm, p_fail_given_no_alarm



def _currently_alarmed_counts() -> dict:
    counts = {c: 0 for c in ["comp1", "comp2", "comp3", "comp4"]}
    for machine_id in range(1, 101):
        risk = predict_failure_risk(machine_id)
        for comp, r in risk.items():
            if r["probability"] >= RISK_THRESHOLD:
                counts[comp] += 1
    return counts

def _test_period_alarmed_counts() -> dict:
    """검증(9~10월)이 아니라 테스트 시작 시점(2015-11-01)에 각 설비가 실제로 경보
    상태였는지를 캐시된 과거 피처로 재현한다. 2026-09-28 CP-M1 사전 점검 지적:
    forecast_demand()의 기존 정확도 확인("6% 이내 일치")은 '지금 시점'의 실시간
    경보 수를 11~12월 실측과 단순 비교한 것이라 진짜 시점 정합 백테스트가 아니었다.
    이 함수는 모델을 다시 학습하지 않고, 이미 학습된 모델(model_{comp}.pkl)을
    2015-11-01 스냅샷에 그대로 적용해서 '그 시점에 결정 가능했던 예측'만 쓴다 -
    학습·검증 데이터를 하나도 들여다보지 않는다."""
    df = load_all_features()
    _, _, test = _time_split(df)
    snapshot = test.sort_values("datetime").groupby("machineID").first()

    counts = {}
    for comp in COMPONENTS:
        bundle = joblib.load(MODEL_DIR / f"model_{comp}.pkl")
        clf, feature_cols = bundle["model"], bundle["feature_cols"]
        x = snapshot.copy()
        x["model"] = x["model"].astype("category").cat.set_categories(bundle["model_categories"])
        proba = clf.predict_proba(x[feature_cols])[:, 1]
        counts[comp] = int((proba >= RISK_THRESHOLD).sum())
    return counts


def _expected_demand(
    horizon_days: int, n_alarmed: int, preventive_rate: float,
    baseline_failure_rate: float, p_alarm: float, p_no_alarm: float,
) -> float:
    """예방 + 고장(1일차/2일차 이후 분리) 기대수요. 2026-09-28 교차 검토가 두 차례
    지적한 반대 방향 오류를 모두 반영한 최종 형태:
    - 1일차(오늘 알람 상태를 실제로 아는 날)는 경보/비경보를 나눠 검증기간 조건부
      비율(p_alarm/p_no_alarm)을 쓴다 - 무조건부 비율을 쓰면 과대예측(최대 700만
      배 차이 실측).
    - 2일차부터(미래, 어느 설비가 알람 상태일지 모르는 날)는 100대 전체에 학습기간
      무조건부 비율(baseline_failure_rate)을 쓴다 - 조건부 비율(p_no_alarm)을 그대로
      끌어쓰면 "미래에 새로 알람이 뜨는 설비"의 위험이 빠져 22~32% 과소예측된다."""
    preventive = N_MACHINES * preventive_rate * horizon_days
    n_normal = N_MACHINES - n_alarmed
    day1 = n_alarmed * p_alarm + n_normal * p_no_alarm
    rest = N_MACHINES * baseline_failure_rate * max(horizon_days - 1, 0)
    return round(preventive + day1 + rest, 1)


def backtest_test_period() -> dict:
    """2015-11-01 스냅샷만으로 61일(11~12월) 수요를 예측하고, 실제 관측치(P-1 탐색
    노트: comp1~4 각 122/124/125/116건 - `docs/design/parts_demand_exploration.md`)
    와 비교한다. 학습/검증 기간 데이터는 여기서 전혀 재사용하지 않으므로 누수가
    있다면 여기서 드러난다."""
    rates = _training_period_rates()
    p_fail_given_alarm, p_fail_given_no_alarm = _validation_rates()
    alarmed = _test_period_alarmed_counts()
    horizon_days = 61  # 2015-11-01 ~ 2015-12-31

    actual = {"comp1": 122, "comp2": 124, "comp3": 125, "comp4": 116}
    result = {}
    for comp in COMPONENTS:
        predicted = _expected_demand(
            horizon_days, alarmed[comp], rates[comp]["preventive_rate"], rates[comp]["baseline_failure_rate"],
            p_fail_given_alarm[comp], p_fail_given_no_alarm[comp],
        )
        result[comp] = {
            "predicted": predicted,
            "actual": actual[comp],
            "n_alarmed_at_test_start": alarmed[comp],
            "error_pct": round((predicted - actual[comp]) / actual[comp] * 100, 1),
        }
    return result


def forecast_demand(horizon_days: int = 30) -> dict:
    rates = _training_period_rates()
    p_fail_given_alarm, p_fail_given_no_alarm = _validation_rates()
    alarmed = _currently_alarmed_counts()

    return {
        comp: _expected_demand(
            horizon_days, alarmed[comp], rates[comp]["preventive_rate"], rates[comp]["baseline_failure_rate"],
            p_fail_given_alarm[comp], p_fail_given_no_alarm[comp],
        )
        for comp in COMPONENTS
    }


if __name__ == "__main__":
    for horizon in (30, 90):
        print(f"{horizon}일 기대수요: {forecast_demand(horizon)}")
