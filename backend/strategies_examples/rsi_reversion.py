"""RSI mean-reversion example strategy.

Computes the Relative Strength Index (RSI) from the recent close history and
trades a simple reversion rule:

  * enter LONG when RSI dips below an oversold threshold (default 30),
  * exit when RSI recovers above an exit threshold (default 55), OR when price
    drops through a protective percentage stop below the entry.

Like every example, it speaks only the ``StrategyContext`` (``ctx``) surface so
it runs identically live and in backtest, and it is sandbox-safe (no imports of
os/sys/etc., no file or network IO, no dunder access).

Parameters (read via ``ctx.param``):
  * ``period``     RSI lookback window (default 14)
  * ``oversold``   entry threshold (default 30.0)
  * ``exit_rsi``   exit threshold (default 55.0)
  * ``stop_pct``   protective stop as a fraction below entry (default 0.05)
  * ``symbol``     symbol to trade (default "AAPL")
  * ``fraction``   fraction of buying power per entry (default 0.95)

NOTE: this runs under the sandbox loader's import allowlist, so it avoids a
``from __future__ import ...`` line (which the scanner rejects) and imports only
``quantdeck.strategies``.
"""
from quantdeck.strategies import Strategy


def _rsi(closes: list[float], period: int) -> float:
    """Classic Wilder-style RSI over the last ``period`` price changes.

    Returns a value in [0, 100]. Requires ``period + 1`` closes; callers must
    guard length before relying on the result.
    """
    # Build successive up/down moves over the most recent window.
    gains = 0.0
    losses = 0.0
    window = closes[-(period + 1):]
    for prev, cur in zip(window[:-1], window[1:]):
        delta = cur - prev
        if delta >= 0:
            gains += delta
        else:
            losses -= delta  # store as a positive magnitude

    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        # No downside over the window -> maximally strong.
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


class RsiReversion(Strategy):
    """Buy oversold dips, exit on RSI recovery or a protective stop."""

    name = "RSI Reversion"

    def on_start(self, ctx) -> None:
        self.period = int(ctx.param("period", 14))
        self.oversold = float(ctx.param("oversold", 30.0))
        self.exit_rsi = float(ctx.param("exit_rsi", 55.0))
        self.stop_pct = float(ctx.param("stop_pct", 0.05))
        self.symbol = str(ctx.param("symbol", "AAPL"))
        self.fraction = float(ctx.param("fraction", 0.95))
        # Remember the entry price so we can enforce a percentage stop.
        self.entry_price = 0.0
        ctx.subscribe([self.symbol])
        ctx.log(f"RSI reversion started period={self.period} "
                f"oversold={self.oversold} exit={self.exit_rsi}")

    def on_bar(self, ctx, bar) -> None:
        if bar.symbol != self.symbol:
            return

        closes = ctx.history(bar.symbol, "close", self.period + 1)
        if len(closes) < self.period + 1:
            return

        rsi = _rsi(closes, self.period)
        pos = ctx.get_position(bar.symbol)
        is_long = pos is not None and pos.side.value == "long"

        if not is_long:
            # Look for an oversold entry.
            if rsi < self.oversold:
                qty = ctx.max_shares(bar.symbol, self.fraction)
                if qty > 0:
                    self.entry_price = bar.close
                    ctx.log(f"BUY {bar.symbol} qty={qty:.4f} rsi={rsi:.1f}")
                    ctx.buy(bar.symbol, qty)
        else:
            # Manage the open long: exit on RSI recovery or a stop breach.
            stop_hit = (
                self.entry_price > 0.0
                and bar.close <= self.entry_price * (1.0 - self.stop_pct)
            )
            if rsi > self.exit_rsi:
                ctx.log(f"CLOSE {bar.symbol} rsi={rsi:.1f} (recovery)")
                ctx.close(bar.symbol)
                self.entry_price = 0.0
            elif stop_hit:
                ctx.log(f"CLOSE {bar.symbol} stop hit @ {bar.close:.2f}")
                ctx.close(bar.symbol)
                self.entry_price = 0.0

    def on_stop(self, ctx) -> None:
        ctx.log("RSI reversion stopped")
