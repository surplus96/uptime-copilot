"""Persist delivery progress so a Tasks retry does not create another work order."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = str(Path(__file__).parent.parent / "store" / "pdm_telemetry.db")


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS cmms_deliveries (
        delivery_key TEXT PRIMARY KEY, status TEXT NOT NULL,
        work_order_id INTEGER, display_id TEXT, error TEXT, payload_json TEXT
    )""")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def claim(key: str, payload_json: str) -> dict:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM cmms_deliveries WHERE delivery_key=?", (key,)).fetchone()
        if row:
            return dict(row)
        conn.execute("INSERT INTO cmms_deliveries (delivery_key, status, payload_json) VALUES (?, 'creating', ?)", (key, payload_json))
        return {"delivery_key": key, "status": "new", "work_order_id": None}


def record(key: str, status: str, work_order_id: int | None = None,
           display_id: str | None = None, error: str | None = None,
           payload_json: str | None = None) -> None:
    with _connect() as conn:
        conn.execute("""UPDATE cmms_deliveries SET status=?,
            work_order_id=COALESCE(?, work_order_id), display_id=COALESCE(?, display_id), error=?,
            payload_json=COALESCE(?, payload_json)
            WHERE delivery_key=?""", (status, work_order_id, display_id, error, payload_json, key))


def get(key: str) -> dict:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM cmms_deliveries WHERE delivery_key=?", (key,)).fetchone()
        return dict(row) if row else {}
