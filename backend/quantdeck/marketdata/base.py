"""Market-data provider interface + the MarketDataService contract.

This file is the FROZEN CONTRACT between the market-data layer and everything
that consumes prices (engine, API, MCP, strategy runtime). Provider
adapters implement `MarketDataProvider`; the rest of the platform only ever
touches a `MarketDataService` instance (see `.service.get_market_data`).
"""
from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from typing import Optional, Protocol, runtime_checkable

from ..core.enums import AssetClass
from ..core.models import OHLCVResponse, Quote, SymbolInfo

# Canonical intervals the platform understands. Providers map these to their own.
VALID_INTERVALS = ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w")


class MarketDataProvider(abc.ABC):
    """A single data source (Yahoo, Finnhub, Alpaca, ...)."""

    name: str = "provider"
    asset_classes: tuple[AssetClass, ...] = ()

    @abc.abstractmethod
    async def get_quote(self, symbol: str) -> Quote:
        ...

    @abc.abstractmethod
    async def get_ohlcv(
        self,
        symbol: str,
        interval: str,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
    ) -> OHLCVResponse:
        ...

    async def search_symbols(self, query: str) -> list[SymbolInfo]:
        return []

    async def stream_quotes(
        self, symbols: list[str]
    ) -> AsyncIterator[Quote]:  # pragma: no cover - optional capability
        """Optional realtime stream. Providers without streaming should not
        override this; the service falls back to REST polling."""
        raise NotImplementedError
        yield  # make this an async generator for typing

    async def aclose(self) -> None:  # pragma: no cover - optional
        return None


@runtime_checkable
class MarketData(Protocol):
    """The consumer-facing surface. `MarketDataService` fulfills this.

    Consumers (engine/API/MCP/strategy) type against THIS, never a concrete
    provider. Keeps providers swappable.
    """

    async def get_quote(self, symbol: str) -> Quote: ...

    async def get_price(self, symbol: str) -> float: ...

    async def get_ohlcv(
        self,
        symbol: str,
        interval: str,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
    ) -> OHLCVResponse: ...

    async def search_symbols(self, query: str, limit: int = 25) -> list[SymbolInfo]: ...

    async def get_symbols(
        self, asset_class: Optional[AssetClass] = None
    ) -> list[SymbolInfo]: ...

    async def subscribe(self, symbols: list[str]) -> AsyncIterator[Quote]: ...

    async def start(self) -> None: ...

    async def aclose(self) -> None: ...
