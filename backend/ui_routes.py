"""Read-only UI routes; importable without initializing LLMs or downloading models."""
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query

from data import machine_views

router = APIRouter()


@router.get("/machines")
def machines(
    search: Annotated[str, Query(max_length=100)] = "", model: Annotated[str | None, Query(max_length=50)] = None,
    offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    try:
        return machine_views.machine_list(search.strip(), model, offset, limit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="설비 마스터 CSV가 준비되지 않았습니다.") from exc


@router.get("/machines/{machine_id}/telemetry")
def machine_telemetry(
    machine_id: Annotated[int, Path(ge=1, le=100)],
    hours: Annotated[int, Query(ge=24, le=720)] = 24, bucket_hours: Annotated[int, Query(ge=1, le=24)] = 1,
):
    try:
        return machine_views.telemetry(machine_id, hours, bucket_hours)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="설비를 찾지 못했습니다.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="설비 마스터 CSV가 준비되지 않았습니다.") from exc


@router.get("/agent/approvals")
def pending_approvals(offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    from agent import agent_service
    return agent_service.list_pending_approvals(offset=offset, limit=limit)
