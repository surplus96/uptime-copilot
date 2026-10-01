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
import threading
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

# _tick_once()(백그라운드 스레드)와 inject()/reset()(FastAPI 워커 스레드)가 같은
# sim_state를 동시에 읽고-고치고-쓸 수 있다 - 둘 다 전체 100행을 한 번에 덮어쓰므로
# 늦게 끝나는 쪽이 상대방의 변경을 통째로 지운다(2026-09-22 security-reviewer 지적:
# inject()로 강제 열화시켜도 마침 그때 도는 틱이 조용히 되돌려놓을 수 있었다).
_LOCK = threading.Lock()


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


def _set_controls(values: dict[str, str]) -> None:
    """여러 제어값을 한 트랜잭션으로 쓴다 - 오류 상태의 두 키(last_error,
    consecutive_failures)를 따로 쓰다가 사이에서 취소/실패하면 한쪽만 남아 어긋났다
    (2026-09-30 code-quality-reviewer, 실행으로 확인)."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executemany("INSERT OR REPLACE INTO sim_control VALUES (?, ?)", list(values.items()))
        conn.commit()
    finally:
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
    """docs/design/SIMULATOR_PLAN.md '클린 상태 보장' - 명시적으로 호출될 때만 지운다."""
    with _LOCK:
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
        "last_error": _get_control("last_error") or None,
        "consecutive_failures": int(_get_control("consecutive_failures", "0") or "0"),
    }



def _age_component_for_injection(machine_id: int, comp: str) -> None:
    """주입한 열화가 모델에 보이려면 그 부품이 어리면 안 된다(START_MIN_AGE_H) - 마지막 정비가 더 최근이면
    그 부품의 정비 기록을 같은 만큼 과거로 민다. **가장 최근 한 건만 옮기면 안 된다**: 하한보다 어린 정비가
    둘 이상이면(부품당 연 약 7회라 흔하다) 다음으로 최근인 기록이 새로 마지막 정비가 돼서 여전히 어리다
    (test-engineer가 임시 DB로 재현: now-10h와 now-300h 두 건 -> 주입 후에도 나이 300h). 그래서 하한보다 어린
    기록을 전부 같은 만큼 민다(간격 유지). 데모 주입은 "이 부품은 노후했다"는 가정이고, 밀린 기록은 고장 기록의
    시각과 어긋날 수 있다(화면에는 영향 없는 이력상의 불일치). 정비 기록이 아직 없으면(첫 틱 전) 첫 틱의 심기가
    같은 하한을 지킨다."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            'SELECT rowid, datetime FROM sim_maint WHERE "machineID"=? AND comp=? ORDER BY datetime DESC', (machine_id, comp),
        ).fetchall()
        now_raw = sim_query.sim_only_now()
        if not rows or now_raw is None:
            return
        now = pd.Timestamp(now_raw)
        floor = sim_engine.START_MIN_AGE_H[comp]
        newest_age = (now - pd.Timestamp(rows[0][1])).total_seconds() / 3600
        if newest_age >= floor:
            return
        target = _draw_machine_ages(f"{SIM_SEED}:inject:{machine_id}:{now}", {comp: floor})[comp]
        shift = pd.Timedelta(hours=target - newest_age)
        for rowid, dt in rows:
            if (now - pd.Timestamp(dt)).total_seconds() / 3600 < floor:
                conn.execute("UPDATE sim_maint SET datetime=? WHERE rowid=?", (str((pd.Timestamp(dt) - shift).floor("h")), rowid))
        conn.commit()
    finally:
        conn.close()


def inject(machine_id: int, signal: str | None = None) -> dict:
    """데모용: 특정 설비를 강제로 강한 열화 상태로 만들어 곧 감지되게 한다."""
    with _LOCK:
        rng = _load_rng()
        states = sim_store.load_states()
        m = states.get(machine_id)
        if m is None:
            return {"error": f"machine_id {machine_id} 없음"}
        m.state = "DEGRADING"
        m.signal = signal or rng.choice(sim_engine.SIGNALS)
        m.direction = sim_engine.SIGNAL_DIRECTION[m.signal]
        m.drift_sigma = sim_engine.STRONG_RANGE[1]
        m.lead_hours = rng.randint(*sim_engine.LEAD_HOURS)
        m.elapsed = 0
        m.errors_emitted = 0
        sim_store.save_states(states)
        _save_rng(rng)
        _age_component_for_injection(machine_id, sim_engine.SIGNAL_TO_COMPONENT[m.signal])
        return {"machine_id": machine_id, "signal": m.signal, "drift_sigma": round(m.drift_sigma, 2)}


