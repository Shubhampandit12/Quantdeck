"""Backtest REST endpoint.

A single POST that hands a `BacktestRequest` to the `Backtester`, which runs the
SAME Strategy SDK over historical bars in-process and returns a P&L-first
`BacktestResult`. Data gaps come back as `data_warnings` inside the result, so a
partial run still returns 200 with an honest report; only genuine failures
(no data at all) surface via the result's `error` field or a 503.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..core.errors import DataUnavailableError, StrategyError
from ..core.models import BacktestRequest, BacktestResult
from .deps import get_backtester

router = APIRouter()


@router.post("/backtest", response_model=BacktestResult)
async def run_backtest(req: BacktestRequest) -> BacktestResult:
    """Run a backtest and return its performance report."""
    try:
        return await get_backtester().run(req)
    except DataUnavailableError:
        raise HTTPException(status_code=503, detail="data unavailable")
    except StrategyError as exc:
        # Bad user code / bad params — 400 with the message for the author.
        raise HTTPException(status_code=400, detail=str(exc))
