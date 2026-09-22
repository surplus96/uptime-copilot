"""자동 감지 시뮬레이터의 백그라운드 루프. FastAPI lifespan에서 asyncio 태스크로 띄운다.
매 SIM_TICK_SECONDS(기본 60)초마다 SIM_HOURS_PER_TICK(기본 1)시간을 진행하고,
HEALTHY가 아닌 설비 + 이번 틱에 이벤트가 생긴 설비만 증분 스캔한다.
sim_control에 실행 여부/난수 상태를 저장해서 재시작 후에도 같은 seed로 이어간다."""
import asyncio
import base64
import logging
import os
import pickle
import random
import sqlite3
import time
from pathlib import Path

import pandas as pd

import notify
from agent import agent_service
from data import sim_engine, sim_query, sim_store
from data.pdm_telemetry import _baseline

DB_PATH = str(Path(__file__).parent.parent / "store" / "pdm_telemetry.db")

SIM_TICK_SECONDS = int(os.getenv("SIM_TICK_SECONDS", "60"))
SIM_HOURS_PER_TICK = int(os.getenv("SIM_HOURS_PER_TICK", "1"))
SIM_SEED = int(os.getenv("SIM_SEED", "42"))

logger = logging.getLogger(__name__)


def _get_control(key: str, default: str | None = None) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT value FROM sim_control WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def _set_control(key: str, value: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO sim_control VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def _load_rng() -> random.Random:
    raw = _get_control("rng_state")
    rng = random.Random()
    if raw:
        rng.setstate(pickle.loads(base64.b64decode(raw)))
    else:
        rng.seed(SIM_SEED)
    return rng


def _save_rng(rng: random.Random) -> None:
    _set_control("rng_state", base64.b64encode(pickle.dumps(rng.getstate())).decode())


def is_running() -> bool:
    return _get_control("running", "0") == "1"


def start() -> None:
    _set_control("running", "1")
    _set_control("started_at", str(time.time()))



def stop() -> None:
    _set_control("running", "0")


def reset() -> None:
    """SIMULATOR_PLAN.md '클린 상태 보장' - 명시적으로 호출될 때만 지운다."""
    conn = sqlite3.connect(DB_PATH)
    for table in ("sim_telemetry", "sim_errors", "sim_failures", "sim_maint", "sim_state", "sim_control"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM detected_events")
    conn.execute("DELETE FROM completed_events")
    conn.commit()
    conn.close()


def status() -> dict:
    states = sim_store.load_states()
    degrading = [
        {
            "machine_id": m.machine_id, "state": m.state, "signal": m.signal,
            "direction": m.direction, "drift_sigma": round(m.drift_sigma, 2),
            "progress": round(m.elapsed / m.lead_hours, 2) if m.lead_hours else 0.0,
            "errors_emitted": m.errors_emitted,
        }
        for m in states.values() if m.state != "HEALTHY"
    ]
    conn = sqlite3.connect(DB_PATH)
    has_events = conn.execute("SELECT COUNT(*) FROM detected_events").fetchone()[0] > 0
    conn.close()

    running = is_running()
    now = time.time()
    started_at = _get_control("started_at")
    last_tick_at = _get_control("last_tick_at")
    return {
        "running": running,
        "sim_now": sim_query.sim_only_now(),
        "hours_per_tick": SIM_HOURS_PER_TICK,
        "tick_seconds": SIM_TICK_SECONDS,
        "degrading": degrading,
        "has_stale_events": not degrading and has_events,
        "elapsed_seconds": round(now - float(started_at)) if running and started_at else None,
        "seconds_since_last_tick": round(now - float(last_tick_at)) if last_tick_at else None,
    }



def inject(machine_id: int, signal: str | None = None) -> dict:
    """데모용: 특정 설비를 강제로 강한 열화 상태로 만들어 곧 감지되게 한다."""
    rng = _load_rng()
    states = sim_store.load_states()
    m = states.get(machine_id)
    if m is None:
        return {"error": f"machine_id {machine_id} 없음"}
    m.state = "DEGRADING"
    m.signal = signal or rng.choice(sim_engine.SIGNALS)
    m.direction = 1
    m.drift_sigma = sim_engine.STRONG_RANGE[1]
    m.lead_hours = rng.randint(*sim_engine.LEAD_HOURS)
    m.elapsed = 0
    m.errors_emitted = 0
    sim_store.save_states(states)
    _save_rng(rng)
    return {"machine_id": machine_id, "signal": m.signal, "drift_sigma": round(m.drift_sigma, 2)}


def _signal_values(machine_id: int, offsets: dict[str, float], rng: random.Random) -> tuple[float, float, float, float]:
    baseline = _baseline(machine_id)
    values = []
    for sig in sim_engine.SIGNALS:
        mean, std = baseline[sig]
        std = std if std and std > 0 else 1
        values.append(rng.gauss(mean, std) + offsets.get(sig, 0.0) * std)
    return tuple(values)


def _tick_once() -> None:
    rng = _load_rng()
    states = sim_store.load_states()
    sim_last = sim_query.sim_only_now()
    base_ts = pd.Timestamp(sim_last) if sim_last else pd.Timestamp.now().floor("h")

    conn = sqlite3.connect(DB_PATH)
    changed: set[int] = set()
    for h in range(1, SIM_HOURS_PER_TICK + 1):
        ts = base_ts + pd.Timedelta(hours=h)
        for m in states.values():
            was_state = m.state
            r = sim_engine.step(m, rng, ts.hour)
            conn.execute(
                "INSERT INTO sim_telemetry VALUES (?, ?, ?, ?, ?, ?)",
                (str(ts), m.machine_id, *_signal_values(m.machine_id, r["offsets"], rng)),
            )
            for err in r["errors"]:
                conn.execute("INSERT INTO sim_errors VALUES (?, ?, ?)", (str(ts), m.machine_id, err))
            if r["failure"]:
                conn.execute("INSERT INTO sim_failures VALUES (?, ?, ?)", (str(ts), m.machine_id, r["failure"]))
                conn.execute("INSERT INTO sim_maint VALUES (?, ?, ?)", (str(ts), m.machine_id, r["maint"]))
            if r["errors"] or r["failure"] or m.state != was_state:
                changed.add(m.machine_id)
    conn.commit()
    conn.close()

    sim_store.save_states(states)
    _save_rng(rng)

    scan_targets = {m.machine_id for m in states.values() if m.state != "HEALTHY"} | changed
    if scan_targets:
        detected, alerts = agent_service.scan_machines(sorted(scan_targets))
        if alerts:
            notify.send_alert(f"[자동 스캔] 새로 감지된 이상 {len(alerts)}건\n\n" + "\n\n".join(alerts))

    _set_control("last_tick_at", str(time.time()))


async def run_forever() -> None:
    """서버 생애 동안 SIM_TICK_SECONDS마다 한 번씩 틱을 시도한다. running=0이면 그냥 쉰다.
    한 틱이 실패해도 루프 자체는 죽지 않는다(notify.py/cmms_client.py와 같은 원칙)."""
    while True:
        await asyncio.sleep(SIM_TICK_SECONDS)
        if is_running():
            try:
                _tick_once()
            except Exception:
                logger.exception("[시뮬레이터] 틱 실행 중 오류")
