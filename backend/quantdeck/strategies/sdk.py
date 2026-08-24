"""Strategy SDK — the public API user code writes against.

FROZEN CONTRACT. A user strategy looks like:

    from quantdeck.strategies import Strategy

    class MyStrategy(Strategy):
        def on_start(self, ctx):
            ctx.subscribe(["AAPL"])
            self.fast, self.slow = 10, 30

        def on_bar(self, ctx, bar):
            closes = ctx.history(bar.symbol, "close", self.slow)
            if len(closes) < self.slow:
                return
            fast = sum(closes[-self.fast:]) / self.fast
            slow = sum(closes) / self.slow
            pos = ctx.get_position(bar.symbol)
            if fast > slow and (pos is None or pos.side.value != "long"):
                ctx.buy(bar.symbol, ctx.max_shares(bar.symbol, 0.95))
            elif fast < slow and pos is not None and pos.side.value == "long":
                ctx.close(bar.symbol)

The `ctx` object is provided by the runtime (live) or the backtester. Both
implement `StrategyContext`, so identical code runs in both. Hooks are plain
(sync) methods — the runtime bridges to the async engine.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, Sequence, runtime_checkable

from ..core.models import Bar, Order, Position, Quote


@runtime_checkable
class StrategyContext(Protocol):
    """Everything a strategy is allowed to touch. No filesystem/network here —
    all IO goes through these methods, which the sandbox mediates."""

    # --- Subscriptions / data ---
    def subscribe(self, symbols: Sequence[str]) -> None: ...

    def price(self, symbol: str) -> Optional[float]:
        """Latest known price for a symbol."""
        ...

    def history(
        self, symbol: str, field: str = "close", lookback: int = 100
    ) -> list[float]:
        """Recent values of an OHLCV field, oldest→newest, up to `lookback`."""
        ...

    def bars(self, symbol: str, lookback: int = 100) -> list[Bar]: ...

    def quote(self, symbol: str) -> Optional[Quote]: ...

    # --- Account ---
    def get_position(self, symbol: str) -> Optional[Position]: ...

    def positions(self) -> list[Position]: ...

    @property
    def cash(self) -> float: ...

    @property
    def equity(self) -> float: ...

    @property
    def buying_power(self) -> float: ...

    def max_shares(self, symbol: str, fraction: float = 1.0) -> float:
        """How many units of `symbol` `fraction` of buying power can buy."""
        ...

    # --- Orders ---
    def buy(
        self,
        symbol: str,
        quantity: float,
        *,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> Optional[Order]: ...

    def sell(
        self,
        symbol: str,
        quantity: float,
        *,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> Optional[Order]: ...

    def close(self, symbol: str) -> Optional[Order]:
        """Flatten the position in `symbol` at market."""
        ...

    def cancel(self, order_id: str) -> None: ...

    def set_leverage(self, leverage: float) -> None: ...

    # --- Misc ---
    def log(self, *args: Any) -> None: ...

    def param(self, key: str, default: Any = None) -> Any:
        """Read a strategy parameter passed in from the UI/backtest request."""
        ...


class Strategy:
    """Base class for user strategies. Override the hooks you need.

    All hooks are optional and no-ops by default, so a minimal strategy only
    implements what it uses.
    """

    #: Human-readable name; runtime falls back to the class name.
    name: str = ""

    def on_start(self, ctx: StrategyContext) -> None:
        """Called once before any bars/ticks. Subscribe + init state here."""

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        """Called on each newly-closed OHLCV bar for a subscribed symbol."""

    def on_tick(self, ctx: StrategyContext, quote: Quote) -> None:
        """Called on each streamed quote (live mode; may be sparse)."""

    def on_order_update(self, ctx: StrategyContext, order: Order) -> None:
        """Called when one of this strategy's orders changes state."""

    def on_stop(self, ctx: StrategyContext) -> None:
        """Called once when the strategy is stopped. Clean up here."""