def _signal_values(machine_id: int, offsets: dict[str, float], rng: random.Random) -> tuple[float, ...]:
    baseline = _baseline(machine_id)
    values = []
    for sig in sim_engine.SIGNALS:
        mean, std = baseline[sig]
        std = std if std and std > 0 else 1
        values.append(rng.gauss(mean, std) + offsets.get(sig, 0.0) * std)
    return tuple(values)


def _draw_machine_ages(seed_key: str, floor: dict[str, float] | None = None) -> dict[str, float]:
    """(seed, 설비)에서 재현 가능하게 뽑은 부품별 정비 후 경과시간(정비 사건 과정을 거슬러 올라가며 - 부품들이
    같은 사건을 공유한다). floor(부품 -> 최소 나이)를 주면 그 조건을 만족할 때까지 같은 난수열로 다시 뽑는다
    (최대 200번, 그래도 안 되면 만족하지 못하는 부품을 하한으로 올린다)."""
    pick = random.Random(seed_key)
    ages = sim_engine.draw_initial_ages(pick)
    for _ in range(200):
        if not floor or all(ages[c] >= f for c, f in floor.items()):
            return ages
        ages = sim_engine.draw_initial_ages(pick)
    return {c: max(a, (floor or {}).get(c, 0.0)) for c, a in ages.items()}


def _seed_maintenance_history(conn: sqlite3.Connection, base_ts: pd.Timestamp, machine_ids, states=None) -> None:
    """시뮬레이션 정비 기록이 아직 없는 (설비, 부품)마다 "시작 전의 마지막 정비"를 한 건씩 심는다.
    위험도 모델이 쓰는 "마지막 정비 후 경과시간" 피처가 학습 범위 안에 들어오게 하고, 부품들이 같은 정비
    사건을 공유하는 실제 구조를 따른다(sim_engine.draw_initial_ages). 이미 정비 기록(고장 때 생긴 것 포함)이
    있는 쌍은 건드리지 않으므로 몇 번을 불러도, 이미 진행 중인 DB에서 불러도 안전하다. 난수는 (seed, 설비)마다
    따로 만들어서 재현 가능하고 틱의 난수열(rng)을 쓰지 않는다 - 이 기능을 켜고 꺼도 열화·오류의 난수열이
    달라지지 않는다. states를 주면, 이미 열화 중인(주입된) 설비의 그 신호 부품은 부품별 시작 하한
    (START_MIN_AGE_H) 이상으로만 심는다 - 어린 부품은 열화를 시작하지 않는다."""
    have = {(row[0], row[1]) for row in conn.execute('SELECT DISTINCT "machineID", comp FROM sim_maint')}
    for machine_id in machine_ids:
        if all((machine_id, comp) in have for comp in sim_engine.COMPONENTS):
            continue
        floor = None
        if states is not None and machine_id in states and states[machine_id].state != "HEALTHY":
            comp = sim_engine.SIGNAL_TO_COMPONENT.get(states[machine_id].signal or "")
            if comp:
                floor = {comp: sim_engine.START_MIN_AGE_H[comp]}
        ages = _draw_machine_ages(f"{SIM_SEED}:maint:{machine_id}", floor)
        for comp in sim_engine.COMPONENTS:
            if (machine_id, comp) in have:
                continue
            ts = (base_ts - pd.Timedelta(hours=ages[comp])).floor("h")
            conn.execute('INSERT INTO sim_maint ("datetime", "machineID", "comp") VALUES (?, ?, ?)', (str(ts), machine_id, comp))


def _load_last_maint(conn: sqlite3.Connection) -> dict[tuple[int, str], pd.Timestamp]:
    rows = conn.execute('SELECT "machineID", comp, MAX(datetime) FROM sim_maint GROUP BY "machineID", comp').fetchall()
    return {(machine_id, comp): pd.Timestamp(dt) for machine_id, comp, dt in rows}


def _comp_ages(last_maint: dict[tuple[int, str], pd.Timestamp], machine_id: int, ts: pd.Timestamp) -> dict[str, float]:
    """설비의 부품별 마지막 정비 후 경과시간(시간). 정비 기록이 없는 부품은 빠진다(= 제한 없음)."""
    return {
        comp: (ts - last_maint[(machine_id, comp)]).total_seconds() / 3600
        for comp in sim_engine.COMPONENTS
        if (machine_id, comp) in last_maint
    }


