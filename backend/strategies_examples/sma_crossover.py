"""SMA crossover example strategy.

A textbook trend-following strategy: track a fast and a slow simple moving
average of the close price. When the fast average rises above the slow average
we treat it as a bullish signal and go long; when the fast average falls back
below the slow average we flatten the position.

The strategy touches nothing but the ``ctx`` (StrategyContext) surface and runs
in live-paper mode (via ``strategies.runner``). It is also sandbox-safe: no
imports, no filesystem or network access, no dunder tricks.

Parameters (read via ``ctx.param``):
  * ``fast``    fast SMA window (default 10)
  * ``slow``    slow SMA window (default 30)
  * ``symbol``  symbol to trade (default "AAPL")
  * ``fraction`` fraction of buying power to deploy per entry (default 0.95)

NOTE: user strategies run under the sandbox loader, which only allows imports
from a curated allowlist (``math``, ``statistics``, ``numpy``, ``pandas``,
``quantdeck.strategies``, ...). In particular ``from __future__ import ...`` is
NOT permitted, so this file deliberately avoids it.
"""
from quantdeck.strategies import Strategy


class SmaCrossover(Strategy):
    """Long-only fast/slow simple-moving-average crossover."""

    name = "SMA Crossover"

    def on_start(self, ctx) -> None:
        # Read tunables from the strategy params (set from the UI).
        self.fast = int(ctx.param("fast", 10))
        self.slow = int(ctx.param("slow", 30))
        self.symbol = str(ctx.param("symbol", "AAPL"))
        self.fraction = float(ctx.param("fraction", 0.95))
        ctx.subscribe([self.symbol])
        ctx.log(f"SMA crossover started fast={self.fast} slow={self.slow} "
                f"symbol={self.symbol}")

    def on_bar(self, ctx, bar) -> None:
        # We only act on bars for the symbol we trade.
        if bar.symbol != self.symbol:
            return

        # Need at least `slow` closes to compute both averages.
        closes = ctx.history(bar.symbol, "close", self.slow)
        if len(closes) < self.slow:
            return

        fast_avg = sum(closes[-self.fast:]) / self.fast
        slow_avg = sum(closes) / self.slow

        pos = ctx.get_position(bar.symbol)
        is_long = pos is not None and pos.side.value == "long"

        if fast_avg > slow_avg and not is_long:
            # Bullish crossover — enter long with a slice of buying power.
            qty = ctx.max_shares(bar.symbol, self.fraction)
            if qty > 0:
                ctx.log(f"BUY {bar.symbol} qty={qty:.4f} "
                        f"fast={fast_avg:.2f} > slow={slow_avg:.2f}")
                ctx.buy(bar.symbol, qty)
        elif fast_avg < slow_avg and is_long:
            # Bearish crossover — flatten the long.
            ctx.log(f"CLOSE {bar.symbol} fast={fast_avg:.2f} < slow={slow_avg:.2f}")
            ctx.close(bar.symbol)

    def on_stop(self, ctx) -> None:
        ctx.log("SMA crossover stopped")
