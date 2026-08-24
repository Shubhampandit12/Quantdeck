"""Symbol helpers: classify a symbol into an asset class and normalize form.

Canonical form:
  - US equities: uppercase ticker, e.g. "AAPL", "BRK.B".
"""
from __future__ import annotations

from .enums import AssetClass


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def classify(symbol: str) -> AssetClass:
    """Every symbol on this platform is a US equity."""
    return AssetClass.US_EQUITY
