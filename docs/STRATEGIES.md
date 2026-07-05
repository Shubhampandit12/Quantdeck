# Writing Strategies

A strategy is a Python class that subclasses `quantdeck.strategies.Strategy` and
overrides lifecycle hooks. The **same file** runs in a backtest and live (paper)
— because both drive it through the identical `StrategyContext` surface.

## Minimal example

```python
from quantdeck.strategies import Strategy

class MyStrategy(Strategy):
    name = "My Strategy"

    def on_start(self, ctx):
        self.symbol = str(ctx.param("symbol", "BTC/USDT"))
        self.fast = int(ctx.param("fast", 10))
        self.slow = int(ctx.param("slow", 30))
        ctx.subscribe([self.symbol])

    def on_bar(self, ctx, bar):
        if bar.symbol != self.symbol:
            return
        closes = ctx.history(bar.symbol, "close", self.slow)
        if len(closes) < self.slow:
            return
        fast = sum(closes[-self.fast:]) / self.fast
        slow = sum(closes) / self.slow
        pos = ctx.get_position(bar.symbol)
        is_long = pos is not None and pos.side.value == "long"
        if fast > slow and not is_long:
            ctx.buy(bar.symbol, ctx.max_shares(bar.symbol, 0.95))
        elif fast < slow and is_long:
            ctx.close(bar.symbol)
```

## Lifecycle hooks (all optional)

| Hook | When |
|---|---|
| `on_start(ctx)` | once, before any data — subscribe + init state |
| `on_bar(ctx, bar)` | each newly-closed OHLCV bar for a subscribed symbol |
| `on_tick(ctx, quote)` | each streamed quote (live mode; may be sparse) |
| `on_order_update(ctx, order)` | when one of your orders changes state |
| `on_stop(ctx)` | once, on stop — clean up |

## The `ctx` (StrategyContext) API

**Data**
- `ctx.subscribe([symbols])`
- `ctx.price(symbol) -> float | None`
- `ctx.history(symbol, field="close", lookback=100) -> list[float]` (oldest→newest)
- `ctx.bars(symbol, lookback=100) -> list[Bar]`
- `ctx.quote(symbol) -> Quote | None`

**Account**
- `ctx.get_position(symbol) -> Position | None`  (`pos.side.value` is `"long"`/`"short"`/`"flat"`)
- `ctx.positions() -> list[Position]`
- `ctx.cash`, `ctx.equity`, `ctx.buying_power`
- `ctx.max_shares(symbol, fraction=1.0) -> float`

**Orders** (all go through the same risk-check path as the UI/AI)
- `ctx.buy(symbol, qty, order_type="market", limit_price=?, stop_price=?, take_profit=?, stop_loss=?)`
- `ctx.sell(symbol, qty, ...)`
- `ctx.close(symbol)` — flatten at market
- `ctx.cancel(order_id)`
- `ctx.set_leverage(leverage)`

**Misc**
- `ctx.log(*args)` — appears in the strategy log panel
- `ctx.param(key, default)` — read a param passed from the UI / backtest request

## Sandbox rules (important)

Injected code runs in a restricted sandbox. You **may** import: `math`,
`statistics`, `datetime`, `collections`, `random`, `itertools`, `functools`,
`decimal`, and `quantdeck.strategies`. Ordinary `try/except`, `raise`, and the
common exception types are allowed. Compute your indicators from the raw
`ctx.history(...)` lists using plain Python + `math`/`statistics` (see the
shipped examples — the RSI/SMA examples do exactly this).

You **may not**: import `os`/`sys`/`subprocess`/`socket`/`shutil`/etc., call
`open`/`eval`/`exec`/`__import__`, use `from __future__ import ...`, or reach
through dunder attributes (`__globals__`, `__subclasses__`, …). These are
rejected before your code runs.

> **Why not numpy/pandas?** Although importing them would pass a syntax scan,
> their file-IO and (de)serialization surfaces — e.g. `pd.read_pickle` (arbitrary
> code execution via pickle) and `pd.read_csv`/`np.load`/`ndarray.tofile`
> (arbitrary host filesystem read/write) — are ordinary library calls the
> sandbox cannot constrain. They are therefore **not** on the import allowlist.
> This is a deliberate security decision for running untrusted code.

## Backtest vs live

- **Backtest:** `POST /api/backtest` with `{ code, symbols, interval, start, end,
  starting_balance, leverage, commission_bps, slippage_bps, params }`. The result
  leads with `total_pnl` and `total_return_pct`.
- **Live paper:** save the strategy (`POST /api/strategies`), then
  `POST /api/strategies/{id}/start` with a portfolio id. Watch logs + P&L in the
  terminal's Strategy Logs tab.

Given the same historical data, a strategy produces the same result in both
modes — the engine's accounting/matching math is shared.

See runnable examples in `backend/strategies_examples/`.
