"""
고장 위험 예측 모델용 피처 테이블 생성 - 1회 실행용 배치 스크립트
(pdm_dataloader.py와 같은 성격). 3시간 간격 샘플 x 설비별로 계산해서
backend/store/ml_features/machine_{id}.parquet에 캐시한다 - 전체를 한 번에
메모리에 올리지 않고, 재실행 시 캐시를 그대로 읽어 결정론적으로 동일한
결과를 낸다 (uptime-update-plan.md 3-2 DoD).

오류-횟수 피처는 24h+48h 이중 창을 쓴다: docs/model_exploration.md에서
실측한 결과, 선행 오류는 고장 24~36시간 전에 몰려 있어(98%) 24h 창 하나로는
가장 강한 신호를 거의 못 본다 (docs/decisions.md 2026-09-23 항목 참고).
"""
from pathlib import Path

import numpy as np
import pandas as pd

ARCHIVE_DIR = Path(__file__).parent.parent.parent / "archive"
CACHE_DIR = Path(__file__).parent.parent / "store" / "ml_features"

SIGNALS = ["volt", "rotate", "pressure", "vibration"]
ERROR_IDS = ["error1", "error2", "error3", "error4", "error5"]
ERROR_WINDOWS_H = (24, 48)
COMPONENTS = ["comp1", "comp2", "comp3", "comp4"]
LABEL_WINDOW_H = 24
SAMPLE_INTERVAL_H = 3
_NAT_NS = np.datetime64("NaT", "ns")


def _load_raw():
    return (
        pd.read_csv(ARCHIVE_DIR / "PdM_telemetry.csv", parse_dates=["datetime"]),
        pd.read_csv(ARCHIVE_DIR / "PdM_errors.csv", parse_dates=["datetime"]),
        pd.read_csv(ARCHIVE_DIR / "PdM_maint.csv", parse_dates=["datetime"]),
        pd.read_csv(ARCHIVE_DIR / "PdM_failures.csv", parse_dates=["datetime"]),
        pd.read_csv(ARCHIVE_DIR / "PdM_machines.csv"),
    )


def build_machine_features(machine_id: int, telemetry, errors, maint, failures, machines) -> pd.DataFrame:
    t = telemetry[telemetry["machineID"] == machine_id].sort_values("datetime").set_index("datetime")
    e = errors[errors["machineID"] == machine_id].sort_values("datetime")
    m = maint[maint["machineID"] == machine_id].sort_values("datetime")
    f = failures[failures["machineID"] == machine_id].sort_values("datetime")

    roll = {}
    for sig in SIGNALS:
        roll[f"{sig}_mean_3h"] = t[sig].rolling("3h").mean()
        roll[f"{sig}_std_3h"] = t[sig].rolling("3h").std()
        roll[f"{sig}_mean_24h"] = t[sig].rolling("24h").mean()
        roll[f"{sig}_std_24h"] = t[sig].rolling("24h").std()
    out = pd.DataFrame(roll, index=t.index).iloc[::SAMPLE_INTERVAL_H].copy()
    # CP-U1 교차 검토(2026-09-23, docs/decisions.md) 지적: 설비 관측 시작 직후엔 오류
    # 48h 창이 덜 찬 채로 값이 나오고(NaN이 아니라 0에 가깝게), 관측 끝 직전엔 라벨의
    # 24h 창이 데이터 밖으로 나간다 - 둘 다 잘라낸다.
    start_ok = t.index[0] + pd.Timedelta(hours=max(ERROR_WINDOWS_H))
    end_ok = t.index[-1] - pd.Timedelta(hours=LABEL_WINDOW_H)
    out = out[(out.index >= start_ok) & (out.index <= end_ok)]

    # 아래 오류/정비 피처는 전부 (T-w, T] 구간 - T 시점 자체도 포함한다.
    for eid in ERROR_IDS:
        arr = e.loc[e["errorID"] == eid, "datetime"].values
        for w in ERROR_WINDOWS_H:
            lo = out.index.values - np.timedelta64(w, "h")
            hi = out.index.values
            counts = np.searchsorted(arr, hi, side="right") - np.searchsorted(arr, lo, side="right")
            out[f"{eid}_count_{w}h"] = counts.astype(np.int16)

    for c in COMPONENTS:
        arr = m.loc[m["comp"] == c, "datetime"].values
        if len(arr) == 0:
            out[f"hours_since_maint_{c}"] = np.float32(np.nan)
            continue
        idx = np.searchsorted(arr, out.index.values, side="right") - 1
        last_maint = np.where(idx >= 0, arr[np.clip(idx, 0, len(arr) - 1)], _NAT_NS)
        out[f"hours_since_maint_{c}"] = (out.index.values - last_maint) / np.timedelta64(1, "h")

    for c in COMPONENTS:
        arr = f.loc[f["failure"] == c, "datetime"].values
        lo = out.index.values
        hi = out.index.values + np.timedelta64(LABEL_WINDOW_H, "h")
        will_fail = (np.searchsorted(arr, hi, side="right") - np.searchsorted(arr, lo, side="right")) > 0
        out[f"label_fail_{c}_{LABEL_WINDOW_H}h"] = will_fail.astype(np.int8)

    info = machines[machines["machineID"] == machine_id].iloc[0]
    out["model"] = info["model"]
    out["age"] = np.int16(info["age"])
    out["machineID"] = np.int16(machine_id)

    out = out.reset_index().rename(columns={"index": "datetime"})
    float_cols = out.select_dtypes(include=["float64"]).columns
    out[float_cols] = out[float_cols].astype(np.float32)
    return out


def load_or_build_features(machine_id: int, *, force: bool = False) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"machine_{machine_id}.parquet"
    if cache_path.exists() and not force:
        return pd.read_parquet(cache_path)
    telemetry, errors, maint, failures, machines = _load_raw()
    df = build_machine_features(machine_id, telemetry, errors, maint, failures, machines)
    df.to_parquet(cache_path, index=False)
    return df


def load_all_features(*, force: bool = False) -> pd.DataFrame:
    """캐시된 100대 전체를 하나로 합친다 - 3-3(학습)에서 이걸 씁니다."""
    return pd.concat([load_or_build_features(mid, force=force) for mid in range(1, 101)], ignore_index=True)


if __name__ == "__main__":
    all_df = load_all_features()
    print(f"전체 피처 테이블: {all_df.shape}, 메모리 {all_df.memory_usage(deep=True).sum() / 1024**2:.1f}MB")
    for c in COMPONENTS:
        col = f"label_fail_{c}_{LABEL_WINDOW_H}h"
        print(f"  {c}: 양성 {all_df[col].sum()}건 ({all_df[col].mean():.2%})")
