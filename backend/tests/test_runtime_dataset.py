import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from agent import agent_service
from data import (
    event_store,
    pdm_dataloader,
    pdm_operations,
    pdm_telemetry,
    runtime_dataset,
    sim_loop,
    sim_query,
    sim_store,
)


@pytest.fixture
def runtime_db(tmp_path, monkeypatch):
    path = str(tmp_path / "runtime.db")
    for module in (event_store, pdm_dataloader, pdm_telemetry, sim_loop, sim_query, sim_store):
        monkeypatch.setattr(module, "DB_PATH", path)
    monkeypatch.setattr(pdm_telemetry, "_BASELINE_CACHE", {})
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE telemetry (datetime TEXT, machineID INTEGER, volt REAL, rotate REAL, pressure REAL, vibration REAL)")
        conn.execute("CREATE TABLE errors (datetime TEXT, machineID INTEGER, errorID TEXT)")
        conn.execute("CREATE TABLE failures (datetime TEXT, machineID INTEGER, failure TEXT)")
        conn.execute("CREATE TABLE maint (datetime TEXT, machineID INTEGER, comp TEXT)")
        conn.executemany("INSERT INTO telemetry VALUES (?,1,?,?,?,?)", [
            ("2015-01-01 06:00:00", 10, 20, 30, 40),
            ("2015-12-30 06:00:00", 12, 22, 32, 42),
            ("2015-12-31 06:00:00", 14, 24, 34, 44),
        ])
        conn.execute("INSERT INTO errors VALUES ('2015-12-30 06:00:00',1,'error1')")
        conn.execute("INSERT INTO failures VALUES ('2015-12-30 06:00:00',1,'comp1')")
        conn.execute("INSERT INTO maint VALUES ('2015-12-29 06:00:00',1,'comp1')")
    event_store.init_event_table()
    sim_store.init_sim_tables()
    return path


def activate(path):
    return runtime_dataset.activate(path, datetime(2026, 10, 8, 9))


def test_shift_preserves_intervals_values_baselines_and_backup(runtime_db):
    event_store.save_event(1, "긴급", "2015년 고장", "2015-12-30 06:00:00")
    meta = activate(runtime_db)
    assert Path(meta["backup_path"]).exists()
    with sqlite3.connect(meta["backup_path"]) as backup:
        assert backup.execute("SELECT min(datetime), count(*) FROM telemetry").fetchone() == ("2015-01-01 06:00:00", 3)
    with sqlite3.connect(runtime_db) as conn:
        assert conn.execute("SELECT datetime,volt FROM telemetry ORDER BY datetime").fetchall() == [
            ("2026-10-07 09:00:00", 12), ("2026-10-08 09:00:00", 14)]
        for table in runtime_dataset.SOURCE_TABLES:
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE substr(datetime,1,4) != '2026'").fetchone()[0] == 0
        assert conn.execute("SELECT datetime FROM maint").fetchone()[0] == "2026-10-06 09:00:00"
        assert conn.execute("SELECT count(*) FROM archived_runtime_events").fetchone()[0] == 1
    assert event_store.list_events()["total"] == 0
    assert pdm_telemetry._baseline(1)["volt"] == pytest.approx((12, 2))
    assert sim_query.dataset_now() == "2026-10-08 09:00:00"
    assert pdm_operations.get_recent_errors(1) == []
    assert pdm_operations.check_recent_failure(1) is None


def test_restart_mapping_is_idempotent_and_reloads_use_same_anchor(runtime_db):
    first = activate(runtime_db)
    assert runtime_dataset.activate(runtime_db, datetime(2026, 11, 8)) == first
    with sqlite3.connect(runtime_db) as conn:
        raw = pd.DataFrame({"datetime": pd.to_datetime(["2015-01-01 06:00:00", "2015-12-31 06:00:00"])})
        projected = runtime_dataset.project_frame(raw, conn)
    assert projected["datetime"].tolist() == [pd.Timestamp("2026-10-08 09:00:00")]
    assert len(list(Path(first["backup_path"]).parent.glob("*.db"))) == 1


def test_queries_exclude_previous_year_future_year_and_future_incidents(runtime_db):
    activate(runtime_db)
    with sqlite3.connect(runtime_db) as conn:
        conn.executemany("INSERT INTO sim_errors VALUES (?,1,'error1')", [
            ("2025-12-31 12:00:00",), ("2026-10-08 10:00:00",),
            ("2026-12-01 12:00:00",), ("2027-01-01 12:00:00",)])
        conn.execute("INSERT INTO sim_telemetry VALUES ('2026-10-08 10:00:00',1,14,24,34,44)")
        conn.execute("INSERT INTO sim_telemetry VALUES ('2027-01-01 12:00:00',1,14,24,34,44)")
    assert sim_query.sim_only_now() == "2026-10-08 10:00:00"
    errors = pdm_operations.get_recent_errors(1, limit=10)
    assert [e["datetime"] for e in errors] == ["2026-10-08 10:00:00"]
    assert all(r[0].startswith("2026") for r in sim_query.all_rows("errors", "sim_errors", ["datetime"], 1))


