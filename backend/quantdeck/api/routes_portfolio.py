"""Portfolio + positions/orders/fills REST endpoints.

All of these are read/lifecycle operations against the shared `TradingEngine`.
Order *placement* lives in `routes_order.py`; here we only create, read, reset,
and configure accounts, and expose their positions/orders/fills.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..core.config import settings
from ..core.errors import PortfolioNotFound
from ..core.models import (
    Fill,
    Order,
    Portfolio,
    PortfolioSnapshot,
    Position,
    RiskLimits,
)
from .deps import get_engine

router = APIRouter()


# --------------------------------------------------------------------------- #
# Request bodies (thin; the response bodies are canonical core models).         #
# --------------------------------------------------------------------------- #
class CreatePortfolioBody(BaseModel):
    name: str = "Default"
    starting_balance: float = Field(default=settings.default_starting_balance, gt=0)
    leverage: float = Field(default=settings.default_leverage, gt=0)
    fill_mode: str = settings.default_fill_mode


class ResetBody(BaseModel):
    starting_balance: Optional[float] = Field(default=None, gt=0)


class LeverageBody(BaseModel):
    leverage: float = Field(..., gt=0)


def _not_found(portfolio_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"portfolio '{portfolio_id}' not found")


# --------------------------------------------------------------------------- #
# Lifecycle                                                                     #
# --------------------------------------------------------------------------- #
@router.get("/portfolios", response_model=list[Portfolio])
async def list_portfolios() -> list[Portfolio]:
    return await get_engine().list_portfolios()


@router.post("/portfolios", response_model=Portfolio)
async def create_portfolio(body: CreatePortfolioBody) -> Portfolio:
    return await get_engine().create_portfolio(
        name=body.name,
        starting_balance=body.starting_balance,
        leverage=body.leverage,
        fill_mode=body.fill_mode,
    )


@router.get("/portfolios/{portfolio_id}", response_model=Portfolio)
async def get_portfolio(portfolio_id: str) -> Portfolio:
    try:
        return await get_engine().get_portfolio(portfolio_id)
    except PortfolioNotFound:
        raise _not_found(portfolio_id)


@router.get("/portfolios/{portfolio_id}/snapshot", response_model=PortfolioSnapshot)
async def get_snapshot(portfolio_id: str) -> PortfolioSnapshot:
    try:
        return await get_engine().get_snapshot(portfolio_id)
    except PortfolioNotFound:
        raise _not_found(portfolio_id)


@router.post("/portfolios/{portfolio_id}/reset", response_model=Portfolio)
async def reset_portfolio(portfolio_id: str, body: ResetBody) -> Portfolio:
    try:
        return await get_engine().reset_portfolio(
            portfolio_id, starting_balance=body.starting_balance
        )
    except PortfolioNotFound:
        raise _not_found(portfolio_id)


@router.post("/portfolios/{portfolio_id}/leverage", response_model=Portfolio)
async def set_leverage(portfolio_id: str, body: LeverageBody) -> Portfolio:
    try:
        return await get_engine().set_leverage(portfolio_id, body.leverage)
    except PortfolioNotFound:
        raise _not_found(portfolio_id)


@router.post("/portfolios/{portfolio_id}/risk", response_model=Portfolio)
async def set_risk_limits(portfolio_id: str, body: RiskLimits) -> Portfolio:
    try:
        return await get_engine().set_risk_limits(portfolio_id, body)
    except PortfolioNotFound:
        raise _not_found(portfolio_id)


# --------------------------------------------------------------------------- #
# Positions / orders / fills                                                    #
# --------------------------------------------------------------------------- #
@router.get("/portfolios/{portfolio_id}/positions", response_model=list[Position])
async def get_positions(portfolio_id: str) -> list[Position]:
    try:
        return await get_engine().get_positions(portfolio_id)
    except PortfolioNotFound:
        raise _not_found(portfolio_id)


@router.get("/portfolios/{portfolio_id}/orders", response_model=list[Order])
async def get_orders(
    portfolio_id: str,
    open: bool = Query(default=False),
) -> list[Order]:
    """List orders. `?open=true` returns only working orders; otherwise the
    full order history for the account (via the snapshot's open orders +
    whatever the engine tracks)."""
    engine = get_engine()
    try:
        if open:
            return await engine.get_open_orders(portfolio_id)
        # Full history isn't a first-class engine method; the snapshot carries
        # open orders and the engine tracks the rest. Prefer a dedicated method
        # if the concrete engine exposes one, else fall back to open orders.
        all_orders_fn = getattr(engine, "get_orders", None)
        if callable(all_orders_fn):
            return await all_orders_fn(portfolio_id)
        return await engine.get_open_orders(portfolio_id)
    except PortfolioNotFound:
        raise _not_found(portfolio_id)


@router.get("/portfolios/{portfolio_id}/fills", response_model=list[Fill])
async def get_fills(
    portfolio_id: str,
    limit: int = Query(default=200, ge=1, le=5000),
) -> list[Fill]:
    try:
        return await get_engine().get_trade_history(portfolio_id, limit=limit)
    except PortfolioNotFound:
        raise _not_found(portfolio_id)
