"""Simulator controls without application startup or model initialization."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from data import sim_loop

router = APIRouter()


class SimulatorStartRequest(BaseModel):
    mode: Literal["all", "single"] = "all"
    machine_id: int | None = Field(default=None, ge=1, le=100, strict=True)

    @model_validator(mode="after")
    def validate_target(self):
        if (self.mode == "single") != (self.machine_id is not None):
            raise ValueError("single 모드는 설비 번호가 필요하며 all 모드는 설비 번호를 받지 않습니다.")
        return self


@router.post("/simulator/start")
def simulator_start(req: SimulatorStartRequest | None = None):
    selection = req or SimulatorStartRequest()
    try:
        sim_loop.start(selection.machine_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"running": True, "mode": selection.mode, "machine_id": selection.machine_id}


@router.post("/simulator/stop")
def simulator_stop():
    sim_loop.stop()
    return {"running": False}


@router.get("/simulator/status")
def simulator_status():
    return sim_loop.status()


class SimulatorInjectRequest(BaseModel):
    machine_id: int = Field(ge=1, le=100, strict=True)
    signal: Literal["volt", "rotate", "pressure", "vibration"] | None = None


@router.post("/simulator/inject")
def simulator_inject(req: SimulatorInjectRequest):
    try:
        return sim_loop.inject(req.machine_id, req.signal)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/simulator/reset")
def simulator_reset():
    sim_loop.reset()
    return {"status": "reset"}



