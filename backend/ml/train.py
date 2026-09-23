"""
고장 위험 예측 모델 학습 + 시간분할 평가 + Z-score 기준선 비교 - 1회 실행용 스크립트.
CP-U1 교차 검토(2026-09-23, docs/decisions.md)를 거쳐 확정된 설계:
- 시간 기준 분할만 사용한다(무작위/행 단위 분할 금지) - 3h 간격 샘플에 24~48h 피처
  창이 겹쳐서 인접 행끼리 피처가 거의 동일하기 때문에, 무작위 분할은 누수가 된다.
- Z-score 기준선의 평균/표준편차는 학습 구간 데이터로만 계산한다(공정 비교).
- 행 단위(재현율/정밀도/PR-AUC)와 이벤트 단위(실제 고장 하나당 탐지 여부·조기경보시간)를
  둘 다 낸다 - 3h 간격 샘플링 때문에 고장 하나가 8개 행으로 중복 반영되어 행 단위 지표만
  보면 낙관적으로 보일 수 있어서, 이벤트 단위로 교차 확인한다.
"""
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.metrics import average_precision_score, precision_score, recall_score

from ml.build_features import ARCHIVE_DIR, COMPONENTS, LABEL_WINDOW_H, load_all_features

TRAIN_END = pd.Timestamp("2015-09-01")
VAL_END = pd.Timestamp("2015-11-01")
SIGNAL_TO_COMPONENT = {"volt": "comp1", "rotate": "comp2", "pressure": "comp3", "vibration": "comp4"}
COMPONENT_TO_SIGNAL = {v: k for k, v in SIGNAL_TO_COMPONENT.items()}
RESULTS_PATH = Path(__file__).parent.parent / "store" / "ml_features" / "eval_results.csv"


def _time_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return (
        df[df["datetime"] < TRAIN_END],
        df[(df["datetime"] >= TRAIN_END) & (df["datetime"] < VAL_END)],
        df[df["datetime"] >= VAL_END],
    )


def _zscore_baseline(train: pd.DataFrame, test: pd.DataFrame, comp: str, z_threshold: float = 3.0) -> pd.Series:
    """detect_anomaly()와 같은 방법론(설비별 평균/표준편차 대비 Z-score)이지만, 학습
    구간 데이터로만 기준선을 계산해서 모델과 공정하게 비교한다."""
    sig = COMPONENT_TO_SIGNAL[comp]
    col = f"{sig}_mean_3h"
    stats = train.groupby("machineID")[col].agg(["mean", "std"])
    mu = test["machineID"].map(stats["mean"])
    sd = test["machineID"].map(stats["std"])
    z = (test[col] - mu).abs() / sd
    return (z >= z_threshold).astype(int)


def evaluate_component(comp: str, train: pd.DataFrame, test: pd.DataFrame, feature_cols: list[str]) -> dict:
    label = f"label_fail_{comp}_{LABEL_WINDOW_H}h"

    clf = LGBMClassifier(
        n_estimators=200, learning_rate=0.05, num_leaves=15,
        is_unbalance=True, random_state=42, verbosity=-1,
    )
    clf.fit(train[feature_cols], train[label])
    proba = clf.predict_proba(test[feature_cols])[:, 1]
    pred = pd.Series((proba >= 0.5).astype(int), index=test.index)
    base_pred = _zscore_baseline(train, test, comp)

    failures = pd.read_csv(ARCHIVE_DIR / "PdM_failures.csv", parse_dates=["datetime"])
    events = failures[(failures["failure"] == comp) & (failures["datetime"] >= VAL_END)]
    detected_model = detected_base = 0
    lead_times = []
    for _, ev in events.iterrows():
        window = test[
            (test["machineID"] == ev["machineID"])
            & (test["datetime"] > ev["datetime"] - pd.Timedelta(hours=LABEL_WINDOW_H))
            & (test["datetime"] <= ev["datetime"])
        ]
        if window.empty:
            continue
        hit = pred.loc[window.index] == 1
        if hit.any():
            detected_model += 1
            first_alert = window.loc[hit[hit].index, "datetime"].min()
            lead_times.append((ev["datetime"] - first_alert).total_seconds() / 3600)
        if (base_pred.loc[window.index] == 1).any():
            detected_base += 1

    n_events = len(events)
    false_alarms_per_machine_day = pred[test[label] == 0].mean() * (24 / 3)

    return {
        "component": comp,
        "row_recall": round(recall_score(test[label], pred, zero_division=0), 3),
        "row_precision": round(precision_score(test[label], pred, zero_division=0), 3),
        "row_prauc": round(average_precision_score(test[label], proba), 3) if test[label].sum() else None,
        "baseline_row_recall": round(recall_score(test[label], base_pred, zero_division=0), 3),
        "baseline_row_precision": round(precision_score(test[label], base_pred, zero_division=0), 3),
        "n_events": n_events,
        "event_recall_model": round(detected_model / n_events, 3) if n_events else None,
        "event_recall_baseline": round(detected_base / n_events, 3) if n_events else None,
        "mean_lead_hours_model": round(np.mean(lead_times), 1) if lead_times else None,
        "false_alarms_per_machine_day_model": round(false_alarms_per_machine_day, 3),
    }


if __name__ == "__main__":
    df = load_all_features()
    df["model"] = df["model"].astype("category")
    train, val, test = _time_split(df)
    print(f"train {len(train)}, val {len(val)}, test {len(test)}")

    label_cols = [f"label_fail_{c}_{LABEL_WINDOW_H}h" for c in COMPONENTS]
    feature_cols = [c for c in df.columns if c not in ("datetime", "machineID", *label_cols)]

    rows = [evaluate_component(c, train, test, feature_cols) for c in COMPONENTS]
    result = pd.DataFrame(rows)
    print(result.to_string(index=False))
    result.to_csv(RESULTS_PATH, index=False)
    print(f"\n저장: {RESULTS_PATH}")
