"""Temporary SQLite only: scoped ticks, stopping, transactions and API contracts."""
import sqlite3
from dataclasses import asdict
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from data import event_store, runtime_dataset, sim_engine, sim_loop, sim_query, sim_store
from simulator_routes import router


@pytest.fixture
def simulation(tmp_path, monkeypatch):
    path = str(tmp_path / "single.db")
    for module in (event_store, sim_loop, sim_query, sim_store):
        monkeypatch.setattr(module, "DB_PATH", path)
    sim_store.init_sim_tables()
    event_store.init_event_table()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE telemetry (datetime TEXT, machineID INTEGER)")
        conn.execute("INSERT INTO telemetry VALUES ('2026-10-28 03:00:00',64)")
        conn.execute("INSERT INTO sim_telemetry VALUES ('2026-10-28 03:00:00',9,100,100,100,100)")
    monkeypatch.setattr(runtime_dataset, "local_now", lambda: datetime(2026, 10, 28, 3))
    monkeypatch.setattr(sim_loop, "SIM_HOURS_PER_TICK", 1)
    monkeypatch.setattr(sim_loop, "_baseline", lambda mid: {s: (100, 10) for s in sim_engine.SIGNALS})
    scans = []
    monkeypatch.setattr(sim_loop.agent_service, "scan_machines", lambda ids: (scans.append(ids) or [], []))
    sim_loop.inject(64, "volt")
    sim_loop.inject(9, "rotate")
    return path, scans


def rows(path, table, other=False):
    with sqlite3.connect(path) as conn:
        return conn.execute(f'SELECT * FROM {table}' + (' WHERE machineID != 64' if other else '')).fetchall()


def test_injection_waits_and_single_tick_preserves_other_machines(simulation):
    path, scans = simulation
    assert not sim_loop.is_running()
    assert len(rows(path, "sim_telemetry")) == 1
    before = {i: asdict(m) for i, m in sim_store.load_states().items() if i != 64}
    others = {t: rows(path, t, True) for t in ("sim_telemetry", "sim_errors", "sim_failures", "sim_maint")}
    sim_loop.start(64)
    sim_loop._tick_once()
    assert sim_store.load_states()[64].elapsed == 1
    assert {i: asdict(m) for i, m in sim_store.load_states().items() if i != 64} == before
    assert {t: rows(path, t, True) for t in others} == others
    assert scans == [[64]]
    assert sim_query.dataset_now() == "2026-10-28 04:00:00"
    assert sim_loop.status()["machine_id"] == 64
    assert sim_loop.status()["mode"] == "single"


def test_single_stops_at_failure_inside_multi_hour_tick(simulation, monkeypatch):
    path, scans = simulation
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO sim_telemetry VALUES ('2026-10-28 05:00:00',64,100,100,100,100)")
    states = sim_store.load_states()
    states[64].state, states[64].lead_hours, states[64].elapsed = "FAULT", 1, 1
    sim_store.save_states(states)
    monkeypatch.setattr(sim_loop, "SIM_HOURS_PER_TICK", 12)
    sim_loop.start(64)
    sim_loop._tick_once()
    assert rows(path, "sim_failures") == [("2026-10-28 06:00:00", 64, "comp1")]
    assert ("2026-10-28 06:00:00", 64, "comp1") in rows(path, "sim_maint")
    assert sim_query.sim_only_now() == "2026-10-28 06:00:00"
    assert sim_store.load_states()[64].state == "HEALTHY"
    assert not sim_loop.is_running()
    assert sim_loop.status()["stop_reason"] == "failure_reached"
    assert scans == [[64]]
    before = rows(path, "sim_telemetry")
    sim_loop._tick_once()
    assert rows(path, "sim_telemetry") == before
    with pytest.raises(ValueError, match="먼저 열화"):
        sim_loop.start(64)


def test_conflicts_pause_resume_and_all_mode(simulation):
    path, _ = simulation
    sim_loop.start(64)
    sim_loop.start(64)
    for action in (lambda: sim_loop.start(), lambda: sim_loop.start(9), lambda: sim_loop.inject(9)):
        with pytest.raises(ValueError, match="정지"):
            action()
    sim_loop._tick_once()
    sim_loop.stop()
    before = rows(path, "sim_telemetry")
    sim_loop._tick_once()
    assert rows(path, "sim_telemetry") == before
    sim_loop.start(64)
    sim_loop._tick_once()
    assert sim_store.load_states()[64].elapsed == 2
    sim_loop.stop()
    sim_loop.start()
    sim_loop._tick_once()
    assert sim_loop.status()["mode"] == "all"
    assert sim_loop.status()["machine_id"] is None
    assert sim_loop.is_running()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(DISTINCT machineID) FROM sim_telemetry WHERE datetime='2026-10-28 06:00:00'").fetchone()[0] == 100


def test_failed_tick_rolls_back_selected_state_and_rows(simulation):
    path, scans = simulation
    sim_loop.start(64)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER fail_write BEFORE INSERT ON sim_telemetry BEGIN SELECT RAISE(ABORT,'test failure'); END")
    before = asdict(sim_store.load_states()[64])
    with pytest.raises(sqlite3.IntegrityError):
        sim_loop._tick_once()
    assert asdict(sim_store.load_states()[64]) == before
    assert rows(path, "sim_maint") == []
    assert sim_query.sim_only_now() == "2026-10-28 03:00:00"
    assert scans == []


def test_control_api_validation_conflicts_and_legacy_start(simulation):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    for body in ({"mode": "single"}, {"mode": "all", "machine_id": 64}, {"mode": "single", "machine_id": 0},
                 {"mode": "single", "machine_id": 101}, {"mode": "single", "machine_id": 1.5}):
        assert client.post("/simulator/start", json=body).status_code == 422
    assert client.post("/simulator/start", json={"mode": "single", "machine_id": 1}).status_code == 409
    assert client.post("/simulator/start", json={"mode": "single", "machine_id": 64}).json() == {
        "running": True, "mode": "single", "machine_id": 64}
    assert client.post("/simulator/start").status_code == 409
    assert client.post("/simulator/inject", json={"machine_id": 64}).status_code == 409
    assert client.post("/simulator/stop").status_code == 200
    assert client.post("/simulator/start").json() == {"running": True, "mode": "all", "machine_id": None}