def _preventive_maintenance(
    conn: sqlite3.Connection, ts: pd.Timestamp, states: dict, last_maint: dict[tuple[int, str], pd.Timestamp]
) -> None:
    """정상(HEALTHY) 설비에 정기 정비 사건을 일으킨다 - 실제 정비 기록의 순수 정기 정비(설비당 연 14.6회)이고,
    사건 하나가 부품 1개(72%) 또는 2개(28%)를 함께 정비해 나이를 0으로 되돌린다. 안 하면 시뮬레이션이 길어질수록
    모든 부품이 열화 가능한 나이가 돼서 고장률이 실제(연 7.6회)보다 계속 올라간다. 열화·전조 상태의 설비는
    건너뛴다(정비하면 고장이 안 날 텐데 그 효과는 모델링하지 않는다). 난수는 틱의 rng를 쓰지 않고
    (seed, 시각)마다 따로 만든다 - 재현 가능하고, 이 기능이 열화·오류의 난수열을 바꾸지 않는다. 설비마다 난수를
    항상 같은 개수 쓴다(상태가 달라도 다른 설비의 결과가 안 흔들리게)."""
    draws = random.Random(f"{SIM_SEED}:pm:{ts}")
    for machine_id in sorted(states):
        hit = draws.random() < sim_engine.P_PM_EVENT
        comps = sim_engine.pick_event_comps(draws, sim_engine.PM_TWO_COMP_PROB)
        if hit and states[machine_id].state == "HEALTHY":
            for comp in comps:
                conn.execute('INSERT INTO sim_maint ("datetime", "machineID", "comp") VALUES (?, ?, ?)', (str(ts), machine_id, comp))
                last_maint[(machine_id, comp)] = ts


def _failure_companion(machine_id: int, ts: pd.Timestamp, failed_comp: str) -> str | None:
    """고장 사건이 고장난 부품 말고 함께 정비하는 다른 부품(없으면 None) - 실제 고장 사건의 46%가 그렇다.
    (seed, 시각, 설비)에서 재현 가능하게 정한다."""
    pick = random.Random(f"{SIM_SEED}:fx:{ts}:{machine_id}")
    if pick.random() >= sim_engine.FAILURE_EXTRA_COMP_PROB:
        return None
    return sim_engine.pick_event_comps(pick, 0.0, exclude=failed_comp)[0]


