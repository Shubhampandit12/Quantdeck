"""Strategy REST endpoints: CRUD + start/stop/logs (code injection).

Users author `Strategy` subclasses and submit the source here. The
`StrategyManager` persists them (`StrategyORM`), scans/loads them safely, and
drives the live loop. It returns plain dicts (StrategySummary / detail shapes),
which we pass straight through — the contract's response shapes are dict-shaped.

Not-found and validation problems raised by the manager (`KeyError`,
`SandboxViolation`, `StrategyError`) are translated into clean HTTP errors so
the UI can show a useful message without a 500.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..core.errors import SandboxViolation, StrategyError
from .deps import get_strategy_manager

router = APIRouter()


class CreateStrategyBody(BaseModel):
    name: str
    code: str
    portfolio_id: Optional[str] = None
    params: Optional[dict[str, Any]] = None


class UpdateStrategyBody(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    params: Optional[dict[str, Any]] = None


class StartStrategyBody(BaseModel):
    portfolio_id: str
    symbols: Optional[list[str]] = None
    interval: str = "1m"


def _not_found(strategy_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"strategy '{strategy_id}' not found")


@router.get("/strategies")
async def list_strategies() -> list[dict]:
    return await get_strategy_manager().list()


@router.post("/strategies")
async def create_strategy(body: CreateStrategyBody) -> dict:
    try:
        return await get_strategy_manager().create(
            name=body.name,
            code=body.code,
            portfolio_id=body.portfolio_id,
            params=body.params,
        )
    except SandboxViolation as exc:
        # Dangerous code — 400 so the author sees exactly what was disallowed.
        raise HTTPException(status_code=400, detail=f"sandbox violation: {exc}")
    except StrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/strategies/{strategy_id}")
async def get_strategy(strategy_id: str) -> dict:
    try:
        return await get_strategy_manager().get(strategy_id)
    except (KeyError, StrategyError):
        # The manager signals a missing strategy via StrategyError (not KeyError).
        raise _not_found(strategy_id)


@router.put("/strategies/{strategy_id}")
async def update_strategy(strategy_id: str, body: UpdateStrategyBody) -> dict:
    try:
        return await get_strategy_manager().update(
            strategy_id,
            name=body.name,
            code=body.code,
            params=body.params,
        )
    except KeyError:
        raise _not_found(strategy_id)
    except SandboxViolation as exc:
        raise HTTPException(status_code=400, detail=f"sandbox violation: {exc}")
    except StrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/strategies/{strategy_id}")
async def delete_strategy(strategy_id: str) -> dict:
    try:
        return await get_strategy_manager().delete(strategy_id)
    except KeyError:
        raise _not_found(strategy_id)


@router.post("/strategies/{strategy_id}/start")
async def start_strategy(strategy_id: str, body: StartStrategyBody) -> dict:
    try:
        return await get_strategy_manager().start(
            strategy_id,
            body.portfolio_id,
            symbols=body.symbols,
            interval=body.interval,
        )
    except KeyError:
        raise _not_found(strategy_id)
    except (SandboxViolation, StrategyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/strategies/{strategy_id}/stop")
async def stop_strategy(strategy_id: str) -> dict:
    try:
        return await get_strategy_manager().stop(strategy_id)
    except (KeyError, StrategyError):
        # The manager signals a missing strategy via StrategyError (not KeyError).
        raise _not_found(strategy_id)


@router.get("/strategies/{strategy_id}/logs")
async def strategy_logs(
    strategy_id: str,
    limit: int = Query(default=200, ge=1, le=5000),
) -> dict:
    try:
        logs = await get_strategy_manager().logs(strategy_id, limit=limit)
        return {"logs": logs}
    except KeyError:
        raise _not_found(strategy_id)
