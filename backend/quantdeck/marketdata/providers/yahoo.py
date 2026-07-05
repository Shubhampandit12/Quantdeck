"""Yahoo Finance market-data provider (US equities, ~15-min delayed).

Backed by the ``yfinance`` library, which does blocking HTTP under the hood.
Every blocking call is pushed onto a worker thread via ``asyncio.to_thread`` so
the event loop is never stalled. This is the no-key fallback for US equities
when neither Finnhub nor Alpaca is configured.

Yahoo data is delayed (typically ~15 minutes), so quotes are labelled
:data:`DataDelay.DELAYED_15M`. Missing data always surfaces as
:class:`DataUnavailableError` — we never invent a price.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from ...core.enums import AssetClass, DataDelay
from ...core.errors import DataUnavailableError
from ...core.models import Bar, OHLCVResponse, Quote
from ...core.symbols import normalize_symbol
from ..base import MarketDataProvider

log = logging.getLogger("quantdeck.marketdata.yahoo")

# Canonical interval -> yfinance interval string.
_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "4h": "60m",   # yfinance has no native 4h; caller may resample if needed
    "1d": "1d",
    "1w": "1wk",
}

# Reasonable lookback windows per interval when start/end aren't given.
# (yfinance restricts how far back intraday intervals can go.)
_DEFAULT_PERIOD = {
    "1m": "7d",
    "5m": "60d",
    "15m": "60d",
    "30m": "60d",
    "1h": "730d",
    "4h": "730d",
    "1d": "5y",
    "1w": "10y",
}


def _to_yahoo(symbol: str) -> str:
    """Canonical ticker -> Yahoo ticker. Yahoo uses '-' where we use '.'."""
    return normalize_symbol(symbol).replace(".", "-")


class YahooProvider(MarketDataProvider):
    """Delayed US-equity data via yfinance (blocking calls run in threads)."""

    name = "yahoo"
    asset_classes = (AssetClass.US_EQUITY,)

    @staticmethod
    def _import_yf():
        """Import yfinance lazily so the package imports even if it is absent."""
        try:
            import yfinance as yf  # type: ignore

            return yf
        except ImportError as exc:  # pragma: no cover - depends on env
            raise DataUnavailableError(
                "yahoo: yfinance library is not installed"
            ) from exc

    # ------------------------------------------------------------------ #
    # Quotes                                                             #
    # ------------------------------------------------------------------ #
    async def get_quote(self, symbol: str) -> Quote:
        canonical = normalize_symbol(symbol)
        ysym = _to_yahoo(symbol)
        data = await asyncio.to_thread(self._fetch_quote_blocking, ysym)
        price = data.get("price")
        if price is None or price <= 0:
            raise DataUnavailableError(f"yahoo: no quote for {symbol}")
        prev_close = data.get("prev_close")
        change = change_pct = None
        if prev_close:
            change = price - prev_close
            change_pct = (change / prev_close) * 100.0 if prev_close else None
        return Quote(
            symbol=canonical,
            asset_class=AssetClass.US_EQUITY,
            price=price,
            bid=data.get("bid"),
            ask=data.get("ask"),
            volume=data.get("volume"),
            change=change,
            change_percent=change_pct,
            prev_close=prev_close,
            timestamp=datetime.now(timezone.utc),
            source=self.name,
            delay=DataDelay.DELAYED_15M,
        )

    def _fetch_quote_blocking(self, ysym: str) -> dict[str, Optional[float]]:
        """Blocking yfinance quote fetch. Returns a plain dict of floats."""
        yf = self._import_yf()
        ticker = yf.Ticker(ysym)
        price = prev_close = bid = ask = volume = None

        # Prefer fast_info (cheap, no full .info scrape) when available.
        fast = getattr(ticker, "fast_info", None)
        if fast is not None:
            price = _safe_float(_fast_get(fast, "last_price"))
            prev_close = _safe_float(_fast_get(fast, "previous_close"))
            volume = _safe_float(_fast_get(fast, "last_volume"))

        # Fall back to the last close from a short history if fast_info empty.
        if price is None:
            try:
                hist = ticker.history(period="5d", interval="1d")
                if hist is not None and not hist.empty:
                    price = _safe_float(hist["Close"].iloc[-1])
                    if len(hist) >= 2:
                        prev_close = _safe_float(hist["Close"].iloc[-2])
                    volume = _safe_float(hist["Volume"].iloc[-1])
            except Exception as exc:  # noqa: BLE001 - degrade to unavailable
                log.debug("yahoo history fallback failed for %s: %s", ysym, exc)

        return {
            "price": price,
            "prev_close": prev_close,
            "bid": bid,
            "ask": ask,
            "volume": volume,
        }

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
        y_interval = _INTERVAL_MAP.get(interval)
        if y_interval is None:
            raise DataUnavailableError(f"yahoo: unsupported interval {interval!r}")
        canonical = normalize_symbol(symbol)
        ysym = _to_yahoo(symbol)
        rows = await asyncio.to_thread(
            self._fetch_ohlcv_blocking, ysym, interval, y_interval, start, end
        )
        if not rows:
            raise DataUnavailableError(f"yahoo: no OHLCV for {symbol}")

        bars = [
            Bar(
                symbol=canonical,
                timestamp=row["timestamp"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
            for row in rows
        ]
        # Honor the caller's limit by keeping the most recent N bars.
        if limit and len(bars) > limit:
            bars = bars[-limit:]

        return OHLCVResponse(
            symbol=canonical,
            asset_class=AssetClass.US_EQUITY,
            interval=interval,
            bars=bars,
            source=self.name,
            delay=DataDelay.DELAYED_15M,
        )

    def _fetch_ohlcv_blocking(
        self,
        ysym: str,
        interval: str,
        y_interval: str,
        start: Optional[str],
        end: Optional[str],
    ) -> list[dict]:
        """Blocking yfinance history fetch -> list of row dicts."""
        yf = self._import_yf()
        ticker = yf.Ticker(ysym)
        kwargs: dict[str, object] = {"interval": y_interval, "auto_adjust": False}
        if start or end:
            if start:
                kwargs["start"] = start
            if end:
                kwargs["end"] = end
        else:
            kwargs["period"] = _DEFAULT_PERIOD.get(interval, "1y")

        try:
            hist = ticker.history(**kwargs)
        except Exception as exc:  # noqa: BLE001 - degrade to unavailable
            log.debug("yahoo history failed for %s: %s", ysym, exc)
            return []
        if hist is None or hist.empty:
            return []

        out: list[dict] = []
        for ts, row in hist.iterrows():
            timestamp = _to_utc_datetime(ts)
            try:
                out.append(
                    {
                        "timestamp": timestamp,
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                        "volume": float(row.get("Volume", 0.0) or 0.0),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out


# --------------------------------------------------------------------------- #
# Module helpers                                                              #
# --------------------------------------------------------------------------- #
def _fast_get(fast_info, key: str):
    """Read a key from yfinance fast_info (dict-like or attribute-like)."""
    try:
        return fast_info[key]
    except (KeyError, TypeError, IndexError):
        return getattr(fast_info, key, None)


def _safe_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # Guard against pandas NaN slipping through.
    if f != f:
        return None
    return f


def _to_utc_datetime(ts) -> datetime:
    """Convert a pandas Timestamp / datetime to a tz-aware UTC datetime."""
    py = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
    if not isinstance(py, datetime):
        return datetime.now(timezone.utc)
    if py.tzinfo is None:
        return py.replace(tzinfo=timezone.utc)
    return py.astimezone(timezone.utc)