def _tick_once() -> None:
    changed: set[int] = set()
    with _LOCK:
        # 상태·난수를 락 안에서 읽는다 - 락 밖에서 읽고 락을 잡으면, 그 사이에 들어온 inject()/reset()의
        # 변경을 틱이 낡은 사본으로 덮어써서 화면은 "주입 성공"인데 설비가 열화하지 않는다
        # (interface-reviewer가 지적, 2026-10-01).
        rng = _load_rng()
        states = sim_store.load_states()
        sim_last = sim_query.sim_only_now()
        base_ts = pd.Timestamp(sim_last) if sim_last else pd.Timestamp.now().floor("h")
        conn = sqlite3.connect(DB_PATH)
        try:
            _seed_maintenance_history(conn, base_ts, states.keys(), states)
            last_maint = _load_last_maint(conn)
            for h in range(1, SIM_HOURS_PER_TICK + 1):
                ts = base_ts + pd.Timedelta(hours=h)
                _preventive_maintenance(conn, ts, states, last_maint)
                for m in states.values():
                    was_state = m.state
                    # 열화를 시작할 수 있는 상태(HEALTHY)일 때만 부품 나이가 쓰인다
                    ages = _comp_ages(last_maint, m.machine_id, ts) if m.state == "HEALTHY" else None
                    r = sim_engine.step(m, rng, ts.hour, comp_ages=ages)
                    conn.execute(
                        'INSERT INTO sim_telemetry ("datetime", "machineID", volt, rotate, pressure, vibration) '
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (str(ts), m.machine_id, *_signal_values(m.machine_id, r["offsets"], rng)),
                    )
                    for err in r["errors"]:
                        conn.execute(
                            'INSERT INTO sim_errors ("datetime", "machineID", "errorID") VALUES (?, ?, ?)',
                            (str(ts), m.machine_id, err),
                        )
                    if r["failure"]:
                        conn.execute(
                            'INSERT INTO sim_failures ("datetime", "machineID", "failure") VALUES (?, ?, ?)',
                            (str(ts), m.machine_id, r["failure"]),
                        )
                        conn.execute(
                            'INSERT INTO sim_maint ("datetime", "machineID", "comp") VALUES (?, ?, ?)',
                            (str(ts), m.machine_id, r["maint"]),
                        )
                        last_maint[(m.machine_id, r["maint"])] = ts
                        companion = _failure_companion(m.machine_id, ts, r["maint"])
                        if companion:
                            conn.execute(
                                'INSERT INTO sim_maint ("datetime", "machineID", "comp") VALUES (?, ?, ?)',
                                (str(ts), m.machine_id, companion),
                            )
                            last_maint[(m.machine_id, companion)] = ts
                    if r["errors"] or r["failure"] or m.state != was_state:
                        changed.add(m.machine_id)

            # 상태/난수/마지막 틱 시각까지 전부 같은 트랜잭션에 묶는다 - 텔레메트리는
            # 기록됐는데 상태 저장 직전에 죽어서 다음 재시작 때 그 시간이 중복 진행되는
            # 불일치를 막는다(2026-09-22 code-quality-reviewer 지적).
            sim_store.save_states(states, conn=conn)
            conn.execute(
                "INSERT OR REPLACE INTO sim_control VALUES (?, ?)",
                ("rng_state", base64.b64encode(pickle.dumps(rng.getstate())).decode()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO sim_control VALUES (?, ?)",
                ("last_tick_at", str(time.time())),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    scan_targets = {m.machine_id for m in states.values() if m.state != "HEALTHY"} | changed
    if scan_targets:
        detected, alerts = agent_service.scan_machines(sorted(scan_targets))
        if alerts:
            notify.send_alert(f"[자동 스캔] 새로 감지된 이상 {len(alerts)}건\n\n" + "\n\n".join(alerts))


async def run_forever() -> None:
    """서버 생애 동안 SIM_TICK_SECONDS마다 한 번씩 틱을 시도한다. running=0이면 그냥 쉰다.
    한 틱이 실패해도 루프 자체는 죽지 않는다(notify.py/cmms_client.py와 같은 원칙) -
    단, is_running() 자체가 DB 오류로 실패할 수도 있으므로 그것도 try 안에 넣는다
    (2026-09-22 code-quality-reviewer 지적: try 밖에 있으면 그 실패는 루프를 영구
    정지시키는데 아무 데도 안 남는다). 연속 실패가 쌓이면 last_error에 기록해서
    /simulator/status로 드러나게 한다."""
    consecutive = 0
    # 서버가 다시 뜨면 이전 실행의 오류 상태는 의미가 없다 - 메모리 카운터는 0에서 시작하는데
    # DB에는 옛 값이 남아 있어서, 정상 틱이 이어져도 화면의 "N회 연속 실패" 배너가 안 지워졌다.
    try:
        await asyncio.to_thread(_set_controls, {"last_error": "", "consecutive_failures": "0"})
    except Exception:
        logger.exception("[시뮬레이터] 기동 시 오류 상태 초기화 실패")
    while True:
        await asyncio.sleep(SIM_TICK_SECONDS)
        # pipeline-optimizer 인계(2026-09-29): 틱 본체만 스레드로 빼고 is_running()/
        # _set_control()은 이벤트 루프에서 동기로 SQLite를 열고 있었다. 감사 로그와
        # 같은 파일을 쓰므로 잠기면 busy_timeout 동안 루프 전체(모든 async 엔드포인트)가
        # 멈춘다 - 이것들도 스레드에서 실행한다.
        try:
            if not await asyncio.to_thread(is_running):
                continue
            await asyncio.to_thread(_tick_once)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            consecutive += 1
            logger.exception("[시뮬레이터] 틱 실행 중 오류 (연속 %d회)", consecutive)
            try:
                await asyncio.to_thread(_set_controls, {
                    "last_error": f"{type(e).__name__}: {e}",
                    "consecutive_failures": str(consecutive),
                })
            except Exception:
                # 상태 기록 실패가 루프를 죽이면 안 된다(위 docstring의 원칙과 동일)
                logger.exception("[시뮬레이터] 오류 상태 기록 실패")
        else:
            if consecutive:
                # 카운터는 DB를 실제로 지운 뒤에만 0으로 돌린다 - 예전엔 먼저 0으로 만들어서,
                # 지우는 쓰기가 실패하면 이후 정상 틱이 다시는 지우려 하지 않았다.
                try:
                    await asyncio.to_thread(_set_controls, {"last_error": "", "consecutive_failures": "0"})
                    consecutive = 0
                except Exception:
                    logger.exception("[시뮬레이터] 오류 상태 초기화 실패 - 다음 정상 틱에서 다시 시도")
