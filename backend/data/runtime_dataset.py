"""Project archived PdM records onto the current test timeline without altering training CSVs."""
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

DB_PATH = str(Path(__file__).parent.parent / "store" / "pdm_telemetry.db")
SOURCE_TABLES = ("telemetry", "errors", "maint", "failures")
SIGNALS = ("volt", "rotate", "pressure", "vibration")


def local_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)


def policy(conn: sqlite3.Connection) -> dict | None:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_dataset'").fetchone():
        return None
    row = conn.execute("SELECT metadata_json FROM runtime_dataset WHERE id=1").fetchone()
    return json.loads(row[0]) if row else None


def window(conn: sqlite3.Connection) -> tuple[str, str] | None:
    active = policy(conn)
    if not active:
        return None
    year = active["year"]
    return f"{year}-01-01 00:00:00", f"{year + 1}-01-01 00:00:00"


def project_frame(frame: pd.DataFrame, conn: sqlite3.Connection) -> pd.DataFrame:
    """Reloads must use the stored offset, rather than silently restoring 2015 dates."""
    active = policy(conn)
    if not active:
        return frame
    result = frame.copy()
    result["datetime"] += pd.Timedelta(seconds=active["offset_seconds"])
    start, end = f"{active['year']}-01-01 00:00:00", f"{active['year'] + 1}-01-01 00:00:00"
    return result[(result["datetime"] >= start) & (result["datetime"] < end)]


def ensure_ready() -> dict:
    """A fresh Docker volume is initialized from the mounted archive before scanning starts."""
    with sqlite3.connect(DB_PATH) as conn:
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not set(SOURCE_TABLES).issubset(present):
        from data import pdm_dataloader
        if "telemetry" not in present:
            pdm_dataloader.load_telemetry_to_sqlite()
        if not {"errors", "maint", "failures"}.issubset(present):
            pdm_dataloader.load_small_tables_to_sqlite()
    return activate(DB_PATH)


def activate(db_path: str = DB_PATH, anchor: datetime | None = None) -> dict:
    """One-time, backed-up, transactional shift; subsequent starts keep the same mapping."""
    anchor = (anchor or local_now()).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        existing = policy(conn)
        if existing:
            return existing
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(SOURCE_TABLES).issubset(present):
            raise ValueError("먼저 PdM 원본 테이블을 적재해야 합니다.")
        source_end = conn.execute("SELECT MAX(datetime) FROM telemetry").fetchone()[0]
        if not source_end:
            raise ValueError("날짜 전환에 필요한 telemetry가 비어 있습니다.")
        offset = int((pd.Timestamp(anchor) - pd.Timestamp(source_end)).total_seconds())
        start, end = f"{anchor.year}-01-01 00:00:00", f"{anchor.year + 1}-01-01 00:00:00"
        backup_dir = Path(db_path).parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"pdm-before-runtime-{local_now():%Y%m%d-%H%M%S-%f}.db"
        with sqlite3.connect(backup_path) as backup:
            conn.backup(backup)
        metadata = {"year": anchor.year, "timezone": "Asia/Seoul", "source_end": source_end,
                    "anchor": str(anchor), "incident_start": str(anchor), "offset_seconds": offset,
                    "activated_at": str(local_now()), "backup_path": str(backup_path)}
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE runtime_dataset (id INTEGER PRIMARY KEY, metadata_json TEXT NOT NULL)")
            # Preserve the full-source anomaly baseline even when the operational year is shorter.
            conn.execute("CREATE TABLE IF NOT EXISTS telemetry_baselines (machine_id INTEGER, signal TEXT, mean REAL, std REAL, PRIMARY KEY(machine_id, signal))")
            for signal in SIGNALS:
                rows = conn.execute(f'SELECT machineID, COUNT(*), AVG({signal}), AVG({signal} * {signal}) FROM telemetry GROUP BY machineID').fetchall()
                for mid, count, mean, mean_square in rows:
                    variance = max(0.0, mean_square - mean * mean) * count / (count - 1) if count > 1 else 0.0
                    conn.execute("INSERT OR REPLACE INTO telemetry_baselines VALUES (?, ?, ?, ?)",
                                 (mid, signal, mean, math.sqrt(variance)))
            counts = {}
            for table in SOURCE_TABLES:
                conn.execute(f"UPDATE {table} SET datetime=datetime(datetime, ?)", (f"{offset:+d} seconds",))
                conn.execute(f"DELETE FROM {table} WHERE datetime < ? OR datetime >= ?", (start, end))
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            # Retire bootstrap incident lists explicitly, without claiming work was completed.
            conn.execute("CREATE TABLE IF NOT EXISTS archived_runtime_events (source_table TEXT, record_json TEXT, archived_at TEXT, reason TEXT)")
            for table in ("detected_events", "completed_events"):
                if table not in present:
                    continue
                cursor = conn.execute(f"SELECT rowid AS archived_rowid, * FROM {table}")
                columns = [col[0] for col in cursor.description]
                for row in cursor.fetchall():
                    event = dict(zip(columns, row))
                    evidence = event.get("evidence_at")
                    if evidence and pd.Timestamp(evidence) > pd.Timestamp(anchor):
                        continue
                    conn.execute("INSERT INTO archived_runtime_events VALUES (?, ?, ?, ?)",
                                 (table, json.dumps(event, ensure_ascii=False), str(local_now()), "runtime timeline cutover; historical reference"))
                    conn.execute(f"DELETE FROM {table} WHERE rowid=?", (event["archived_rowid"],))
            metadata["counts"] = counts
            conn.execute("INSERT INTO runtime_dataset VALUES (1, ?)", (json.dumps(metadata, ensure_ascii=False),))
        return metadata
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(activate(), ensure_ascii=False, indent=2))
