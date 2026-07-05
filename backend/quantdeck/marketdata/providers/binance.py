"""Binance public market-data provider (crypto, realtime, no API key).

Uses only Binance's PUBLIC endpoints:
  - REST  https://api.binance.com  ->  /api/v3/ticker/24hr (quotes),
                                        /api/v3/klines      (OHLCV)
  - WS    wss://stream.binance.com:9443  ->  <symbol>@ticker combined stream

Canonical symbols are the platform's "BASE/QUOTE" form (e.g. "BTC/USDT").
Binance wants them concatenated and uppercased ("BTCUSDT"), which is what
:func:`_to_binance` produces. We never fabricate a price: on any failure to
obtain real data we raise :class:`DataUnavailableError` (or, for HTTP 429,
:class:`ProviderRateLimited` so the service can serve cache).
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
from ...core.symbols import to_crypto_pair
from ..base import MarketDataProvider

log = logging.getLogger("quantdeck.marketdata.binance")

_REST_BASE = "https://api.binance.com"
_WS_BASE = "wss://stream.binance.com:9443/stream"

# Canonical interval -> Binance interval string. Binance uses the same short
# forms for everything except our "1w" which maps to Binance "1w" too.
_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
}


def _to_binance(symbol: str) -> str:
    """Canonical 'BTC/USDT' (or 'BTCUSDT') -> Binance 'BTCUSDT'."""
    pair = to_crypto_pair(symbol)  # ensure BASE/QUOTE form
    return pair.replace("/", "").upper()


def _from_binance(binance_symbol: str) -> str:
    """Binance 'BTCUSDT' -> canonical 'BTC/USDT' (best-effort)."""
    return to_crypto_pair(binance_symbol.upper())


class BinanceProvider(MarketDataProvider):
    """Realtime crypto data from Binance's public endpoints."""

    name = "binance"
    asset_classes = (AssetClass.CRYPTO,)

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create a shared async HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=_REST_BASE,
                timeout=self._timeout,
                headers={"Accept": "application/json"},
            )
        return self._client

    # ------------------------------------------------------------------ #
    # Quotes                                                             #
    # ------------------------------------------------------------------ #
    async def get_quote(self, symbol: str) -> Quote:
        client = await self._get_client()
        bsym = _to_binance(symbol)
        try:
            resp = await client.get("/api/v3/ticker/24hr", params={"symbol": bsym})
        except httpx.HTTPError as exc:
            raise DataUnavailableError(
                f"binance: network error for {symbol}: {exc}"
            ) from exc

        if resp.status_code == 429:
            raise ProviderRateLimited(f"binance: rate limited on quote for {symbol}")
        if resp.status_code >= 400:
            raise DataUnavailableError(
                f"binance: no quote for {symbol} (HTTP {resp.status_code})"
            )

        data = resp.json()
        try:
            last = float(data["lastPrice"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataUnavailableError(
                f"binance: malformed quote for {symbol}"
            ) from exc
        if last <= 0:
            raise DataUnavailableError(f"binance: zero price for {symbol}")

        bid = _safe_float(data.get("bidPrice"))
        ask = _safe_float(data.get("askPrice"))
        prev_close = _safe_float(data.get("prevClosePrice"))
        return Quote(
            symbol=to_crypto_pair(symbol),
            asset_class=AssetClass.CRYPTO,
            price=last,
            bid=bid,
            ask=ask,
            volume=_safe_float(data.get("volume")),
            change=_safe_float(data.get("priceChange")),
            change_percent=_safe_float(data.get("priceChangePercent")),
            prev_close=prev_close,
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
        b_interval = _INTERVAL_MAP.get(interval)
        if b_interval is None:
            raise DataUnavailableError(f"binance: unsupported interval {interval!r}")

        client = await self._get_client()
        bsym = _to_binance(symbol)
        # Binance caps klines at 1000 per request.
        params: dict[str, object] = {
            "symbol": bsym,
            "interval": b_interval,
            "limit": max(1, min(int(limit), 1000)),
        }
        start_ms = _iso_to_ms(start)
        end_ms = _iso_to_ms(end)
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms

        try:
            resp = await client.get("/api/v3/klines", params=params)
        except httpx.HTTPError as exc:
            raise DataUnavailableError(
                f"binance: network error for klines {symbol}: {exc}"
            ) from exc

        if resp.status_code == 429:
            raise ProviderRateLimited(f"binance: rate limited on klines for {symbol}")
        if resp.status_code >= 400:
            raise DataUnavailableError(
                f"binance: no klines for {symbol} (HTTP {resp.status_code})"
            )

        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            raise DataUnavailableError(f"binance: empty klines for {symbol}")

        canonical = to_crypto_pair(symbol)
        bars: list[Bar] = []
        for row in rows:
            # Kline layout: [openTime, open, high, low, close, volume, closeTime, ...]
            try:
                bars.append(
                    Bar(
                        symbol=canonical,
                        timestamp=datetime.fromtimestamp(
                            int(row[0]) / 1000, tz=timezone.utc
                        ),
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                    )
                )
            except (IndexError, TypeError, ValueError):
                # Skip a single malformed row rather than fail the whole set.
                continue

        if not bars:
            raise DataUnavailableError(f"binance: no usable klines for {symbol}")

        return OHLCVResponse(
            symbol=canonical,
            asset_class=AssetClass.CRYPTO,
            interval=interval,
            bars=bars,
            source=self.name,
            delay=DataDelay.REALTIME,
        )

    # ------------------------------------------------------------------ #
    # Realtime stream (combined @ticker websocket)                       #
    # ------------------------------------------------------------------ #
    async def stream_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]:
        """Yield realtime :class:`Quote`s from Binance's combined ticker stream.

        Reconnects with a short backoff on connection loss. Requires the
        ``websockets`` library; if it is not installed this raises
        ``NotImplementedError`` so the service transparently falls back to
        REST polling.
        """
        try:
            import websockets  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on env
            raise NotImplementedError("websockets library not installed") from exc

        streams = [f"{_to_binance(s).lower()}@ticker" for s in symbols]
        if not streams:
            return
        url = f"{_WS_BASE}?streams={'/'.join(streams)}"

        backoff = 1.0
        while True:
            try:
                async with websockets.connect(
                    url, ping_interval=20, ping_timeout=20, max_queue=256
                ) as ws:
                    backoff = 1.0  # reset after a successful connect
                    async for raw in ws:
                        quote = _parse_ticker_frame(raw)
                        if quote is not None:
                            yield quote
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the stream resilient
                log.warning("binance ws error, reconnecting: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# --------------------------------------------------------------------------- #
# Module helpers                                                              #
# --------------------------------------------------------------------------- #
def _parse_ticker_frame(raw: str | bytes) -> Optional[Quote]:
    """Parse one combined-stream frame into a Quote, or None if unusable."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    payload = msg.get("data", msg) if isinstance(msg, dict) else None
    if not isinstance(payload, dict):
        return None
    bsym = payload.get("s")
    last = _safe_float(payload.get("c"))  # 'c' = last price in a 24h ticker frame
    if not bsym or last is None or last <= 0:
        return None
    return Quote(
        symbol=_from_binance(bsym),
        asset_class=AssetClass.CRYPTO,
        price=last,
        bid=_safe_float(payload.get("b")),
        ask=_safe_float(payload.get("a")),
        volume=_safe_float(payload.get("v")),
        change=_safe_float(payload.get("p")),
        change_percent=_safe_float(payload.get("P")),
        prev_close=_safe_float(payload.get("x")),
        timestamp=datetime.now(timezone.utc),
        source="binance",
        delay=DataDelay.REALTIME,
    )


def _safe_float(value: object) -> Optional[float]:
    """Coerce to float, returning None on any failure."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _iso_to_ms(value: Optional[str]) -> Optional[int]:
    """Convert an ISO-8601 date/time string to epoch milliseconds, or None."""
    if not value:
        return None
    try:
        # Accept both plain dates ("2024-01-01") and full timestamps.
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None
