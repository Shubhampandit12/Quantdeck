"""Order REST endpoints: place / cancel / modify / flatten.

Every order — from the UI, the AI, or a strategy — goes through
`engine.place_order()`, the single shared risk-check path (spine rule #3). A
*business* rejection (insufficient buying power, risk limit, unknown symbol
priced-out) is not an error: the engine returns an `Order` with
`status="rejected"` and a `reject_reason`, and we return HTTP 200 with that
order. We only raise HTTP errors for genuinely exceptional states (missing
portfolio / order, malformed request).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.errors import PortfolioNotFound
from ..core.models import Order, OrderRequest
from .deps import get_engine

router = APIRouter()


class ModifyOrderBody(BaseModel):
    """Partial modification of a working order. All fields optional."""

    quantity: Optional[float] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None


def _not_found_portfolio(portfolio_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"portfolio '{portfolio_id}' not found")


def _not_found_order(order_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"order '{order_id}' not found")


@router.post("/portfolios/{portfolio_id}/orders", response_model=Order)
async def place_order(portfolio_id: str, req: OrderRequest) -> Order:
    """Submit an order. Result may be `status:"rejected"` — HTTP is still 200."""
    try:
        return await get_engine().place_order(portfolio_id, req)
    except PortfolioNotFound:
        raise _not_found_portfolio(portfolio_id)


@router.post("/portfolios/{portfolio_id}/orders/{order_id}/cancel", response_model=Order)
async def cancel_order(portfolio_id: str, order_id: str) -> Order:
    try:
        return await get_engine().cancel_order(portfolio_id, order_id)
    except PortfolioNotFound:
        raise _not_found_portfolio(portfolio_id)
    except KeyError:
        raise _not_found_order(order_id)


@router.post("/portfolios/{portfolio_id}/orders/{order_id}/modify", response_model=Order)
async def modify_order(
    portfolio_id: str, order_id: str, body: ModifyOrderBody
) -> Order:
    try:
        return await get_engine().modify_order(
            portfolio_id,
            order_id,
            quantity=body.quantity,
            limit_price=body.limit_price,
            stop_price=body.stop_price,
            take_profit=body.take_profit,
            stop_loss=body.stop_loss,
        )
    except PortfolioNotFound:
        raise _not_found_portfolio(portfolio_id)
    except KeyError:
        raise _not_found_order(order_id)


@router.post("/portfolios/{portfolio_id}/flatten", response_model=list[Order])
async def flatten(portfolio_id: str) -> list[Order]:
    """Kill-switch: market-close every open position. Returns the close orders."""
    try:
        return await get_engine().flatten_all(portfolio_id)
    except PortfolioNotFound:
        raise _not_found_portfolio(portfolio_id)
