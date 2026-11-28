"""Finnhub market-data provider (US equities, realtime last trade).

Only active when ``settings.finnhub_api_key`` is set. Uses Finnhub REST:
  - /quote        -> realtime last trade + day OHLC + prev close
  - /stock/candle -> historical OHLCV candles
and an optional websocket (wss://ws.finnhub.io) for a realtime trade stream.

Never fabricates prices: an empty/zero Finnhub response (its convention for an
unknown or unsubscribed symbol) becomes :class:`DataUnavailableError`, and an
HTTP 429 becomes :class:`ProviderRateLimited` so the service can serve cache.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Optional

import httpx

from ...core.enums import AssetClass, DataDelay
from ...core.errors import DataUnavailableError, ProviderRateLimited
from ...core.models import Bar, OHLCVResponse, Quote
from ...core.symbols import normalize_symbol
from ..base import MarketDataProvider

log = logging.getLogger("quantdeck.marketdata.finnhub")

_REST_BASE = "https://finnhub.io/api/v1"
_WS_BASE = "wss://ws.finnhub.io"

# Canonical interval -> Finnhub resolution. Finnhub has no 4h resolution, so
# 4h is left out: the service then falls through to Alpaca or Yahoo, which
# return real 4-hour candles (rather than hourly candles labelled as 4h).
_RESOLUTION_MAP = {
    "1m": "1",
    "5m": "5",
    "1h": "60",
}

# How far back (seconds) to request per interval when start/end are absent.
_LOOKBACK_SECONDS = {
    "1m": 60 * 60 * 24 * 5,
    "5m": 60 * 60 * 24 * 20,
    "1h": 60 * 60 * 24 * 120,
}


class FinnhubProvider(MarketDataProvider):
    """Realtime US-equity data via Finnhub (requires an API key)."""

    name = "finnhub"
    asset_classes = (AssetClass.US_EQUITY,)

    def __init__(self, api_key: str, timeout: float = 10.0) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=_REST_BASE,
                timeout=self._timeout,
                headers={"Accept": "application/json", "X-Finnhub-Token": self._api_key},
            )
        return self._client

    # ------------------------------------------------------------------ #
    # Quotes                                                             #
    # ------------------------------------------------------------------ #
    async def get_quote(self, symbol: str) -> Quote:
        canonical = normalize_symbol(symbol)
        client = await self._get_client()
        try:
            resp = await client.get("/quote", params={"symbol": canonical})
        except httpx.HTTPError as exc:
            raise DataUnavailableError(
                f"finnhub: network error for {symbol}: {exc}"
            ) from exc

        if resp.status_code == 429:
            raise ProviderRateLimited(f"finnhub: rate limited on quote for {symbol}")
        if resp.status_code >= 400:
            raise DataUnavailableError(
                f"finnhub: no quote for {symbol} (HTTP {resp.status_code})"
            )

        data = resp.json()
        # Finnhub returns {"c": current, "pc": prevClose, "d": change,
        # "dp": pctChange, "h": high, "l": low, "o": open, "t": epoch}.
        current = _safe_float(data.get("c"))
        if current is None or current <= 0:
            raise DataUnavailableError(f"finnhub: empty quote for {symbol}")
        return Quote(
            symbol=canonical,
            asset_class=AssetClass.US_EQUITY,
            price=current,
            bid=None,
            ask=None,
            volume=None,
            change=_safe_float(data.get("d")),
            change_percent=_safe_float(data.get("dp")),
            prev_close=_safe_float(data.get("pc")),
            timestamp=datetime.now(timezone.utc),
            source=self.name,
            delay=DataDelay.REALTIME,
        )

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
        resolution = _RESOLUTION_MAP.get(interval)
        if resolution is None:
            raise DataUnavailableError(f"finnhub: unsupported interval {interval!r}")

        canonical = normalize_symbol(symbol)
        client = await self._get_client()

        now = int(datetime.now(timezone.utc).timestamp())
        from_ts = _iso_to_epoch(start)
        to_ts = _iso_to_epoch(end) or now
        if from_ts is None:
            from_ts = to_ts - _LOOKBACK_SECONDS.get(interval, 60 * 60 * 24 * 365)

        params = {
            "symbol": canonical,
            "resolution": resolution,
            "from": from_ts,
            "to": to_ts,
        }
        try:
            resp = await client.get("/stock/candle", params=params)
        except httpx.HTTPError as exc:
            raise DataUnavailableError(
                f"finnhub: network error for candles {symbol}: {exc}"
            ) from exc

        if resp.status_code == 429:
            raise ProviderRateLimited(f"finnhub: rate limited on candles for {symbol}")
        if resp.status_code >= 400:
            raise DataUnavailableError(
                f"finnhub: no candles for {symbol} (HTTP {resp.status_code})"
            )

        data = resp.json()
        # Finnhub uses {"s": "ok"|"no_data", "t":[...], "o":[...], ...}.
        if not isinstance(data, dict) or data.get("s") != "ok":
            raise DataUnavailableError(f"finnhub: no candle data for {symbol}")

        times = data.get("t") or []
        opens = data.get("o") or []
        highs = data.get("h") or []
        lows = data.get("l") or []
        closes = data.get("c") or []
        volumes = data.get("v") or []
        n = min(len(times), len(opens), len(highs), len(lows), len(closes))
        if n == 0:
            raise DataUnavailableError(f"finnhub: empty candles for {symbol}")

        bars: list[Bar] = []
        for i in range(n):
            try:
                bars.append(
                    Bar(
                        symbol=canonical,
                        timestamp=datetime.fromtimestamp(
                            int(times[i]), tz=timezone.utc
                        ),
                        open=float(opens[i]),
                        high=float(highs[i]),
                        low=float(lows[i]),
                        close=float(closes[i]),
                        volume=float(volumes[i]) if i < len(volumes) else 0.0,
                    )
                )
            except (TypeError, ValueError):
                continue

        if not bars:
            raise DataUnavailableError(f"finnhub: no usable candles for {symbol}")
        if limit and len(bars) > limit:
            bars = bars[-limit:]

        return OHLCVResponse(
            symbol=canonical,
            asset_class=AssetClass.US_EQUITY,
            interval=interval,
            bars=bars,
            source=self.name,
            delay=DataDelay.REALTIME,
        )

    # ------------------------------------------------------------------ #
    # Symbol search                                                      #
    # ------------------------------------------------------------------ #
    async def search_symbols(self, query: str):
        from ...core.models import SymbolInfo

        client = await self._get_client()
        try:
            resp = await client.get("/search", params={"q": query})
        except httpx.HTTPError:
            return []
        if resp.status_code != 200:
            return []
        data = resp.json()
        out: list[SymbolInfo] = []
        for item in (data.get("result") or [])[:50]:
            sym = str(item.get("symbol", "")).strip().upper()
            # Skip compound/exotic symbols that carry a dot-exchange suffix.
            if not sym or "." in sym and len(sym.split(".")[-1]) > 1:
                continue
            out.append(
                SymbolInfo(
                    symbol=sym,
                    name=str(item.get("description", "")).title(),
                    asset_class=AssetClass.US_EQUITY,
                    exchange="",
                    provider_symbol=sym,
                    tradable=True,
                )
            )
        return out

    # ------------------------------------------------------------------ #
    # Realtime trade stream                                              #
    # ------------------------------------------------------------------ #
    async def stream_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]:
        """Yield realtime trades from Finnhub's websocket.

        Requires the ``websockets`` library and a valid key; otherwise raises
        ``NotImplementedError`` so the service falls back to REST polling.
        """
        if not self._api_key:
            raise NotImplementedError("finnhub: no API key for websocket")
        try:
            import websockets  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on env
            raise NotImplementedError("websockets library not installed") from exc

        url = f"{_WS_BASE}?token={self._api_key}"
        canon = [normalize_symbol(s) for s in symbols]
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    backoff = 1.0
                    for sym in canon:
                        await ws.send(json.dumps({"type": "subscribe", "symbol": sym}))
                    async for raw in ws:
                        for quote in _parse_trade_frame(raw):
                            yield quote
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - resilient stream
                log.warning("finnhub ws error, reconnecting: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# --------------------------------------------------------------------------- #
# Module helpers                                                              #
# --------------------------------------------------------------------------- #
def _parse_trade_frame(raw: str | bytes) -> list[Quote]:
    """Parse a Finnhub trade frame into zero-or-more Quotes."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(msg, dict) or msg.get("type") != "trade":
        return []
    quotes: list[Quote] = []
    for trade in msg.get("data") or []:
        sym = str(trade.get("s", "")).strip().upper()
        price = _safe_float(trade.get("p"))
        if not sym or price is None or price <= 0:
            continue
        quotes.append(
            Quote(
                symbol=normalize_symbol(sym),
                asset_class=AssetClass.US_EQUITY,
                price=price,
                volume=_safe_float(trade.get("v")),
                timestamp=datetime.now(timezone.utc),
                source="finnhub",
                delay=DataDelay.REALTIME,
            )
        )
    return quotes


def _safe_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _iso_to_epoch(value: Optional[str]) -> Optional[int]:
    """ISO-8601 date/time -> epoch seconds, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None
