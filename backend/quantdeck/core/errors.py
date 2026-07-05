"""Typed exceptions used across the platform."""
from __future__ import annotations


class QuantDeckError(Exception):
    """Base class for all platform errors."""


class DataUnavailableError(QuantDeckError):
    """A provider genuinely cannot supply data for a symbol/range.

    Callers must surface an honest 'data unavailable' state — never fabricate.
    """


class ProviderRateLimited(QuantDeckError):
    """A provider returned a rate-limit (429) response."""


class UnknownSymbolError(QuantDeckError):
    pass


class OrderRejected(QuantDeckError):
    """Raised (or returned) when the shared risk-check path rejects an order."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class InsufficientBuyingPower(OrderRejected):
    pass


class RiskLimitExceeded(OrderRejected):
    pass


class PortfolioNotFound(QuantDeckError):
    pass


class StrategyError(QuantDeckError):
    pass


class SandboxViolation(StrategyError):
    """User strategy attempted a disallowed operation."""
