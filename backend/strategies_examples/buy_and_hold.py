"""Buy-and-hold example strategy — the simplest possible strategy.

On the first bar it sees, it deploys a fraction of buying power into the target
symbol and then does nothing else, holding the position for the rest of the run.
Useful as a baseline benchmark to compare more active strategies against.

Only the ``StrategyContext`` surface is used, so it passes the sandbox AST scan.

Parameters (read via ``ctx.param``):
  * ``symbol``    symbol to buy and hold (default "AAPL")
  * ``fraction``  fraction of buying power to deploy (default 0.95)

NOTE: runs under the sandbox loader's import allowlist, so it avoids a
``from __future__ import ...`` line (rejected by the scanner).
"""
from quantdeck.strategies import Strategy


class BuyAndHold(Strategy):
    """Enter once on the first bar, then hold."""

    name = "Buy and Hold"

    def on_start(self, ctx) -> None:
        self.symbol = str(ctx.param("symbol", "AAPL"))
        self.fraction = float(ctx.param("fraction", 0.95))
        self.entered = False
        ctx.subscribe([self.symbol])
        ctx.log(f"Buy-and-hold started symbol={self.symbol}")

    def on_bar(self, ctx, bar) -> None:
        if self.entered or bar.symbol != self.symbol:
            return
        qty = ctx.max_shares(bar.symbol, self.fraction)
        if qty > 0:
            ctx.log(f"BUY {bar.symbol} qty={qty:.4f} @ {bar.close:.2f}")
            ctx.buy(bar.symbol, qty)
            self.entered = True