def test_completed_incident_is_excluded_from_direct_diagnosis_but_new_failure_is_allowed(runtime_db, monkeypatch):
    activate(runtime_db)
    event_store.save_event(1, "긴급", "점검 완료할 사고", "2026-10-08 10:00:00")
    event_store.complete_events([1])
    monkeypatch.setattr(pdm_operations, "get_machine_info", lambda mid: {"model": "model1"})
    monkeypatch.setattr(pdm_operations, "get_component_failure_stats", lambda model: [])
    monkeypatch.setattr(pdm_operations, "get_recent_errors", lambda mid, limit=3: [
        {"datetime": "2026-10-08 10:00:00", "errorID": "error1", "description": "완료된 오류"}])
    latest = {"datetime": "2026-10-08 10:00:00", "component": "comp1", "description": "고장"}
    monkeypatch.setattr(pdm_operations, "check_recent_failure", lambda *args, **kwargs: latest)
    monkeypatch.setattr(pdm_telemetry, "detect_anomaly", lambda mid: {
        "has_anomaly": True, "flagged_signals": ["volt"], "as_of": "2026-10-08 10:00:00"})
    monkeypatch.setattr(agent_service, "_predict_risk_safe", lambda mid: {
        "comp1": {"probability": 0.99, "top_features": []}})
    result = agent_service._diagnose_machine(1)
    assert result["severity"] == "일반"
    assert result["component_evidence"] == {}
    assert "완료된 오류" not in result["diagnosis"]
    latest["datetime"] = "2026-10-08 11:00:00"
    result = agent_service._diagnose_machine(1)
    assert result["severity"] == "긴급"
    assert "11:00:00" in result["component_evidence"]["comp1"]


def test_year_boundary_stops_without_writing_2027_rows(runtime_db, monkeypatch):
    activate(runtime_db)
    with sqlite3.connect(runtime_db) as conn:
        conn.execute("INSERT INTO sim_telemetry VALUES ('2026-12-31 23:00:00',1,14,24,34,44)")
    sim_loop.start()
    monkeypatch.setattr(sim_loop, "SIM_HOURS_PER_TICK", 1)
    with pytest.raises(ValueError, match="2026년 테스트 범위"):
        sim_loop._tick_once()
    assert not sim_loop.is_running()
    with sqlite3.connect(runtime_db) as conn:
        assert conn.execute("SELECT count(*) FROM sim_telemetry WHERE datetime >= '2027-01-01 00:00:00'").fetchone()[0] == 0


def test_failed_migration_rolls_back_original_dates(runtime_db):
    with sqlite3.connect(runtime_db) as conn:
        conn.execute("CREATE TRIGGER reject_shift BEFORE UPDATE ON failures BEGIN SELECT RAISE(ABORT,'blocked'); END")
    with pytest.raises(sqlite3.IntegrityError):
        activate(runtime_db)
    with sqlite3.connect(runtime_db) as conn:
        assert conn.execute("SELECT min(datetime), count(*) FROM telemetry").fetchone() == ("2015-01-01 06:00:00", 3)
        assert runtime_dataset.policy(conn) is None
        conn.execute("DROP TRIGGER reject_shift")
    assert activate(runtime_db)["year"] == 2026


def test_live_model_excludes_bootstrap_errors_and_uses_simulated_component_age(runtime_db, monkeypatch):
    from ml import predict
    activate(runtime_db)
    monkeypatch.setattr(pdm_operations, "get_machine_info", lambda mid: {"model": "model1", "age": 5})
    with sqlite3.connect(runtime_db) as conn:
        conn.execute("INSERT INTO errors VALUES ('2026-10-08 09:00:00',1,'error1')")
        conn.execute("INSERT INTO sim_errors VALUES ('2026-10-08 10:00:00',1,'error1')")
        conn.execute("INSERT INTO sim_telemetry VALUES ('2026-10-08 11:00:00',1,14,24,34,44)")
        conn.execute("INSERT INTO sim_maint VALUES ('2026-10-01 11:00:00',1,'comp1')")
    features = predict._build_live_feature_row(1)
    assert features["error1_count_24h"].iloc[0] == 1
    assert features["hours_since_maint_comp1"].iloc[0] == 7 * 24


def test_fresh_database_bootstraps_and_reload_keeps_runtime_dates(runtime_db, tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    with sqlite3.connect(runtime_db) as conn:
        for table in runtime_dataset.SOURCE_TABLES:
            pd.read_sql_query(f"SELECT * FROM {table}", conn).to_csv(archive / f"PdM_{table}.csv", index=False)
    fresh = str(tmp_path / "fresh.db")
    monkeypatch.setattr(runtime_dataset, "DB_PATH", fresh)
    monkeypatch.setattr(runtime_dataset, "local_now", lambda: datetime(2026, 10, 8, 9, 30))
    monkeypatch.setattr(pdm_dataloader, "DB_PATH", fresh)
    monkeypatch.setattr(pdm_dataloader, "DATA_DIR", str(archive))
    meta = runtime_dataset.ensure_ready()
    assert meta["year"] == 2026
    with sqlite3.connect(fresh) as conn:
        before = conn.execute("SELECT * FROM telemetry ORDER BY datetime").fetchall()
    pdm_dataloader.load_telemetry_to_sqlite()
    pdm_dataloader.load_small_tables_to_sqlite()
    with sqlite3.connect(fresh) as conn:
        assert conn.execute("SELECT * FROM telemetry ORDER BY datetime").fetchall() == before
        assert runtime_dataset.policy(conn) == meta


def test_completed_watermark_before_initialization_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(event_store, "DB_PATH", str(tmp_path / "empty.db"))
    assert event_store.completed_evidence_at(1) is None
