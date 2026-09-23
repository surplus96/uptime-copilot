"""
predict_failure_risk(machine_id) - 학습된 부품별 모델로 "지금" 기준 24h 내 고장
확률과, 그 확률에 가장 크게 기여한 피처 상위 3개를 낸다. train.py가 저장해 둔
모델(backend/store/ml_models/model_{comp}.pkl)을 그대로 불러와서 쓴다 - 재학습 없음.

라이브 피처 행은 build_features.py의 학습용 피처와 정의를 그대로 따르되,
원본 CSV 대신 sim_query(원본+시뮬레이션 합쳐 조회)로 "지금" 시점 값을 계산한다.
"""
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from data import pdm_operations, sim_query
from data.pdm_telemetry import TELEMETRY_COLUMNS
from ml.build_features import COMPONENTS, ERROR_IDS, ERROR_WINDOWS_H, SIGNALS

MODEL_DIR = Path(__file__).parent.parent / "store" / "ml_models"


def _load_model_bundle(comp: str) -> dict:
    path = MODEL_DIR / f"model_{comp}.pkl"
    if not path.exists():
        raise FileNotFoundError(f"{comp} 모델이 없습니다 - 먼저 `python -m ml.train`을 실행하세요: {path}")
    return joblib.load(path)


def _build_live_feature_row(machine_id: int) -> pd.DataFrame:
    now = pd.Timestamp(sim_query.dataset_now())

    tele = pd.DataFrame(
        sim_query.recent_rows("telemetry", "sim_telemetry", TELEMETRY_COLUMNS, machine_id, 30),
        columns=TELEMETRY_COLUMNS,
    )
    tele["datetime"] = pd.to_datetime(tele["datetime"])

    feats: dict = {}
    for sig in SIGNALS:
        feats[f"{sig}_mean_3h"] = tele[sig].iloc[-3:].mean()
        feats[f"{sig}_std_3h"] = tele[sig].iloc[-3:].std()
        feats[f"{sig}_mean_24h"] = tele[sig].iloc[-24:].mean()
        feats[f"{sig}_std_24h"] = tele[sig].iloc[-24:].std()

    err = pd.DataFrame(
        sim_query.all_rows("errors", "sim_errors", ["datetime", "errorID"], machine_id),
        columns=["datetime", "errorID"],
    )
    if not err.empty:
        err["datetime"] = pd.to_datetime(err["datetime"])
    for eid in ERROR_IDS:
        for w in ERROR_WINDOWS_H:
            cutoff = now - pd.Timedelta(hours=w)
            feats[f"{eid}_count_{w}h"] = (
                0 if err.empty else int(((err["errorID"] == eid) & (err["datetime"] > cutoff) & (err["datetime"] <= now)).sum())
            )

    maint = pd.DataFrame(
        sim_query.all_rows("maint", "sim_maint", ["datetime", "comp"], machine_id),
        columns=["datetime", "comp"],
    )
    if not maint.empty:
        maint["datetime"] = pd.to_datetime(maint["datetime"])
    for c in COMPONENTS:
        comp_maint = maint[maint["comp"] == c] if not maint.empty else maint
        feats[f"hours_since_maint_{c}"] = (
            np.nan if comp_maint.empty else (now - comp_maint["datetime"].max()).total_seconds() / 3600
        )

    info = pdm_operations.get_machine_info(machine_id)
    feats["model"] = info.get("model")
    feats["age"] = info.get("age")
    return pd.DataFrame([feats])


def predict_failure_risk(machine_id: int) -> dict:
    row = _build_live_feature_row(machine_id)
    result = {}
    for comp in COMPONENTS:
        bundle = _load_model_bundle(comp)
        clf, feature_cols, categories = bundle["model"], bundle["feature_cols"], bundle["model_categories"]

        x = row.copy()
        x["model"] = pd.Categorical(x["model"], categories=categories)
        x = x[feature_cols]

        proba = float(clf.predict_proba(x)[:, 1][0])
        contrib = clf.predict(x, pred_contrib=True)[0][:-1]
        top_idx = np.argsort(-np.abs(contrib))[:3]
        result[comp] = {
            "probability": round(proba, 4),
            "top_features": [
                {"feature": feature_cols[i], "value": x.iloc[0][feature_cols[i]], "contribution": round(float(contrib[i]), 4)}
                for i in top_idx
            ],
        }
    return result


if __name__ == "__main__":
    import sys
    mid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    for comp, r in predict_failure_risk(mid).items():
        print(f"{comp}: {r['probability']:.2%}")
        for f in r["top_features"]:
            print(f"   {f['feature']} = {f['value']} (기여 {f['contribution']:+.4f})")
