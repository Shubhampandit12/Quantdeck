"""Market-data REST endpoints.

These are thin adapters over the `MarketDataService` singleton. The service
already normalizes providers into canonical `Quote` / `OHLCVResponse` /
`SymbolInfo` shapes, so these handlers mostly parse query params and translate
`DataUnavailableError` into an honest HTTP 503 — we NEVER fabricate a price.

Symbols travel in the path with the `:path` converter so crypto pairs like
`BTC/USDT` survive without needing URL-encoding, and encoded forms
(`BTC%2FUSDT`) are decoded by Starlette before we see them.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..core.enums import AssetClass
from ..core.errors import DataUnavailableError, UnknownSymbolError
from ..core.models import OHLCVResponse, Quote, SymbolInfo
from ..core.symbols import normalize_symbol
from .deps import get_market_data

router = APIRouter()

# Consistent 503 payload for genuine provider outages. Honest > fabricated.
_UNAVAILABLE = "data unavailable"


def _parse_asset_class(value: Optional[str]) -> Optional[AssetClass]:
    """Coerce the optional ?asset_class= query param into an enum (or None)."""
    if not value:
        return None
    try:
        return AssetClass(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown asset_class '{value}'")


@router.get("/symbols", response_model=list[SymbolInfo])
async def list_symbols(
    asset_class: Optional[str] = Query(default=None),
) -> list[SymbolInfo]:
    """All tradable instruments, optionally filtered by asset class.

    Includes the S&P 500 universe for US equities.
    """
    md = get_market_data()
    ac = _parse_asset_class(asset_class)
    return await md.get_symbols(ac)


@router.get("/search", response_model=list[SymbolInfo])
async def search_symbols(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=25, ge=1, le=100),
) -> list[SymbolInfo]:
    """Fuzzy symbol/name search for the ticker picker."""
    md = get_market_data()
    return await md.search_symbols(q, limit=limit)


@router.get("/quote/{symbol:path}", response_model=Quote)
async def get_quote(symbol: str) -> Quote:
    """Latest quote for one symbol. 503 if the provider genuinely has no data."""
    md = get_market_data()
    try:
        return await md.get_quote(normalize_symbol(symbol))
    except DataUnavailableError:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    except UnknownSymbolError:
        raise HTTPException(status_code=404, detail=f"unknown symbol '{symbol}'")


@router.get("/quotes", response_model=list[Quote])
async def get_quotes(
    symbols: str = Query(..., description="Comma-separated symbols"),
) -> list[Quote]:
    """Batch quotes. Symbols that fail resolve are simply omitted rather than
    failing the whole request — the caller asked for many and wants what's
    available (each returned quote is real; none are fabricated)."""
    md = get_market_data()
    wanted = [normalize_symbol(s) for s in symbols.split(",") if s.strip()]
    out: list[Quote] = []
    for sym in wanted:
        try:
            out.append(await md.get_quote(sym))
        except (DataUnavailableError, UnknownSymbolError):
            # Skip unavailable symbols; the UI shows only what is real.
            continue
    return out


@router.get("/ohlcv/{symbol:path}", response_model=OHLCVResponse)
async def get_ohlcv(
    symbol: str,
    interval: str = Query(default="1d"),
    limit: int = Query(default=500, ge=1, le=5000),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
) -> OHLCVResponse:
    """Historical candles for a symbol/interval."""
    md = get_market_data()
    try:
        return await md.get_ohlcv(
            normalize_symbol(symbol),
            interval,
            start=start,
            end=end,
            limit=limit,
        )
    except DataUnavailableError:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    except UnknownSymbolError:
        raise HTTPException(status_code=404, detail=f"unknown symbol '{symbol}'")


@router.get("/market/summary")
async def market_summary() -> dict:
    """Dashboard summary: top movers plus a slice of crypto and stocks.

    Delegates to the service if it exposes a `get_market_summary()`; otherwise
    builds a best-effort summary from a handful of well-known symbols. Any
    symbol that cannot be priced is silently omitted (never faked)."""
    md = get_market_data()

    # Preferred path: the service provides a purpose-built summary.
    summary_fn = getattr(md, "get_market_summary", None)
    if callable(summary_fn):
        try:
            return await summary_fn()
        except DataUnavailableError:
            raise HTTPException(status_code=503, detail=_UNAVAILABLE)

    # Fallback: assemble from a small representative watchlist.
    stock_syms = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"]
    crypto_syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]

    async def _collect(syms: list[str]) -> list[Quote]:
        quotes: list[Quote] = []
        for s in syms:
            try:
                quotes.append(await md.get_quote(s))
            except (DataUnavailableError, UnknownSymbolError):
                continue
        return quotes

    stocks = await _collect(stock_syms)
    crypto = await _collect(crypto_syms)

    ranked = sorted(
        [q for q in (stocks + crypto) if q.change_percent is not None],
        key=lambda q: q.change_percent or 0.0,
    )
    movers_down = ranked[:5]
    movers_up = list(reversed(ranked[-5:]))

    return {
        "movers_up": [q.model_dump() for q in movers_up],
        "movers_down": [q.model_dump() for q in movers_down],
        "crypto": [q.model_dump() for q in crypto],
        "stocks": [q.model_dump() for q in stocks],
    }
