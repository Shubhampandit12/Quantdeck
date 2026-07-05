"""Canonical Pydantic data models — the shared schema for the whole platform.

Every layer speaks these types: market data normalizes providers into `Quote`
and `Bar`; the engine produces `Order`, `Fill`, `Position`, `Portfolio`; the API
and MCP server serialize exactly these. Do not invent parallel dicts elsewhere —
import from here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from .enums import (
    AssetClass,
    DataDelay,
    EventType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Market data                                                                 #
# --------------------------------------------------------------------------- #
class SymbolInfo(BaseModel):
    """A tradable instrument."""

    symbol: str                       # canonical symbol, e.g. "AAPL" or "BTC/USDT"
    name: str = ""
    asset_class: AssetClass
    exchange: str = ""
    # Provider-native symbol (e.g. "BTCUSDT" for Binance) if it differs.
    provider_symbol: str = ""
    tradable: bool = True


class Quote(BaseModel):
    """A normalized point-in-time quote from any provider."""

    symbol: str
    asset_class: AssetClass
    price: float                      # last / mid price used for fills & marking
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume: Optional[float] = None
    change: Optional[float] = None            # absolute change vs prev close
    change_percent: Optional[float] = None    # percent change vs prev close
    prev_close: Optional[float] = None
    timestamp: datetime = Field(default_factory=utcnow)
    source: str = "unknown"           # provider name, shown in UI
    delay: DataDelay = DataDelay.UNKNOWN


class Bar(BaseModel):
    """A single OHLCV candle."""

    symbol: str
    timestamp: datetime               # bar open time (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @field_validator("timestamp")
    @classmethod
    def _tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class OHLCVResponse(BaseModel):
    symbol: str
    asset_class: AssetClass
    interval: str
    bars: list[Bar]
    source: str = "unknown"
    delay: DataDelay = DataDelay.UNKNOWN


# --------------------------------------------------------------------------- #
# Orders / fills / positions                                                  #
# --------------------------------------------------------------------------- #
class OrderRequest(BaseModel):
    """What a caller (UI / AI / strategy) submits to place an order."""

    symbol: str
    side: OrderSide
    type: OrderType = OrderType.MARKET
    quantity: float = Field(gt=0)
    limit_price: Optional[float] = None       # for LIMIT / STOP_LIMIT
    stop_price: Optional[float] = None        # for STOP / STOP_LIMIT
    time_in_force: TimeInForce = TimeInForce.GTC
    # Optional bracket legs (attach a take-profit / stop-loss on fill).
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    # Free-form origin label: "ui", "ai:<session>", "strategy:<id>".
    source: str = "ui"
    client_order_id: Optional[str] = None


class Order(BaseModel):
    id: str
    portfolio_id: str
    symbol: str
    asset_class: AssetClass
    side: OrderSide
    type: OrderType
    quantity: float
    filled_quantity: float = 0.0
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    status: OrderStatus = OrderStatus.PENDING
    avg_fill_price: Optional[float] = None
    source: str = "ui"
    reject_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Fill(BaseModel):
    id: str
    order_id: str
    portfolio_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    commission: float = 0.0
    slippage: float = 0.0
    timestamp: datetime = Field(default_factory=utcnow)


class Position(BaseModel):
    symbol: str
    asset_class: AssetClass
    side: PositionSide = PositionSide.FLAT
    quantity: float = 0.0             # signed is avoided; use `side`. Always >= 0.
    avg_entry_price: float = 0.0
    last_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    # Liquidation price estimate given current leverage/margin (None if flat/N.A.)
    liquidation_price: Optional[float] = None


class Portfolio(BaseModel):
    id: str
    name: str = "Default"
    base_currency: str = "USD"
    starting_balance: float
    cash: float                       # settled cash balance
    equity: float                     # cash + unrealized P&L of positions
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    # Leverage / margin
    leverage: float = 1.0             # account-level max leverage multiplier
    buying_power: float = 0.0         # equity * leverage - used margin
    margin_used: float = 0.0
    maintenance_margin: float = 0.0
    fill_mode: str = "realistic"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PortfolioSnapshot(BaseModel):
    """Full account state returned by get_portfolio()."""

    portfolio: Portfolio
    positions: list[Position] = Field(default_factory=list)
    open_orders: list[Order] = Field(default_factory=list)


class RiskLimits(BaseModel):
    """Per-portfolio risk configuration enforced on the shared order path."""

    max_leverage: float = 10.0
    max_position_notional: Optional[float] = None   # per-symbol notional cap
    max_order_notional: Optional[float] = None
    max_daily_loss: Optional[float] = None          # absolute currency amount
    allowed_symbols: Optional[list[str]] = None     # None = all allowed


# --------------------------------------------------------------------------- #
# Events                                                                       #
# --------------------------------------------------------------------------- #
class Event(BaseModel):
    """A message on the real-time event bus."""

    type: EventType
    portfolio_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=utcnow)
    data: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Backtest                                                                      #
# --------------------------------------------------------------------------- #
class BacktestRequest(BaseModel):
    code: Optional[str] = None                # raw strategy source
    strategy_id: Optional[str] = None         # or reference a saved strategy
    symbols: list[str]
    interval: str = "1d"
    start: Optional[str] = None               # ISO date
    end: Optional[str] = None
    starting_balance: float = 100_000.0
    leverage: float = 1.0
    fill_mode: str = "realistic"
    commission_bps: float = 0.0               # commission in basis points
    slippage_bps: float = 0.0
    params: dict[str, Any] = Field(default_factory=dict)


class TradeRecord(BaseModel):
    symbol: str
    side: OrderSide
    quantity: float
    entry_price: float
    exit_price: Optional[float] = None
    entry_time: datetime
    exit_time: Optional[datetime] = None
    pnl: float = 0.0
    return_pct: float = 0.0


class EquityPoint(BaseModel):
    timestamp: datetime
    equity: float


class BacktestResult(BaseModel):
    # P&L headline — front-and-center per the product spec.
    starting_balance: float
    ending_balance: float
    total_pnl: float
    total_return_pct: float
    # Detailed metrics
    annualized_return_pct: Optional[float] = None
    max_drawdown_pct: float = 0.0
    sharpe_ratio: Optional[float] = None
    win_rate: float = 0.0
    num_trades: int = 0
    profit_factor: Optional[float] = None
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    trades: list[TradeRecord] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    data_warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None
