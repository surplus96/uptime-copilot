"""Read-only views for the operations UI. Values are observations, never risk probabilities."""
import math
import sqlite3
from datetime import datetime, timedelta

from data import pdm_operations, runtime_dataset, sim_query

SIGNALS = ("volt", "rotate", "pressure", "vibration")


def _bounds(conn: sqlite3.Connection) -> tuple[str, tuple]:
    window = runtime_dataset.window(conn)
    return (" AND datetime>=? AND datetime<?", window) if window else ("", ())


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [name for name in ("telemetry", "sim_telemetry") if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone()]


def _number(value) -> float | None:
    return float(value) if value is not None and math.isfinite(float(value)) else None


def _freshness(latest: str | None, reference: str | None) -> dict:
    if latest is None or reference is None:
        return {"status": "missing", "dataset_lag_hours": None, "wall_clock_lag_hours": None}
    lag = max(0.0, (datetime.fromisoformat(reference) - datetime.fromisoformat(latest)).total_seconds() / 3600)
    wall = (runtime_dataset.local_now() - datetime.fromisoformat(latest)).total_seconds() / 3600
    return {"status": "lagging" if lag > 2 else "aligned", "dataset_lag_hours": round(lag, 1),
            "wall_clock_lag_hours": round(wall, 1)}


def machine_list(search: str = "", model: str | None = None, offset: int = 0, limit: int = 20) -> dict:
    catalog = pdm_operations.list_machine_info()
    reference = sim_query.dataset_now()
    with sim_query.readonly_connection() as conn:
        scope, args = _bounds(conn)
        tables = _tables(conn)
        result = []
        for machine in catalog:
            machine_id = int(machine["machineID"])
            if model and machine["model"] != model:
                continue
            if search and search.casefold() not in f"{machine_id} {machine['model']}".casefold():
                continue
            newest = None
            for table in tables:
                row = conn.execute(
                    f'SELECT datetime, volt, rotate, pressure, vibration FROM {table} WHERE machineID=?{scope} ORDER BY datetime DESC LIMIT 1',
                    (machine_id, *args),
                ).fetchone()
                if row and (newest is None or row[0] >= newest[0]):
                    newest = row  # The simulation wins timestamp ties.
            latest = newest[0] if newest else None
            result.append({"machine_id": machine_id, "model": str(machine["model"]), "age": int(machine["age"]),
                           "latest_at": latest, "freshness": _freshness(latest, reference),
                           "sensors": {signal: _number(newest[i + 1]) if newest else None for i, signal in enumerate(SIGNALS)}})
    return {"machines": result[offset:offset + limit], "total": len(result), "offset": offset, "limit": limit,
            "dataset_now": reference, "queried_at": runtime_dataset.local_now().isoformat(timespec="seconds"),
            "timezone": "Asia/Seoul", "models": sorted({str(row["model"]) for row in catalog})}


def telemetry(machine_id: int, hours: int = 24, bucket_hours: int = 1) -> dict:
    if hours not in (24, 72, 168, 720) or bucket_hours not in (1, 3, 6, 12, 24) or hours % bucket_hours:
        raise ValueError("지원하지 않는 조회 기간 또는 집계 간격입니다.")
    info = pdm_operations.get_machine_info(machine_id)
    if "error" in info:
        raise KeyError(machine_id)
    reference = sim_query.dataset_now()
    end = datetime.fromisoformat(reference) if reference else None
    start = end - timedelta(hours=hours - 1) if end else None
    merged: dict[str, tuple] = {}
    duplicates = 0
    latest = None
    with sim_query.readonly_connection() as conn:
        scope, args = _bounds(conn)
        for table in _tables(conn):
            row = conn.execute(f'SELECT MAX(datetime) FROM {table} WHERE machineID=?{scope}', (machine_id, *args)).fetchone()
            if row and row[0] and (latest is None or row[0] > latest):
                latest = row[0]
            if not end or not start:
                continue
            rows = conn.execute(
                f'SELECT datetime, volt, rotate, pressure, vibration FROM {table} WHERE machineID=? AND datetime>=? AND datetime<=?{scope} ORDER BY datetime, rowid',
                (machine_id, str(start), str(end), *args),
            ).fetchall()
            for row in rows:
                if row[0] in merged:
                    duplicates += 1
                merged[row[0]] = row
    points = []
    if start:
        buckets: dict[int, list[tuple]] = {}
        for row in merged.values():
            index = int((datetime.fromisoformat(row[0]) - start).total_seconds() // (3600 * bucket_hours))
            buckets.setdefault(index, []).append(row)
        for index in range(hours // bucket_hours):
            rows = buckets.get(index, [])
            point: dict = {"datetime": str(start + timedelta(hours=index * bucket_hours)), "sample_count": len(rows)}
            for i, signal in enumerate(SIGNALS, 1):
                values = [number for row in rows if (number := _number(row[i])) is not None]
                point[signal] = round(sum(values) / len(values), 3) if values else None
                point[f"{signal}_min"] = min(values) if values else None
                point[f"{signal}_max"] = max(values) if values else None
            points.append(point)
    return {"machine_id": machine_id, "model": str(info["model"]), "age": int(info["age"]),
            "dataset_now": reference, "latest_at": latest, "freshness": _freshness(latest, reference),
            "queried_at": runtime_dataset.local_now().isoformat(timespec="seconds"), "timezone": "Asia/Seoul",
            "hours": hours, "bucket_hours": bucket_hours, "aggregation": "mean", "unique_samples": len(merged),
            "expected_samples": hours if reference else 0, "duplicate_samples": duplicates,
            "window_start": str(start) if start else None, "window_end": str(end) if end else None, "points": points}
