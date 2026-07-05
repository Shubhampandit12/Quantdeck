"""Canonical enums shared across the whole platform.

These are the single source of truth for order sides, types, statuses, asset
classes, and event kinds. Every module (engine, market data, API, MCP, strategy
runtime) imports from here so string values never drift.
"""
from __future__ import annotations

from enum import Enum


class AssetClass(str, Enum):
    """The two — and only two — asset universes this platform supports."""

    US_EQUITY = "us_equity"
    CRYPTO = "crypto"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    GTC = "gtc"  # good til cancelled
    DAY = "day"
    IOC = "ioc"  # immediate or cancel


class OrderStatus(str, Enum):
    PENDING = "pending"        # accepted, waiting to be worked
    OPEN = "open"             # resting on the book (limit/stop)
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class FillMode(str, Enum):
    """How the simulation engine models fills."""

    IDEALIZED = "idealized"    # fill exactly at quote, no slippage/spread
    REALISTIC = "realistic"    # apply configurable slippage + spread


class DataDelay(str, Enum):
    REALTIME = "realtime"
    DELAYED_15M = "delayed_15m"
    UNKNOWN = "unknown"


class EventType(str, Enum):
    """Types emitted on the real-time engine/event bus."""

    ORDER_ACCEPTED = "order_accepted"
    ORDER_REJECTED = "order_rejected"
    ORDER_OPEN = "order_open"
    ORDER_FILLED = "order_filled"
    ORDER_PARTIALLY_FILLED = "order_partially_filled"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_MODIFIED = "order_modified"
    POSITION_CHANGED = "position_changed"
    PORTFOLIO_UPDATED = "portfolio_updated"
    MARGIN_CALL = "margin_call"
    LIQUIDATION = "liquidation"
    QUOTE = "quote"
    STRATEGY_LOG = "strategy_log"
    STRATEGY_STATUS = "strategy_status"
    AI_TRACE = "ai_trace"
    AI_STATUS = "ai_status"


class StrategyStatus(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class AISessionStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class RunMode(str, Enum):
    LIVE_PAPER = "live_paper"
    BACKTEST = "backtest"
