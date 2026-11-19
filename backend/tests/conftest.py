"""Shared pytest fixtures for the QuantDeck backend test-suite.

Design goals:
  * Tests never touch the network. A deterministic in-memory ``FakeMarketData``
    stub fulfills the ``MarketData`` protocol from ``marketdata.base`` and hands
    out reproducible quotes / bars.
  * Tests never touch the developer's real SQLite file. Before ``quantdeck.db``
    is imported we point ``QUANTDECK_DATABASE_URL`` at a throwaway temp file and
    reset the cached settings singleton so the async engine binds to it.
  * Everything is async; ``pytest-asyncio`` runs coroutine tests directly.

Fixtures are written against the export-contract names so they exercise the same
concrete modules the rest of the platform imports (``TradingEngine``,
``MarketDataService``, ...). It is fine for a fixture to import a
module built by another agent — those imports resolve at integration time.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Path + settings bootstrap (must run BEFORE importing quantdeck.db)          #
# --------------------------------------------------------------------------- #
# Make ``backend/`` importable so ``import quantdeck`` and
# ``import strategies_examples.*`` work regardless of the invocation cwd.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Point the DB at a unique temp file for the whole test session and disable AI
# so nothing tries to reach Anthropic. These env vars must be set before the
# settings singleton is first constructed.
_TMP_DB_PATH = Path(tempfile.gettempdir()) / f"quantdeck_test_{uuid.uuid4().hex}.db"
os.environ["QUANTDECK_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB_PATH}"
os.environ.pop("QUANTDECK_ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)

# Reset the cached settings so the temp DB URL is honored, then re-point the
# module-level ``settings`` object other modules already captured by reference.
from quantdeck.core import config as _config  # noqa: E402

_config.get_settings.cache_clear()
_config.settings = _config.get_settings()

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from quantdeck.core.enums import AssetClass, DataDelay  # noqa: E402
from quantdeck.core.models import (  # noqa: E402
    Bar,
    OHLCVResponse,
    Quote,
    SymbolInfo,
    utcnow,
)
from quantdeck.core import symbols as _symbols  # noqa: E402


# --------------------------------------------------------------------------- #
# Deterministic fake market data                                              #
# --------------------------------------------------------------------------- #
# Interval label -> minutes-per-bar. Only the intervals used by the tests need
# to be present; anything else defaults to a day.
_INTERVAL_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 60 * 24,
    "1w": 60 * 24 * 7,
}


class FakeMarketData:
    """An in-memory, network-free ``MarketData`` implementation for tests.

    Prices are fully deterministic:
      * a fixed base price per symbol (``base_prices``), and
      * an optional per-symbol override series for OHLCV so tests can feed a
        hand-crafted price path (e.g. an SMA-cross scenario).

    The stub also lets a test *push* a live price via ``set_price`` so engine
    tests can simulate a quote crossing a resting limit/stop order.
    """

    def __init__(
        self,
        base_prices: Optional[dict[str, float]] = None,
        symbols: Optional[list[SymbolInfo]] = None,
    ) -> None:
        # Default universe covers the symbols the tests trade with.
        self._prices: dict[str, float] = {
            "AAPL": 150.0,
            "MSFT": 300.0,
            "NVDA": 120.0,
            "GOOGL": 140.0,
        }
        if base_prices:
            self._prices.update({_symbols.normalize_symbol(k): v
                                 for k, v in base_prices.items()})
        # Optional explicit close-price series keyed by symbol; when present,
        # ``get_ohlcv`` builds candles from it instead of the synthetic wave.
        self._series: dict[str, list[float]] = {}
        self._symbols: list[SymbolInfo] = symbols or self._default_symbols()
        self.started = False
        self.closed = False

    # --- test helpers (not part of the protocol) -------------------------- #
    def set_price(self, symbol: str, price: float) -> None:
        """Override the last price used by ``get_quote``/``get_price``."""
        self._prices[_symbols.normalize_symbol(symbol)] = float(price)

    def set_series(self, symbol: str, closes: list[float]) -> None:
        """Provide an explicit close series for deterministic OHLCV."""
        self._series[_symbols.normalize_symbol(symbol)] = list(closes)

    @staticmethod
    def _default_symbols() -> list[SymbolInfo]:
        return [
            SymbolInfo(symbol="AAPL", name="Apple Inc.",
                       asset_class=AssetClass.US_EQUITY, exchange="NASDAQ"),
            SymbolInfo(symbol="MSFT", name="Microsoft Corp.",
                       asset_class=AssetClass.US_EQUITY, exchange="NASDAQ"),
            SymbolInfo(symbol="NVDA", name="NVIDIA Corp.",
                       asset_class=AssetClass.US_EQUITY, exchange="NASDAQ"),
            SymbolInfo(symbol="GOOGL", name="Alphabet Inc.",
                       asset_class=AssetClass.US_EQUITY, exchange="NASDAQ"),
        ]

    def _base_price(self, symbol: str) -> float:
        sym = _symbols.normalize_symbol(symbol)
        return self._prices.get(sym, 100.0)

    # --- MarketData protocol --------------------------------------------- #
    async def get_quote(self, symbol: str) -> Quote:
        sym = _symbols.normalize_symbol(symbol)
        price = self._base_price(sym)
        return Quote(
            symbol=sym,
            asset_class=_symbols.classify(sym),
            price=price,
            bid=price * 0.999,
            ask=price * 1.001,
            volume=1_000.0,
            change=0.0,
            change_percent=0.0,
            prev_close=price,
            source="fake",
            delay=DataDelay.REALTIME,
        )

    async def get_price(self, symbol: str) -> float:
        return self._base_price(symbol)

    async def get_ohlcv(
        self,
        symbol: str,
        interval: str,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
    ) -> OHLCVResponse:
        sym = _symbols.normalize_symbol(symbol)
        step = timedelta(minutes=_INTERVAL_MINUTES.get(interval, 60 * 24))
        # Anchor the series so the last bar's open time is "now".
        first_ts = utcnow() - step * (limit - 1)

        closes = self._series.get(sym)
        if closes is None:
            # Synthetic but deterministic sine wave around the base price so
            # crossovers and reversions can occur without any randomness.
            base = self._base_price(sym)
            closes = [
                base * (1.0 + 0.05 * math.sin(i / 5.0))
                for i in range(limit)
            ]
        else:
            closes = closes[-limit:]

        # An empty series means "no data for this symbol": return zero bars
        # honestly instead of raising, so callers asking for a data-less symbol
        # get an empty OHLCVResponse (never fabricated candles, never a raw crash).
        bars: list[Bar] = []
        if not closes:
            return OHLCVResponse(
                symbol=sym,
                asset_class=_symbols.classify(sym),
                interval=interval,
                bars=bars,
                source="fake",
                delay=DataDelay.REALTIME,
            )

        prev_close = closes[0]
        for i, close in enumerate(closes):
            ts = first_ts + step * i
            open_ = prev_close
            high = max(open_, close) * 1.001
            low = min(open_, close) * 0.999
            bars.append(
                Bar(
                    symbol=sym,
                    timestamp=ts,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=1_000.0,
                )
            )
            prev_close = close

        return OHLCVResponse(
            symbol=sym,
            asset_class=_symbols.classify(sym),
            interval=interval,
            bars=bars,
            source="fake",
            delay=DataDelay.REALTIME,
        )

    async def search_symbols(self, query: str, limit: int = 25) -> list[SymbolInfo]:
        q = query.strip().upper()
        matches = [
            s for s in self._symbols
            if q in s.symbol.upper() or q in s.name.upper()
        ]
        return matches[:limit]

    async def get_symbols(
        self, asset_class: Optional[AssetClass] = None
    ) -> list[SymbolInfo]:
        if asset_class is None:
            return list(self._symbols)
        return [s for s in self._symbols if s.asset_class == asset_class]

    async def subscribe(self, symbols: list[str]) -> AsyncIterator[Quote]:
        # Yield one deterministic quote per requested symbol, then stop.
        for sym in symbols:
            yield await self.get_quote(sym)

    async def start(self) -> None:
        self.started = True

    async def aclose(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_market_data() -> FakeMarketData:
    """A fresh deterministic market-data stub per test."""
    return FakeMarketData()


@pytest_asyncio.fixture
async def db_ready() -> AsyncIterator[None]:
    """Create the schema in the temp DB and drop it afterwards.

    Each test gets a clean set of tables so portfolios/orders from one test do
    not leak into another.
    """
    from quantdeck.db.database import Base, engine, init_db

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await init_db()
    try:
        yield
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def engine_obj(db_ready, fake_market_data):  # noqa: ANN001 - fixture
    """A loaded ``TradingEngine`` wired to the fake market data.

    Depends on ``db_ready`` so the schema exists first. ``load()`` restores any
    persisted portfolios (none on a clean DB) — calling it exercises the real
    startup path.
    """
    from quantdeck.engine.engine import TradingEngine

    eng = TradingEngine(fake_market_data)
    await eng.load()
    return eng


@pytest_asyncio.fixture
async def portfolio(engine_obj):  # noqa: ANN001 - fixture
    """A default $100k, 1x-leverage portfolio ready to trade."""
    return await engine_obj.create_portfolio(
        name="Test",
        starting_balance=100_000.0,
        leverage=1.0,
        fill_mode="idealized",
    )


def cleanup_temp_db() -> None:
    """Remove the session temp DB file (best-effort)."""
    try:
        if _TMP_DB_PATH.exists():
            _TMP_DB_PATH.unlink()
    except OSError:
        pass


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ANN001
    cleanup_temp_db()
