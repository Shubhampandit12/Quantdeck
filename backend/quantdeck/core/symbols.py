"""Symbol helpers: classify a symbol into an asset class and normalize form.

Canonical forms:
  - US equities: uppercase ticker, e.g. "AAPL", "BRK.B".
  - Crypto: "BASE/QUOTE" uppercase, e.g. "BTC/USDT", "ETH/USDT".
"""
from __future__ import annotations

from .enums import AssetClass

# Common crypto quote currencies used to detect a crypto pair without a slash.
_CRYPTO_QUOTES = ("USDT", "USDC", "USD", "BTC", "ETH", "BUSD", "FDUSD")


def normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if "/" in s:
        base, _, quote = s.partition("/")
        return f"{base}/{quote}"
    return s


def classify(symbol: str) -> AssetClass:
    """Best-effort classification.

    A slash always means crypto. A bare token ending in a known crypto quote
    (e.g. 'BTCUSDT') is treated as crypto. Everything else is a US equity.
    """
    s = symbol.strip().upper()
    if "/" in s:
        return AssetClass.CRYPTO
    for q in _CRYPTO_QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return AssetClass.CRYPTO
    return AssetClass.US_EQUITY


def to_crypto_pair(symbol: str) -> str:
    """Coerce 'BTCUSDT' -> 'BTC/USDT'; leave 'BTC/USDT' unchanged."""
    s = symbol.strip().upper()
    if "/" in s:
        return s
    for q in _CRYPTO_QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return f"{s[:-len(q)]}/{q}"
    return s
