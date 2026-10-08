"""
predict_failure_risk(machine_id) - 학습된 부품별 모델로 "지금" 기준 24h 내 고장
확률과, 그 확률에 가장 크게 기여한 피처 상위 3개를 낸다. train.py가 저장해 둔
모델(backend/store/ml_models/model_{comp}.pkl)을 그대로 불러와서 쓴다 - 재학습 없음.

라이브 피처 행은 build_features.py의 학습용 피처와 정의를 그대로 따르되,
원본 CSV 대신 sim_query(원본+시뮬레이션 합쳐 조회)로 "지금" 시점 값을 계산한다.
"""
import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from data import event_store, pdm_operations, sim_query
from data.pdm_telemetry import TELEMETRY_COLUMNS
from ml.build_features import COMPONENTS, ERROR_IDS, ERROR_WINDOWS_H, SIGNALS

logger = logging.getLogger(__name__)
MODEL_DIR = Path(__file__).parent.parent / "store" / "ml_models"


def _load_model_bundle(comp: str) -> dict:
    path = MODEL_DIR / f"model_{comp}.pkl"
    if not path.exists():
        raise FileNotFoundError(f"{comp} 모델이 없습니다 - 먼저 `python -m ml.train`을 실행하세요: {path}")
    return joblib.load(path)


def build_feature_row(
    now: pd.Timestamp, tele: pd.DataFrame, err: pd.DataFrame, maint: pd.DataFrame, info: dict
) -> pd.DataFrame:
    """DB 조회와 분리한 순수 계산부 - 실제 서비스(_build_live_feature_row)와 시뮬레이터
    보정 측정(ml/sim_alarm_eval.py)이 같은 피처 정의를 쓰게 한다. tele/err/maint의
    datetime 열은 이미 Timestamp여야 한다."""
    feats: dict = {}
    for sig in SIGNALS:
        feats[f"{sig}_mean_3h"] = tele[sig].iloc[-3:].mean()
        feats[f"{sig}_std_3h"] = tele[sig].iloc[-3:].std()
        feats[f"{sig}_mean_24h"] = tele[sig].iloc[-24:].mean()
        feats[f"{sig}_std_24h"] = tele[sig].iloc[-24:].std()

    for eid in ERROR_IDS:
        for w in ERROR_WINDOWS_H:
            cutoff = now - pd.Timedelta(hours=w)
            feats[f"{eid}_count_{w}h"] = (
                0 if err.empty else int(((err["errorID"] == eid) & (err["datetime"] > cutoff) & (err["datetime"] <= now)).sum())
            )

    for c in COMPONENTS:
        comp_maint = maint[maint["comp"] == c] if not maint.empty else maint
        feats[f"hours_since_maint_{c}"] = (
            np.nan if comp_maint.empty else (now - comp_maint["datetime"].max()).total_seconds() / 3600
        )

    feats["model"] = info.get("model")
    feats["age"] = info.get("age")
    return pd.DataFrame([feats])


def _build_live_feature_row(machine_id: int) -> pd.DataFrame:
    now = pd.Timestamp(sim_query.dataset_now())

    tele = pd.DataFrame(
        sim_query.recent_rows("telemetry", "sim_telemetry", TELEMETRY_COLUMNS, machine_id, 30),
        columns=TELEMETRY_COLUMNS,
    )
    tele["datetime"] = pd.to_datetime(tele["datetime"])

    err = pd.DataFrame(
        sim_query.all_rows("errors", "sim_errors", ["datetime", "errorID"], machine_id),
        columns=["datetime", "errorID"],
    )
    if not err.empty:
        err["datetime"] = pd.to_datetime(err["datetime"])
        cutoffs = [t for t in (sim_query.incident_start(), event_store.completed_evidence_at(machine_id)) if t]
        if cutoffs:
            err = err[err["datetime"] > max(pd.Timestamp(t) for t in cutoffs)]

    maint = pd.DataFrame(
        sim_query.all_rows("maint", "sim_maint", ["datetime", "comp"], machine_id),
        columns=["datetime", "comp"],
    )
    if not maint.empty:
        maint["datetime"] = pd.to_datetime(maint["datetime"])
    if sim_query.runtime_year():
        simulated = pd.DataFrame(sim_query.latest_sim_maintenance(machine_id), columns=["datetime", "comp"])
        if not simulated.empty:
            simulated["datetime"] = pd.to_datetime(simulated["datetime"])
            # Shifted bootstrap maintenance must not override injected simulation ages.
            maint = pd.concat([maint[~maint["comp"].isin(simulated["comp"])], simulated], ignore_index=True)

    return build_feature_row(now, tele, err, maint, pdm_operations.get_machine_info(machine_id))


def predict_failure_risk(machine_id: int) -> dict:
    row = _build_live_feature_row(machine_id)
    # 정비 기록이 없는 부품의 "마지막 정비 후 경과시간"은 결측(NaN)인데, 학습 데이터에는 이 피처의 결측이 한 건도
    # 없어서(모든 설비·부품이 2015-01-03 이전에 정비 기록이 있다) 모델은 NaN을 0("방금 정비함")처럼 다룬다.
    # 실제 고장 121건(시험 구간)을 재생하면 경보율이 99.2% -> 43%로 떨어졌고 0으로 넣은 것과 같았다(2026-10-02).
    # 조용히 점수가 낮아지므로 반드시 로그로 알린다 - docs/INTEGRATION_CONTRACT.md 2절.
    missing = [
        c for c in COMPONENTS
        if f"hours_since_maint_{c}" in row.columns and pd.isna(row[f"hours_since_maint_{c}"].iloc[0])
    ]
    if missing:
        logger.warning(
            "설비 #%s: %s의 정비 기록이 없어 '마지막 정비 후 경과시간'이 결측입니다 - 모델은 이를 '방금 정비함'으로 "
            "다뤄 위험 점수가 크게 낮아질 수 있습니다(정비 기록 입력 필요)", machine_id, ", ".join(missing),
        )
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
