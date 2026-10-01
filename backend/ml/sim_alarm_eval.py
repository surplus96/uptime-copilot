"""
시뮬레이터가 만드는 열화가 실제 위험도 모델의 '주의' 경보(>= RISK_THRESHOLD)로 이어지는지 측정한다.

배경: 시뮬레이터(data/sim_engine.py)의 설계 의도는 열화 -> 주의 -> 긴급(고장 기록) 순서인데,
주의는 모델이 경보를 낼 때만 붙고(agent_service._diagnose_machine), 모델은 원본 데이터로 학습돼서
시뮬레이터의 열화 패턴에 반응한다는 보장이 없다(2026-10-01 브라우저 테스트에서 #46/#84는 주의 없이
바로 긴급이 됐다). 이 스크립트는 "신호별로 열화 에피소드를 N건 만들었을 때, 고장 몇 시간 전에
주의가 뜨는가"를 실제 모델 파일로 잰다 - 서버와 DB를 건드리지 않는다(메모리에서만 시뮬레이션).

실행(모델 파일이 있는 환경, 예: 백엔드 컨테이너 안):
    python -m ml.sim_alarm_eval --episodes 30 --seed 1 --tick-hours 12
"""
import argparse
import random
import sqlite3
import statistics
from collections import defaultdict

import numpy as np
import pandas as pd

from data import pdm_operations, sim_engine
from data.pdm_telemetry import DB_PATH, _baseline
from ml.build_features import COMPONENTS
from ml.predict import _load_model_bundle, build_feature_row

# agent_service.RISK_THRESHOLD와 같은 값 - agent_service는 import 부작용(LLM 클라이언트 생성)이
# 있어서 상수만 복제하고, 어긋나면 test_sim_alarm_eval이 잡는다.
RISK_THRESHOLD = 0.5
ALL_SIGNALS = list(sim_engine.SIGNALS)       # simulate_episode가 엔진의 SIGNALS를 잠깐 바꾸므로 따로 보관
START = pd.Timestamp("2026-10-01 00:00:00")  # 라이브 시뮬레이터와 같은 '10년 뒤' 시각
HISTORY_HOURS = 48                           # 열화 시작 전 정상 이력(오류 48h 창을 채우기 위해)
MAX_EPISODE_HOURS = 120


