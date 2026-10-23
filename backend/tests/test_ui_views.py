"""Read-only UI contracts against temporary databases; never invoke a production graph."""
import sqlite3
from datetime import datetime
from typing import TypedDict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from data import machine_views, pdm_operations, runtime_dataset, sim_query, work_order_view
from ui_routes import router


@pytest.fixture
def ui_db(tmp_path, monkeypatch):
    path = str(tmp_path / "ui.db")
    monkeypatch.setattr(sim_query, "DB_PATH", path)
    catalog = [{"machineID": i, "model": f"model{i}", "age": i * 2} for i in (1, 2, 3)]
    monkeypatch.setattr(pdm_operations, "list_machine_info", lambda: catalog)
    monkeypatch.setattr(pdm_operations, "get_machine_info", lambda mid: next((row for row in catalog if row["machineID"] == mid), {"error": "unknown"}))
    monkeypatch.setattr(runtime_dataset, "local_now", lambda: datetime(2026, 10, 19, 12))
    with sqlite3.connect(path) as conn:
        for name in ("telemetry", "sim_telemetry"):
            conn.execute(f"CREATE TABLE {name} (datetime TEXT, machineID INTEGER, volt REAL, rotate REAL, pressure REAL, vibration REAL)")
        conn.executemany("INSERT INTO telemetry VALUES (?, ?, ?, ?, ?, ?)", [
            ("2026-10-19 10:00:00", 1, 100, 500, 10, 1),
            ("2026-10-19 12:00:00", 1, 200, 600, 20, 2),
            ("2026-10-19 06:00:00", 2, 90, 400, 9, 1),
        ])
        conn.execute("INSERT INTO sim_telemetry VALUES ('2026-10-19 12:00:00', 1, 250, 650, 25, 3)")
    return path


def test_telemetry_deduplicates_prefers_simulation_and_keeps_gaps(ui_db):
    result = machine_views.telemetry(1)
    assert result["unique_samples"] == 2
    assert result["duplicate_samples"] == 1
    assert len(result["points"]) == 24
    assert result["points"][-1]["volt"] == 250
    assert result["points"][-2]["volt"] is None
    assert result["points"][-2]["sample_count"] == 0
    assert result["freshness"]["status"] == "aligned"


def test_bucket_means_keep_extrema_and_sample_counts(ui_db):
    result = machine_views.telemetry(1, 24, 3)
    last = result["points"][-1]
    assert len(result["points"]) == 8
    assert last["volt"] == 175
    assert last["volt_min"] == 100
    assert last["volt_max"] == 250
    assert last["sample_count"] == 2


def test_machine_catalog_filters_pages_and_does_not_label_absence_healthy(ui_db):
    result = machine_views.machine_list()
    assert result["total"] == 3
    assert result["machines"][1]["freshness"]["status"] == "lagging"
    assert result["machines"][1]["freshness"]["dataset_lag_hours"] == 6
    assert result["machines"][2]["freshness"]["status"] == "missing"
    assert result["machines"][2]["sensors"]["volt"] is None
    assert machine_views.machine_list(model="model2")["machines"][0]["machine_id"] == 2
    assert machine_views.machine_list(search="model3")["total"] == 1
    assert machine_views.machine_list(offset=2, limit=1)["machines"][0]["machine_id"] == 3


def test_telemetry_respects_active_runtime_year(ui_db):
    with sqlite3.connect(ui_db) as conn:
        conn.execute("INSERT INTO sim_telemetry VALUES ('2027-01-01 00:00:00', 1, 999, 999, 999, 999)")
        conn.execute("CREATE TABLE runtime_dataset (id INTEGER PRIMARY KEY, metadata_json TEXT)")
        conn.execute('INSERT INTO runtime_dataset VALUES (1, \'{"year": 2026}\')')
    assert machine_views.telemetry(1)["points"][-1]["volt"] == 250


def test_ui_routes_validate_and_accept_numeric_query_strings(ui_db):
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/machines?limit=1&offset=1").json()["machines"][0]["machine_id"] == 2
        assert client.get("/machines/1/telemetry?hours=72&bucket_hours=3").status_code == 200
        assert client.get("/machines/1/telemetry?hours=25").status_code == 422
        assert client.get("/machines/1/telemetry?bucket_hours=0").status_code == 422
        assert client.get("/machines/101/telemetry").status_code == 422
        assert client.get("/machines/4/telemetry").status_code == 404
        assert client.get("/machines?offset=-1").status_code == 422


def test_server_event_filters_and_summary_are_not_page_scoped(event_store_module):
    events = event_store_module
    events.save_event(1, "주의", "압력 확인", "2026-10-19 10:00:00")
    events.save_event(2, "긴급", "진동 확인", "2026-10-19 11:00:00")
    events.save_event(3, "긴급", "진동 확인", "2026-10-19 12:00:00")
    result = events.list_events(limit=1, offset=1, severity="긴급", search="진동")
    assert result["total"] == 2
    assert len(result["events"]) == 1
    assert result["summary"] == {"total": 3, "urgent": 2, "warning": 1}
    assert events.list_events(search="' OR 1=1 --")["total"] == 0
    assert events.list_events(search="%")["total"] == 0  # Literal substring, not LIKE wildcards.


def test_pending_approvals_use_latest_checkpoint_and_do_not_resume(monkeypatch, tmp_path):
    from agent import agent_service

    class State(TypedDict):
        machine_id: int
        approved: bool

    calls = []

    def approval(state):
        decision = interrupt({"message": "approval"})
        calls.append(decision)
        return {"approved": decision}

    graph = StateGraph(State)
    graph.add_node("approval", approval)
    graph.add_edge(START, "approval")
    graph.add_edge("approval", END)
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.db")) as saver:
        app = graph.compile(checkpointer=saver)
        monkeypatch.setattr(agent_service, "app", app)
        for name in ("pending", "completed"):
            app.invoke({"machine_id": 1, "approved": False}, {"configurable": {"thread_id": name}})
        app.invoke(Command(resume=False), {"configurable": {"thread_id": "completed"}})
        before = list(calls)
        result = agent_service.list_pending_approvals()
        assert result["total"] == 1
        assert result["approvals"][0]["thread_id"] == "pending"
        assert result["approvals"][0]["machine_id"] == 1
        assert calls == before
        assert agent_service.list_pending_approvals(offset=1)["approvals"] == []


def test_structured_order_preserves_original_and_refuses_pii():
    from core.harness import HarnessRejectedError

    values = {"work_order": "legacy", "machine_id": 1, "involved_components": ["comp1"],
              "component_evidence": {"comp1": "관측 근거"}, "component_steps": {"comp1": ["점검"]}}
    result = work_order_view.build(values)
    assert values["work_order"] == "legacy"
    assert result["components"][0]["steps"] == ["점검"]
    assert result["components"][0]["procurement"] is None
    assert work_order_view.build({"work_order": "old checkpoint"}) is None
    values["component_evidence"]["comp1"] = "010-1234-5678"
    with pytest.raises(HarnessRejectedError):
        work_order_view.build(values)
