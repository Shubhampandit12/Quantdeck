"""FROZEN CONTRACT: the public surface of the simulation engine.

The API layer, MCP tools, strategy runtime, and AI loop all call an object that
satisfies `EngineProtocol`. The concrete `TradingEngine` (in `.engine`)
implements it. Keeping the surface here lets those layers be built in parallel
against a stable signature set.

All methods are async. All monetary/quantity values are floats in the
portfolio's base currency. Placing an order ALWAYS returns the resulting
`Order` (which may already be `REJECTED` with a `reject_reason`) — the shared
risk-check path never silently drops an order.
"""
from __future__ import annotations

from typing import Optional, Protocol

from ..core.models import (
    Fill,
    Order,
    OrderRequest,
    Portfolio,
    PortfolioSnapshot,
    Position,
    RiskLimits,
)


class EngineProtocol(Protocol):
    # --- Portfolio lifecycle ---
    async def create_portfolio(
        self,
        name: str = "Default",
        starting_balance: float = 100_000.0,
        leverage: float = 1.0,
        fill_mode: str = "realistic",
        risk_limits: Optional[RiskLimits] = None,
        base_currency: str = "USD",
    ) -> Portfolio: ...

    async def get_portfolio(self, portfolio_id: str) -> Portfolio: ...

    async def list_portfolios(self) -> list[Portfolio]: ...

    async def get_snapshot(self, portfolio_id: str) -> PortfolioSnapshot: ...

    async def reset_portfolio(
        self, portfolio_id: str, starting_balance: Optional[float] = None
    ) -> Portfolio:
        """Wipe positions/orders/fills and restart at a chosen balance."""
        ...

    async def set_leverage(self, portfolio_id: str, leverage: float) -> Portfolio: ...

    async def set_risk_limits(
        self, portfolio_id: str, limits: RiskLimits
    ) -> Portfolio: ...

    # --- Orders (shared risk-check path for ALL origins) ---
    async def place_order(self, portfolio_id: str, req: OrderRequest) -> Order: ...

    async def cancel_order(self, portfolio_id: str, order_id: str) -> Order: ...

    async def modify_order(
        self,
        portfolio_id: str,
        order_id: str,
        *,
        quantity: Optional[float] = None,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> Order: ...

    async def get_order(self, portfolio_id: str, order_id: str) -> Order: ...

    async def get_open_orders(self, portfolio_id: str) -> list[Order]: ...

    async def get_trade_history(
        self, portfolio_id: str, limit: int = 200
    ) -> list[Fill]: ...

    # --- Positions ---
    async def get_positions(self, portfolio_id: str) -> list[Position]: ...

    async def get_position(
        self, portfolio_id: str, symbol: str
    ) -> Optional[Position]: ...

    async def flatten_all(self, portfolio_id: str) -> list[Order]:
        """Emergency: market-close every open position. Kill-switch primitive."""
        ...

    # --- Market marking / matching tick ---
    async def on_quote(self, symbol: str, price: float) -> None:
        """Feed a fresh price: marks positions, checks resting orders, brackets,
        margin calls / liquidation. Called by the market-data streamer."""
        ...
