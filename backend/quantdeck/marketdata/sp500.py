"""Fixed demo symbol universe.

QuantDeck is a showcase for AI/MCP-driven trading, not a full-market
simulator, so the tradable universe is deliberately capped at 15 well-known,
sector-diverse US equities rather than the full S&P 500. ``load_sp500()``
(name kept for minimal call-site churn) returns exactly these 15 as
:class:`SymbolInfo`, with no live/network fetch — the list is small and
curated enough that there is nothing to fetch.

``DEMO_SYMBOLS`` (the bare ticker list) is also the default order-placement
allowlist for the app's auto-created portfolio; see
``api/deps.py::_ensure_default_portfolio``.
"""
from __future__ import annotations

from ..core.enums import AssetClass
from ..core.models import SymbolInfo

# --------------------------------------------------------------------------- #
# The fixed 15-symbol demo universe: (symbol, company name, exchange).        #
# Sector-diverse mega-caps, real tickers, no synthetic data.                  #
# --------------------------------------------------------------------------- #
_DEMO_STATIC: tuple[tuple[str, str, str], ...] = (
    ("AAPL", "Apple Inc.", "NASDAQ"),
    ("MSFT", "Microsoft Corporation", "NASDAQ"),
    ("NVDA", "NVIDIA Corporation", "NASDAQ"),
    ("AMZN", "Amazon.com, Inc.", "NASDAQ"),
    ("GOOGL", "Alphabet Inc. Class A", "NASDAQ"),
    ("META", "Meta Platforms, Inc.", "NASDAQ"),
    ("TSLA", "Tesla, Inc.", "NASDAQ"),
    ("BRK.B", "Berkshire Hathaway Inc. Class B", "NYSE"),
    ("LLY", "Eli Lilly and Company", "NYSE"),
    ("AVGO", "Broadcom Inc.", "NASDAQ"),
    ("JPM", "JPMorgan Chase & Co.", "NYSE"),
    ("UNH", "UnitedHealth Group Incorporated", "NYSE"),
    ("V", "Visa Inc.", "NYSE"),
    ("JNJ", "Johnson & Johnson", "NYSE"),
    ("XOM", "Exxon Mobil Corporation", "NYSE"),
)

DEMO_SYMBOLS: tuple[str, ...] = tuple(sym for sym, _, _ in _DEMO_STATIC)


def load_sp500() -> list[SymbolInfo]:
    """Return the fixed 15-symbol demo universe as SymbolInfo."""
    return [
        SymbolInfo(
            symbol=sym,
            name=name,
            asset_class=AssetClass.US_EQUITY,
            exchange=exch,
            provider_symbol=sym,
            tradable=True,
        )
        for sym, name, exch in _DEMO_STATIC
    ]
