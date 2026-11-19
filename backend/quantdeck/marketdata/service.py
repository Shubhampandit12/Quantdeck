"""MarketDataService — the single market-data surface for the whole platform.

Everything that needs prices (engine, API, MCP, strategy runtime)
talks to a :class:`MarketDataService` obtained from :func:`get_market_data`.
The service:

  * routes each symbol to the first working of ``settings.us_equity_providers``,
  * caches quotes / OHLCV / symbol lists with TTLs from settings,
  * exposes the S&P 500 (via :func:`load_sp500`),
  * searches across that universe,
  * streams realtime updates via REST polling in :meth:`subscribe`,
  * and NEVER fabricates a price — provider failure surfaces as
    :class:`DataUnavailableError`, while a rate-limit falls back to cache.

It fulfills the :class:`~quantdeck.marketdata.base.MarketData` protocol.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Optional

from ..core.config import settings
from ..core.enums import AssetClass, DataDelay
from ..core.errors import DataUnavailableError, ProviderRateLimited
from ..core.models import OHLCVResponse, Quote, SymbolInfo
from ..core.symbols import classify, normalize_symbol
from .base import VALID_INTERVALS, MarketDataProvider
from .providers.alpaca import AlpacaProvider
from .providers.finnhub import FinnhubProvider
from .providers.yahoo import YahooProvider
from .sp500 import load_sp500

log = logging.getLogger("quantdeck.marketdata.service")


class MarketDataService:
    """Routes, caches, and streams market data behind the MarketData protocol."""

    def __init__(self) -> None:
        # US-equity providers are built on demand from settings, in preference
        # order. The first one that successfully answers a request wins for
        # that call; we do not pin a single provider globally so a keyed
        # provider hiccup can gracefully fall through to yahoo.
        self._equity_providers: list[MarketDataProvider] = self._build_equity_providers()

        # Lazy caches for the assembled symbol universes.
        self._symbol_cache: Optional[list[SymbolInfo]] = None
        self._symbol_index: dict[str, SymbolInfo] = {}

        # Per-kind TTL caches for quotes / ohlcv / symbol lists.
        from .cache import TTLCache

        self._quote_cache = TTLCache(default_ttl=settings.quote_cache_ttl)
        self._ohlcv_cache = TTLCache(default_ttl=settings.ohlcv_cache_ttl)
        self._meta_cache = TTLCache(default_ttl=settings.symbol_cache_ttl)

        self._started = False

    # ------------------------------------------------------------------ #
    # Provider wiring                                                    #
    # ------------------------------------------------------------------ #
    def _build_equity_providers(self) -> list[MarketDataProvider]:
        """Instantiate US-equity providers in the configured preference order."""
        built: list[MarketDataProvider] = []
        for name in settings.us_equity_providers:
            key = name.strip().lower()
            if key == "finnhub" and settings.finnhub_api_key:
                built.append(FinnhubProvider(settings.finnhub_api_key))
            elif key == "alpaca" and settings.alpaca_api_key and settings.alpaca_api_secret:
                built.append(AlpacaProvider(settings.alpaca_api_key, settings.alpaca_api_secret))
            elif key == "yahoo":
                built.append(YahooProvider())
        # Guarantee a working keyless fallback even if config omitted yahoo.
        if not any(isinstance(p, YahooProvider) for p in built):
            built.append(YahooProvider())
        return built

    def _providers_for(self, asset_class: AssetClass) -> list[MarketDataProvider]:
        return self._equity_providers

    # ------------------------------------------------------------------ #
    # Lifecycle                                                          #
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        """Warm the symbol universe. Safe to call more than once."""
        if self._started:
            return
        await self.get_symbols()  # populates the symbol index/cache
        self._started = True

    async def aclose(self) -> None:
        """Close every provider's HTTP client/websocket."""
        for p in self._equity_providers:
            try:
                await p.aclose()
            except Exception as exc:  # noqa: BLE001 - best-effort shutdown
                log.debug("provider %s close error: %s", getattr(p, "name", "?"), exc)

    # ------------------------------------------------------------------ #
    # Quotes                                                             #
    # ------------------------------------------------------------------ #
    async def get_quote(self, symbol: str) -> Quote:
        canonical = self._canonical(symbol)
        key = f"quote:{canonical}"
        cached = await self._quote_cache.get(key)
        if cached is not None:
            return cached

        # Capture any last-known value BEFORE hitting providers. get() above may
        # have lazily evicted an expired entry, so grab the stale copy now while
        # it may still exist — this is the value we honestly fall back to on a
        # provider rate-limit / total failure (never a fabricated price).
        stale = await self._quote_cache.get_stale(key)

        asset_class = classify(canonical)
        last_error: Optional[Exception] = None
        for provider in self._providers_for(asset_class):
            try:
                quote = await provider.get_quote(canonical)
            except ProviderRateLimited as exc:
                # Serve stale cache rather than fabricate or hard-fail.
                last_error = exc
                if stale is not None:
                    log.debug("rate limited on %s; serving cached quote", canonical)
                    return stale
                continue
            except DataUnavailableError as exc:
                last_error = exc
                continue
            except Exception as exc:  # noqa: BLE001 - try the next provider
                last_error = exc
                log.debug("provider %s quote error for %s: %s", provider.name, canonical, exc)
                continue
            await self._quote_cache.set(key, quote)
            return quote

        # Every provider failed. Last resort: stale cache if we ever had one.
        if stale is not None:
            return stale
        raise DataUnavailableError(
            f"no provider could quote {symbol}: {last_error}"
        )

    async def get_price(self, symbol: str) -> float:
        """Convenience: just the last/mid price used for fills and marking."""
        return (await self.get_quote(symbol)).price

    # ------------------------------------------------------------------ #
    # OHLCV                                                              #
    # ------------------------------------------------------------------ #
    async def get_ohlcv(
        self,
        symbol: str,
        interval: str,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
    ) -> OHLCVResponse:
        if interval not in VALID_INTERVALS:
            raise DataUnavailableError(f"unsupported interval {interval!r}")
        canonical = self._canonical(symbol)

        # Only cache the "default" recent window (no explicit start/end), since
        # those are what a live chart hammers; ranged/historical calls skip cache.
        cacheable = start is None and end is None
        key = f"ohlcv:{canonical}:{interval}:{limit}"
        if cacheable:
            cached = await self._ohlcv_cache.get(key)
            if cached is not None:
                return cached

        asset_class = classify(canonical)
        last_error: Optional[Exception] = None
        for provider in self._providers_for(asset_class):
            try:
                resp = await provider.get_ohlcv(
                    canonical, interval, start=start, end=end, limit=limit
                )
            except ProviderRateLimited as exc:
                last_error = exc
                if cacheable:
                    stale = await self._ohlcv_cache.get_stale(key)
                    if stale is not None:
                        return stale
                continue
            except DataUnavailableError as exc:
                last_error = exc
                continue
            except Exception as exc:  # noqa: BLE001 - try the next provider
                last_error = exc
                log.debug("provider %s ohlcv error for %s: %s", provider.name, canonical, exc)
                continue
            if cacheable:
                await self._ohlcv_cache.set(key, resp)
            return resp

        if cacheable:
            stale = await self._ohlcv_cache.get_stale(key)
            if stale is not None:
                return stale
        raise DataUnavailableError(
            f"no provider could supply OHLCV for {symbol}: {last_error}"
        )

    # ------------------------------------------------------------------ #
    # Symbols / search                                                   #
    # ------------------------------------------------------------------ #
    async def get_symbols(
        self, asset_class: Optional[AssetClass] = None
    ) -> list[SymbolInfo]:
        """Return the tradable universe, optionally filtered by asset class."""
        universe = await self._load_universe()
        if asset_class is None:
            return list(universe)
        return [s for s in universe if s.asset_class is asset_class]

    async def _load_universe(self) -> list[SymbolInfo]:
        """Assemble (and cache) the S&P 500 universe."""
        if self._symbol_cache is not None:
            return self._symbol_cache
        cached = await self._meta_cache.get("universe")
        if cached is not None:
            self._symbol_cache = cached
            self._reindex(cached)
            return cached

        # load_sp500 may do blocking I/O in its dynamic path; run off-loop.
        universe = await asyncio.to_thread(load_sp500)
        self._symbol_cache = universe
        self._reindex(universe)
        await self._meta_cache.set("universe", universe)
        return universe

    def _reindex(self, universe: list[SymbolInfo]) -> None:
        self._symbol_index = {s.symbol.upper(): s for s in universe}

    async def search_symbols(self, query: str, limit: int = 25) -> list[SymbolInfo]:
        """Substring search across the known universe."""
        q = (query or "").strip().upper()
        if not q:
            return []
        universe = await self._load_universe()

        scored: list[tuple[int, SymbolInfo]] = []
        for info in universe:
            sym = info.symbol.upper()
            name = info.name.upper()
            if sym == q:
                rank = 0
            elif sym.startswith(q):
                rank = 1
            elif q in sym:
                rank = 2
            elif name.startswith(q):
                rank = 3
            elif q in name:
                rank = 4
            else:
                continue
            scored.append((rank, info))

        scored.sort(key=lambda t: (t[0], t[1].symbol))
        return [info for _, info in scored[:limit]]

    # ------------------------------------------------------------------ #
    # Realtime subscription (merged stream)                              #
    # ------------------------------------------------------------------ #
    async def subscribe(self, symbols: list[str]) -> AsyncIterator[Quote]:
        """Yield realtime quotes for a set of equity symbols.

        Equities are polled over REST on a short interval, feeding a single
        internal queue. Cancelling the async generator tears down the
        underlying task.
        """
        equities = [self._canonical(s) for s in symbols]

        queue: asyncio.Queue[Quote] = asyncio.Queue(maxsize=1000)
        tasks: list[asyncio.Task] = []

        if equities:
            tasks.append(asyncio.create_task(self._poll_equities(equities, queue)))

        try:
            while True:
                yield await queue.get()
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    async def _poll_equities(
        self, symbols: list[str], queue: asyncio.Queue[Quote]
    ) -> None:
        """Poll equity quotes over REST (there is no free equity websocket here)."""
        await self._poll(symbols, queue, interval=3.0)

    async def _poll(
        self, symbols: list[str], queue: asyncio.Queue[Quote], *, interval: float
    ) -> None:
        """Generic REST poller: fetch each symbol's quote every ``interval`` s."""
        while True:
            for sym in symbols:
                try:
                    quote = await self.get_quote(sym)
                except DataUnavailableError:
                    continue  # honest skip; never emit a fabricated tick
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.debug("poll quote error for %s: %s", sym, exc)
                    continue
                await self._offer(queue, quote)
            await asyncio.sleep(interval)

    @staticmethod
    async def _offer(queue: asyncio.Queue[Quote], quote: Quote) -> None:
        """Non-blocking enqueue; drop the oldest tick if the consumer lags."""
        try:
            queue.put_nowait(quote)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(quote)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    # ------------------------------------------------------------------ #
    # Market summary helper (used by /api/market/summary)                #
    # ------------------------------------------------------------------ #
    async def get_market_summary(
        self, *, stock_sample: int = 12
    ) -> dict[str, list[Quote]]:
        """Assemble a lightweight dashboard summary.

        Returns quotes for a sample of large-cap stocks, plus the biggest
        movers among them. Symbols that cannot be quoted are simply omitted
        (never fabricated).
        """
        universe = await self._load_universe()
        stocks_syms = [
            s.symbol for s in universe if s.asset_class is AssetClass.US_EQUITY
        ][:stock_sample]

        stock_quotes = await self._gather_quotes(stocks_syms)

        with_change = [q for q in stock_quotes if q.change_percent is not None]
        movers = sorted(with_change, key=lambda q: q.change_percent, reverse=True)
        return {
            "movers_up": movers[:5],
            "movers_down": list(reversed(movers[-5:])) if movers else [],
            "stocks": stock_quotes,
        }

    async def _gather_quotes(self, symbols: list[str]) -> list[Quote]:
        """Fetch many quotes concurrently, dropping any that are unavailable."""
        results = await asyncio.gather(
            *(self.get_quote(s) for s in symbols), return_exceptions=True
        )
        return [q for q in results if isinstance(q, Quote)]

    # ------------------------------------------------------------------ #
    # Internal                                                           #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _canonical(symbol: str) -> str:
        """Normalize a caller symbol into canonical form for routing/keys."""
        return normalize_symbol(symbol)


# --------------------------------------------------------------------------- #
# Lazy module singleton                                                       #
# --------------------------------------------------------------------------- #
_service: Optional[MarketDataService] = None


def get_market_data() -> MarketDataService:
    """Return the process-wide MarketDataService, constructing it on first use."""
    global _service
    if _service is None:
        _service = MarketDataService()
    return _service
