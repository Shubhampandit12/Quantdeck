"""QuantDeck — AI-driven paper-trading platform (demo money only).

Hard invariant enforced throughout this package: NO real money, ever. There is
no code path, config flag, or credential that can route an order to a real
broker or exchange. The only external credentials permitted are read-only
market-data API keys.
"""

__version__ = "0.1.0"
