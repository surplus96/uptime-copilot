"""Presentation metadata for the existing simulator; never advances its state."""
from datetime import datetime, timedelta

from data.sim_engine import MachineSim, hours_until_failure


def failure_timing(machine: MachineSim, reference: str | None) -> dict:
    if machine.state == "HEALTHY" or reference is None:
        return {"hours_until_failure": None, "failure_due_at": None}
    timestamp = datetime.fromisoformat(reference)
    remaining = hours_until_failure(machine.elapsed, machine.lead_hours, timestamp.hour)
    return {"hours_until_failure": remaining, "failure_due_at": str(timestamp + timedelta(hours=remaining))}
