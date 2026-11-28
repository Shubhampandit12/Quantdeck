"""Alpaca market-data provider (US equities, realtime IEX feed).

Only active when ``settings.alpaca_api_key`` and ``settings.alpaca_api_secret``
are set. Uses Alpaca's free IEX market-data REST API at
``https://data.alpaca.markets`` (the ``feed=iex`` tier that free accounts get):
  - /v2/stocks/{symbol}/quotes/latest -> latest NBBO-style quote (bid/ask)
  - /v2/stocks/{symbol}/trades/latest  -> latest trade (last price)
  - /v2/stocks/{symbol}/bars           -> historical OHLCV bars

Authentication is via the ``APCA-API-KEY-ID`` / ``APCA-API-SECRET-KEY`` headers.
As everywhere, missing data raises :class:`DataUnavailableError` and HTTP 429
raises :class:`ProviderRateLimited`; we never fabricate a price.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from ...core.enums import AssetClass, DataDelay
from ...core.errors import DataUnavailableError, ProviderRateLimited
from ...core.models import Bar, OHLCVResponse, Quote
from ...core.symbols import normalize_symbol
from ..base import MarketDataProvider

log = logging.getLogger("quantdeck.marketdata.alpaca")

_DATA_BASE = "https://data.alpaca.markets"

# Canonical interval -> Alpaca timeframe string. Alpaca accepts "<n><unit>"
# with units Min/Hour/Day/Week; 4h is expressed as "4Hour".
_TIMEFRAME_MAP = {
    "1m": "1Min",
    "5m": "5Min",
    "1h": "1Hour",
    "4h": "4Hour",
}


class AlpacaProvider(MarketDataProvider):
    """Realtime IEX US-equity data via Alpaca (requires key + secret)."""

    name = "alpaca"
    asset_classes = (AssetClass.US_EQUITY,)

    def __init__(self, api_key: str, api_secret: str, timeout: float = 10.0) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._api_secret)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=_DATA_BASE,
                timeout=self._timeout,
                headers={
                    "Accept": "application/json",
                    "APCA-API-KEY-ID": self._api_key,
                    "APCA-API-SECRET-KEY": self._api_secret,
                },
            )
        return self._client

    # ------------------------------------------------------------------ #
    # Quotes                                                             #
    # ------------------------------------------------------------------ #
    async def get_quote(self, symbol: str) -> Quote:
        canonical = normalize_symbol(symbol)
        client = await self._get_client()

        # Last trade drives our fill/mark price; the latest quote gives bid/ask.
        last_price = await self._latest_trade_price(client, canonical)
        bid = ask = None
        try:
            q = await client.get(
                f"/v2/stocks/{canonical}/quotes/latest", params={"feed": "iex"}
            )
            if q.status_code == 200:
                qd = (q.json() or {}).get("quote") or {}
                bid = _safe_float(qd.get("bp"))
                ask = _safe_float(qd.get("ap"))
                if last_price is None:
                    # Fall back to the mid if there is a quote but no trade yet.
                    if bid and ask:
                        last_price = (bid + ask) / 2.0
        except httpx.HTTPError:
            pass  # quote is optional detail; trade price is the source of truth

        if last_price is None or last_price <= 0:
            raise DataUnavailableError(f"alpaca: no price for {symbol}")

        return Quote(
            symbol=canonical,
            asset_class=AssetClass.US_EQUITY,
            price=last_price,
            bid=bid,
            ask=ask,
            volume=None,
            timestamp=datetime.now(timezone.utc),
            source=self.name,
            delay=DataDelay.REALTIME,
        )

    async def _latest_trade_price(
        self, client: httpx.AsyncClient, canonical: str
    ) -> Optional[float]:
        try:
            resp = await client.get(
                f"/v2/stocks/{canonical}/trades/latest", params={"feed": "iex"}
            )
        except httpx.HTTPError as exc:
            raise DataUnavailableError(
                f"alpaca: network error for {canonical}: {exc}"
            ) from exc
        if resp.status_code == 429:
            raise ProviderRateLimited(f"alpaca: rate limited on trade for {canonical}")
        if resp.status_code >= 400:
            return None
        trade = (resp.json() or {}).get("trade") or {}
        return _safe_float(trade.get("p"))

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
        timeframe = _TIMEFRAME_MAP.get(interval)
        if timeframe is None:
            raise DataUnavailableError(f"alpaca: unsupported interval {interval!r}")

        canonical = normalize_symbol(symbol)
        client = await self._get_client()
        params: dict[str, object] = {
            "timeframe": timeframe,
            "limit": max(1, min(int(limit), 10_000)),
            "feed": "iex",
            "adjustment": "raw",
        }
        if start:
            params["start"] = _iso(start)
        if end:
            params["end"] = _iso(end)

        try:
            resp = await client.get(f"/v2/stocks/{canonical}/bars", params=params)
        except httpx.HTTPError as exc:
            raise DataUnavailableError(
                f"alpaca: network error for bars {symbol}: {exc}"
            ) from exc
        if resp.status_code == 429:
            raise ProviderRateLimited(f"alpaca: rate limited on bars for {symbol}")
        if resp.status_code >= 400:
            raise DataUnavailableError(
                f"alpaca: no bars for {symbol} (HTTP {resp.status_code})"
            )

        data = resp.json() or {}
        raw_bars = data.get("bars") or []
        if not raw_bars:
            raise DataUnavailableError(f"alpaca: empty bars for {symbol}")

        bars: list[Bar] = []
        for b in raw_bars:
            # Alpaca bar: {"t": ISO8601, "o","h","l","c","v"}.
            ts = _parse_iso(b.get("t"))
            if ts is None:
                continue
            try:
                bars.append(
                    Bar(
                        symbol=canonical,
                        timestamp=ts,
                        open=float(b["o"]),
                        high=float(b["h"]),
                        low=float(b["l"]),
                        close=float(b["c"]),
                        volume=float(b.get("v", 0.0) or 0.0),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

        if not bars:
            raise DataUnavailableError(f"alpaca: no usable bars for {symbol}")

        return OHLCVResponse(
            symbol=canonical,
            asset_class=AssetClass.US_EQUITY,
            interval=interval,
            bars=bars,
            source=self.name,
            delay=DataDelay.REALTIME,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# --------------------------------------------------------------------------- #
# Module helpers                                                              #
# --------------------------------------------------------------------------- #
def _safe_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _iso(value: str) -> str:
    """Normalize a caller-supplied date/time to RFC-3339 for Alpaca."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return value


def _parse_iso(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None