def _original_maint(machine_id: int) -> pd.DataFrame:
    """라이브 서비스와 같이 원본 정비 기록만 있고 시뮬레이션 정비는 아직 없는 상태."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute('SELECT datetime, comp FROM maint WHERE "machineID"=?', (machine_id,)).fetchall()
    conn.close()
    df = pd.DataFrame(rows, columns=["datetime", "comp"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df


def _with_seeded_history(maint: pd.DataFrame, rng: random.Random, degrading_comp: str | None = None) -> pd.DataFrame:
    """라이브 시뮬레이터와 같이(sim_loop._seed_maintenance_history) 부품마다 시작 전의 마지막 정비를 한 건씩
    더한다. 부품들이 같은 정비 사건을 공유하는 구조(sim_engine.draw_initial_ages)를 따르고, 열화하는 부품은
    엔진의 시작 하한(START_MIN_AGE_H) 이상이다 - 라이브에서는 어린 부품이 열화를 시작하지 않는다."""
    floor = sim_engine.START_MIN_AGE_H[degrading_comp] if degrading_comp else 0.0
    ages = sim_engine.draw_initial_ages(rng)
    for _ in range(1000):
        if ages[degrading_comp or "comp1"] >= floor:
            break
        ages = sim_engine.draw_initial_ages(rng)
    seeded = pd.DataFrame([(START - pd.Timedelta(hours=ages[c]), c) for c in COMPONENTS], columns=["datetime", "comp"])
    return pd.concat([maint, seeded], ignore_index=True)


def _telemetry_row(machine_id: int, offsets: dict[str, float], rng: random.Random) -> list[float]:
    baseline = _baseline(machine_id)
    out = []
    for sig in ALL_SIGNALS:
        mean, std = baseline[sig]
        std = std if std and std > 0 else 1
        out.append(rng.gauss(mean, std) + offsets.get(sig, 0.0) * std)
    return out


def simulate_episode(
    machine_id: int, signal: str, rng: random.Random, maint_hours: float | None = None, seed_maint: bool = True
) -> dict:
    """열화 에피소드 하나를 고장 기록까지 메모리에서 돌리고, 시각별 피처 행을 모은다."""
    machine = sim_engine.MachineSim(machine_id)
    sim_engine.SIGNALS = [signal]  # 열화가 시작될 때 이 신호가 뽑히게 한다(방향·크기도 엔진이 신호별로 정한다)
    try:
        return _run_episode(machine, machine_id, signal, rng, maint_hours, seed_maint)
    finally:
        sim_engine.SIGNALS = ALL_SIGNALS


def _run_episode(
    machine: sim_engine.MachineSim, machine_id: int, signal: str, rng: random.Random, maint_hours: float | None, seed_maint: bool
) -> dict:
    tele_rows: list[list] = []
    err_rows: list[tuple] = []
    for h in range(HISTORY_HOURS):
        ts = START + pd.Timedelta(hours=h)
        tele_rows.append([ts, *_telemetry_row(machine_id, {}, rng)])

    maint = _original_maint(machine_id)
    if seed_maint:
        maint = _with_seeded_history(maint, rng, sim_engine.SIGNAL_TO_COMPONENT[signal])
    info = pdm_operations.get_machine_info(machine_id)
    feature_rows = []
    row_times: list[pd.Timestamp] = []
    failure_ts = None
    precursor_errors = 0
    for h in range(HISTORY_HOURS, HISTORY_HOURS + MAX_EPISODE_HOURS):
        ts = START + pd.Timedelta(hours=h)
        r = sim_engine.step(machine, rng, ts.hour)
        tele_rows.append([ts, *_telemetry_row(machine_id, r["offsets"], rng)])
        for e in r["errors"]:
            err_rows.append((ts, e))
            precursor_errors += 1
        if r["failure"]:
            failure_ts = ts
            break
        if machine.state == "HEALTHY":
            continue  # P_EPISODE=1.0이라 보통 첫 시간에 시작한다
        tele = pd.DataFrame(tele_rows[-30:], columns=["datetime", *ALL_SIGNALS])
        err = pd.DataFrame(err_rows, columns=["datetime", "errorID"])
        row = build_feature_row(ts, tele, err, maint, info)
        if maint_hours is not None:  # 진단용: "마지막 정비 후 경과시간"을 학습 범위 안의 값으로 고정
            for c in COMPONENTS:
                row[f"hours_since_maint_{c}"] = maint_hours
        feature_rows.append(row)
        row_times.append(ts)
    return {
        "machine_id": machine_id, "features": feature_rows, "times": row_times,
        "failure_ts": failure_ts, "precursor_errors": precursor_errors,
    }


def _risk_detail(ep: dict) -> tuple[list[float], list[str]]:
    """에피소드의 각 시각에서 4개 부품 모델의 최대 위험 점수와, 그 점수를 낸 부품."""
    if not ep["features"]:
        return [], []
    frame = pd.concat(ep["features"], ignore_index=True)
    probas = []
    for comp in COMPONENTS:
        bundle = _load_model_bundle(comp)
        x = frame.copy()
        x["model"] = pd.Categorical(x["model"], categories=bundle["model_categories"])
        probas.append(bundle["model"].predict_proba(x[bundle["feature_cols"]])[:, 1])
    stacked = np.vstack(probas)
    return [float(v) for v in stacked.max(axis=0)], [COMPONENTS[i] for i in stacked.argmax(axis=0)]


def _risk_by_time(ep: dict) -> list[float]:
    return _risk_detail(ep)[0]


def first_alarm_component(ep: dict) -> str | None:
    """고장 전 처음 경보가 뜬 시각에 가장 높은 점수를 낸 부품 - 경보가 엉뚱한 부품을 가리키면
    작업지시서가 다른 부품 블록을 만든다."""
    risks, comps = _risk_detail(ep)
    for p, comp in zip(risks, comps):
        if p >= RISK_THRESHOLD:
            return comp
    return None


def lead_hours(ep: dict, tick_hours: int = 1) -> float | None:
    """고장 기록 전에 처음 경보가 뜬 시각부터 고장까지 몇 시간인가. 경보가 없으면 None.
    tick_hours > 1이면 스캔이 그 간격으로만 도는 것으로 본다(라이브 시뮬레이터의 틱)."""
    if ep["failure_ts"] is None:
        return None
    risks = _risk_by_time(ep)
    for ts, p in zip(ep["times"], risks):
        hours_from_start = int((ts - START).total_seconds() // 3600)
        if hours_from_start % tick_hours:
            continue
        if p >= RISK_THRESHOLD:
            return (ep["failure_ts"] - ts).total_seconds() / 3600
    return None


def evaluate(
    episodes: int, seed: int, tick_hours: int, maint_hours: float | None = None, seed_maint: bool = True
) -> dict[str, dict]:
    rng = random.Random(seed)
    saved_p_episode = sim_engine.P_EPISODE
    sim_engine.P_EPISODE = 1.0  # 첫 시간에 바로 열화 시작 - 신호는 아래에서 고정한다
    try:
        return _evaluate(episodes, rng, tick_hours, maint_hours, seed_maint)
    finally:
        sim_engine.P_EPISODE = saved_p_episode


def _evaluate(
    episodes: int, rng: random.Random, tick_hours: int, maint_hours: float | None, seed_maint: bool
) -> dict[str, dict]:
    results: dict[str, dict] = defaultdict(lambda: {"leads_1h": [], "leads_tick": [], "errors": [], "right_part": [], "n": 0})
    for signal in ALL_SIGNALS:
        for i in range(episodes):
            machine_id = 1 + (i * 7 + ALL_SIGNALS.index(signal) * 13) % 100
            ep = simulate_episode(machine_id, signal, rng, maint_hours, seed_maint)
            if ep["failure_ts"] is None:
                continue
            res = results[signal]
            res["n"] += 1
            res["errors"].append(ep["precursor_errors"])
            res["leads_1h"].append(lead_hours(ep, 1))
            res["leads_tick"].append(lead_hours(ep, tick_hours))
            first = first_alarm_component(ep)
            if first is not None:
                res["right_part"].append(first == sim_engine.SIGNAL_TO_COMPONENT[signal])
    return results


def _pct(values: list, pred) -> float:
    return 100.0 * sum(1 for v in values if pred(v)) / len(values) if values else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=30, help="신호별 에피소드 수")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--tick-hours", type=int, default=12, help="스캔 간격(시뮬레이션 시간) - 라이브 틱 크기")
    parser.add_argument(
        "--maint-hours", type=float, default=None,
        help="진단용: 마지막 정비 후 경과시간 피처를 이 값으로 고정(기본: 라이브와 같이 원본 정비 기록 기준)",
    )
    parser.add_argument(
        "--no-maint-history", action="store_true",
        help="라이브 시뮬레이터가 정비 이력을 심기 전(원본 정비 기록만, 경과시간 약 9만 시간) 상태로 잰다",
    )
    args = parser.parse_args()

    results = evaluate(args.episodes, args.seed, args.tick_hours, args.maint_hours, not args.no_maint_history)
    history = "정비 이력 없음(약 9만 시간)" if args.no_maint_history else "심은 정비 이력"
    if args.maint_hours is not None:
        history = f"정비 후 경과시간 {args.maint_hours:g}시간 고정"
    print(f"에피소드 신호별 {args.episodes}건, seed={args.seed}, {history}, 경보 기준 위험 점수 >= {RISK_THRESHOLD}")
    print(f"{'신호':<10}{'n':>4}{'전조오류0건':>12}{'경보(매시간)':>14}{'>=6h전':>8}"
          f"{'경보(' + str(args.tick_hours) + 'h틱)':>12}{'>=6h전':>8}{'중앙 선행(h)':>14}{'부품 일치':>10}")
    for signal, res in results.items():
        l1, lt = res["leads_1h"], res["leads_tick"]
        found = [v for v in l1 if v is not None]
        print(
            f"{signal:<10}{res['n']:>4}"
            f"{_pct(res['errors'], lambda v: v == 0):>11.0f}%"
            f"{_pct(l1, lambda v: v is not None):>13.0f}%"
            f"{_pct(l1, lambda v: v is not None and v >= 6):>7.0f}%"
            f"{_pct(lt, lambda v: v is not None):>11.0f}%"
            f"{_pct(lt, lambda v: v is not None and v >= 6):>7.0f}%"
            f"{(statistics.median(found) if found else float('nan')):>14.1f}"
            f"{_pct(res['right_part'], bool):>9.0f}%"
        )


if __name__ == "__main__":
    main()
