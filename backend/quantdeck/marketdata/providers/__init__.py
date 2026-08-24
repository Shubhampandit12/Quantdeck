"""Concrete market-data provider adapters.

Each module here implements :class:`quantdeck.marketdata.base.MarketDataProvider`
for one upstream source. The :class:`~quantdeck.marketdata.service.MarketDataService`
picks the first working provider and never leaks provider details to the rest
of the platform.

Providers, at a glance:
  - YahooProvider   : yfinance (blocking, run in a thread). US equity, ~15m delayed.
  - FinnhubProvider : Finnhub REST + optional websocket. US equity, realtime (keyed).
  - AlpacaProvider  : Alpaca free IEX data REST. US equity, realtime IEX (keyed).
"""
from __future__ import annotations

from .alpaca import AlpacaProvider
from .finnhub import FinnhubProvider
from .yahoo import YahooProvider

__all__ = [
    "AlpacaProvider",
    "FinnhubProvider",
    "YahooProvider",
]
