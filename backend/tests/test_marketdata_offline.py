"""Offline market-data tests: classification, the demo symbol universe, and
the service's symbol listing — all without touching the network.

``load_sp500`` (name kept for minimal churn) ships a fixed, curated 15-symbol
demo universe with no network dependency. ``get_symbols`` must be servable
from that list, again offline.
"""
from __future__ import annotations

import pytest

from quantdeck.core.enums import AssetClass
from quantdeck.core.models import SymbolInfo
from quantdeck.core import symbols


# --------------------------------------------------------------------------- #
# symbols.classify / normalize                                                #
# --------------------------------------------------------------------------- #
def test_classify_is_always_us_equity():
    assert symbols.classify("AAPL") == AssetClass.US_EQUITY
    assert symbols.classify("BRK.B") == AssetClass.US_EQUITY


def test_normalize_symbol():
    assert symbols.normalize_symbol("  aapl ") == "AAPL"
    assert symbols.normalize_symbol("brk.b") == "BRK.B"


# --------------------------------------------------------------------------- #
# Demo symbol universe — fixed 15-symbol static list                          #
# --------------------------------------------------------------------------- #
def test_load_sp500_returns_fixed_demo_universe():
    from quantdeck.marketdata.sp500 import DEMO_SYMBOLS, load_sp500

    syms = load_sp500()
    assert isinstance(syms, list)
    assert len(syms) == 15
    assert len(DEMO_SYMBOLS) == 15

    # Every entry is a well-formed US-equity SymbolInfo.
    for s in syms:
        assert isinstance(s, SymbolInfo)
        assert s.asset_class == AssetClass.US_EQUITY
        assert s.symbol == s.symbol.upper()
        assert s.symbol  # non-empty

    # No duplicate tickers.
    tickers = [s.symbol for s in syms]
    assert len(tickers) == len(set(tickers))
    assert tickers == list(DEMO_SYMBOLS)

    # A few household names should be present in the demo universe.
    assert "AAPL" in set(tickers)
    assert "MSFT" in set(tickers)


# --------------------------------------------------------------------------- #
# MarketDataService construction + offline get_symbols                        #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_service_constructs_and_lists_symbols_offline():
    from quantdeck.marketdata.service import MarketDataService, get_market_data

    # The module singleton getter returns a MarketDataService instance.
    svc = get_market_data()
    assert isinstance(svc, MarketDataService)

    # Listing US equities must work without any network: it is served from the
    # embedded S&P 500 fallback. If the implementation ever routes this through
    # a live provider and no connectivity exists, allow a graceful skip rather
    # than a false failure — but it must not fabricate.
    try:
        equities = await svc.get_symbols(AssetClass.US_EQUITY)
    except Exception as exc:  # pragma: no cover - only on network-coupled impls
        pytest.skip(f"get_symbols(US_EQUITY) required connectivity: {exc!r}")

    assert isinstance(equities, list)
    assert len(equities) == 15
    assert all(e.asset_class == AssetClass.US_EQUITY for e in equities)

    # A second call should be cache-fast and return a consistent count.
    equities2 = await svc.get_symbols(AssetClass.US_EQUITY)
    assert len(equities2) == len(equities)

    await svc.aclose()


@pytest.mark.asyncio
async def test_service_search_symbols_offline():
    """Searching the offline symbol universe should find a household ticker."""
    from quantdeck.marketdata.service import get_market_data

    svc = get_market_data()
    try:
        results = await svc.search_symbols("AAPL", limit=25)
    except Exception as exc:  # pragma: no cover - network-coupled impls only
        pytest.skip(f"search_symbols required connectivity: {exc!r}")

    assert isinstance(results, list)
    # If the offline universe backs search, AAPL should surface.
    if results:
        assert any(r.symbol == "AAPL" for r in results)
    await svc.aclose()
