"""Shared dependency accessors: process-wide singletons for the API layer.

This module is the wiring hub. It constructs one of each collaborator —
market-data service, trading engine, strategy manager, backtester, AI session
manager — and hands them out through simple getter functions that the route
modules and WebSocket layer call. `setup_singletons()` is invoked once from the
FastAPI lifespan (see `app.py`) after the database has been initialized.

Everything downstream (routes, ws, MCP-in-process) talks to these getters so
there is exactly one engine and one market-data feed for the whole process —
the single risk-check / marking path the spine mandates.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..core.config import settings
from ..marketdata.service import MarketDataService, get_market_data as _get_md
from ..engine.engine import TradingEngine
from ..strategies.runner import StrategyManager
from ..backtest.engine import Backtester
from ..ai.session import AISessionManager

log = logging.getLogger("quantdeck.api.deps")

# --------------------------------------------------------------------------- #
# Module-level singletons. Populated by setup_singletons() at startup and then  #
# read-only for the life of the process.                                        #
# --------------------------------------------------------------------------- #
_market_data: Optional[MarketDataService] = None
_engine: Optional[TradingEngine] = None
_strategy_manager: Optional[StrategyManager] = None
_backtester: Optional[Backtester] = None
_ai_manager: Optional[AISessionManager] = None

# Convenience: the id of a "default" portfolio so a fresh install is usable
# immediately (the UI / AI chat need at least one account to point at).
_default_portfolio_id: Optional[str] = None

# Background task that streams live prices into the engine so positions
# mark-to-market and resting orders / bracket stops & targets actually trigger.
_market_feed_task: Optional["asyncio.Task"] = None
# How often to poll live prices for symbols the engine is holding/watching.
MARKET_FEED_INTERVAL_S = 3.0


async def _market_feed_loop() -> None:
    """Poll live prices for every symbol the engine is tracking and feed them to
    `engine.on_quote`. This is what makes stops/targets fire and P&L update in
    real time. Runs for the life of the process; resilient to transient errors.
    """
    assert _engine is not None and _market_data is not None
    log.info("market feed loop started (interval=%.1fs)", MARKET_FEED_INTERVAL_S)
    while True:
        try:
            symbols = _engine.tracked_symbols()
            for sym in symbols:
                try:
                    price = await _market_data.get_price(sym)
                except Exception:
                    continue  # provider hiccup — skip this symbol this tick
                await _engine.on_quote(sym, price)
        except asyncio.CancelledError:
            log.info("market feed loop stopped")
            raise
        except Exception:  # pragma: no cover - keep the loop alive
            log.exception("market feed loop error (continuing)")
        await asyncio.sleep(MARKET_FEED_INTERVAL_S)


async def setup_singletons() -> None:
    """Build and wire every collaborator. Idempotent — safe to call twice.

    Order matters: market data must be started before the engine loads (so the
    engine can mark restored positions), and the engine must exist before the
    strategy manager / backtester / AI manager, which all drive it.
    """
    global _market_data, _engine, _strategy_manager, _backtester, _ai_manager
    global _default_portfolio_id, _market_feed_task

    if _engine is not None:  # already wired — do not rebuild collaborators
        return

    # Market data: the lazy module singleton from the marketdata package.
    _market_data = _get_md()
    await _market_data.start()

    # Trading engine: restores any persisted portfolios/positions/orders.
    _engine = TradingEngine(_market_data)
    await _engine.load()

    # Higher-level services that all route through the single engine instance.
    _strategy_manager = StrategyManager(_engine, _market_data)
    _backtester = Backtester(_market_data)
    _ai_manager = AISessionManager(_engine, _market_data, _backtester)

    # Ensure at least one portfolio exists so the platform is usable on a fresh
    # database. Reuse an existing one if present.
    await _ensure_default_portfolio()

    # Start the live market-feed loop so positions mark-to-market and bracket
    # stops/targets trigger in real time.
    _market_feed_task = asyncio.create_task(_market_feed_loop())

    log.info(
        "API singletons ready (ai_enabled=%s, default_portfolio=%s)",
        settings.ai_enabled,
        _default_portfolio_id,
    )


async def _ensure_default_portfolio() -> None:
    """Guarantee a usable starting portfolio and remember its id."""
    global _default_portfolio_id
    assert _engine is not None

    existing = await _engine.list_portfolios()
    if existing:
        _default_portfolio_id = existing[0].id
        return

    portfolio = await _engine.create_portfolio(
        name="Default",
        starting_balance=settings.default_starting_balance,
        leverage=settings.default_leverage,
        fill_mode=settings.default_fill_mode,
    )
    _default_portfolio_id = portfolio.id


async def teardown_singletons() -> None:
    """Release resources (market-data sockets/clients) on shutdown."""
    global _market_data, _engine, _strategy_manager, _backtester, _ai_manager
    global _market_feed_task
    if _market_feed_task is not None:
        _market_feed_task.cancel()
        try:
            await _market_feed_task
        except (asyncio.CancelledError, Exception):  # pragma: no cover
            pass
        _market_feed_task = None
    if _market_data is not None:
        try:
            await _market_data.aclose()
        except Exception:  # pragma: no cover - best-effort cleanup
            log.exception("error closing market data")
    _market_data = None
    _engine = None
    _strategy_manager = None
    _backtester = None
    _ai_manager = None


# --------------------------------------------------------------------------- #
# Getters. These raise if called before setup_singletons(), which would only    #
# happen if a request slipped in before the lifespan startup completed.         #
# --------------------------------------------------------------------------- #
def _require(obj: Optional[object], name: str) -> object:
    if obj is None:
        raise RuntimeError(
            f"{name} not initialized; setup_singletons() must run at startup"
        )
    return obj


def get_market_data() -> MarketDataService:
    return _require(_market_data, "market_data")  # type: ignore[return-value]


def get_engine() -> TradingEngine:
    return _require(_engine, "engine")  # type: ignore[return-value]


def get_strategy_manager() -> StrategyManager:
    return _require(_strategy_manager, "strategy_manager")  # type: ignore[return-value]


def get_backtester() -> Backtester:
    return _require(_backtester, "backtester")  # type: ignore[return-value]


def get_ai_manager() -> AISessionManager:
    return _require(_ai_manager, "ai_manager")  # type: ignore[return-value]


def get_default_portfolio_id() -> Optional[str]:
    """The convenience default portfolio id (None until setup runs)."""
    return _default_portfolio_id
