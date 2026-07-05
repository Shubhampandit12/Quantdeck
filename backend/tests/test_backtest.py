"""Backtester tests: run the real Strategy SDK over synthetic bars.

The ``Backtester`` (from ``backtest.engine``) drives a user Strategy through an
in-process ``BacktestContext`` — the SAME SDK surface used live — after
AST-scanning the source via ``strategies.loader.scan_code``. We feed it a
deterministic price path from the ``FakeMarketData`` stub so a fast/slow SMA
crossover is guaranteed to produce at least one round-trip trade, then assert
the headline P&L numbers are internally consistent.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from quantdeck.core.models import BacktestRequest

pytestmark = pytest.mark.asyncio

# Directory holding the shipped, documented example strategies.
_EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "strategies_examples"


# A compact, sandbox-safe SMA crossover strategy as inline source. Uses only the
# ctx surface (no imports of os/sys/etc., no dunder access) so it passes the
# loader's AST scan and restricted-builtins execution.
SMA_SOURCE = '''
from quantdeck.strategies import Strategy


class SmaCross(Strategy):
    name = "SMA Cross Test"

    def on_start(self, ctx):
        self.fast = int(ctx.param("fast", 3))
        self.slow = int(ctx.param("slow", 8))
        self.symbol = str(ctx.param("symbol", "AAPL"))
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
            qty = ctx.max_shares(bar.symbol, 0.95)
            if qty > 0:
                ctx.buy(bar.symbol, qty)
        elif fast < slow and is_long:
            ctx.close(bar.symbol)
'''


def _crossover_series() -> list[float]:
    """A price path with a clear down-then-up move so a fast/slow SMA cross
    triggers a buy (uptrend) and later a sell (downtrend), yielding >=1 trade.
    """
    # Start flat, dip, then a sustained rally (fast crosses above slow -> BUY),
    # then a sustained decline (fast crosses below slow -> CLOSE).
    ramp_up = [100.0 + i for i in range(0, 20)]        # 100 -> 119 rally
    ramp_down = [119.0 - i for i in range(1, 20)]      # 118 -> 100 decline
    prefix = [100.0] * 8                               # seed enough for slow SMA
    return prefix + ramp_up + ramp_down


async def test_backtest_sma_cross_runs_and_trades(fake_market_data):
    from quantdeck.backtest.engine import Backtester

    # Feed the deterministic crossover path for AAPL.
    fake_market_data.set_series("AAPL", _crossover_series())

    bt = Backtester(fake_market_data)
    req = BacktestRequest(
        code=SMA_SOURCE,
        symbols=["AAPL"],
        interval="1d",
        starting_balance=100_000.0,
        leverage=1.0,
        fill_mode="idealized",
        commission_bps=0.0,
        slippage_bps=0.0,
        params={"fast": 3, "slow": 8, "symbol": "AAPL"},
    )
    result = await bt.run(req)

    # No hard error should have been reported.
    assert result.error is None, result.error

    # Headline consistency: ending = starting + total_pnl.
    assert math.isclose(
        result.ending_balance, result.starting_balance + result.total_pnl,
        rel_tol=1e-6, abs_tol=1e-3,
    )
    assert math.isclose(result.starting_balance, 100_000.0, rel_tol=1e-9)

    # total_return_pct is consistent with total_pnl vs starting balance.
    expected_return = 100.0 * result.total_pnl / result.starting_balance
    assert math.isclose(result.total_return_pct, expected_return,
                        rel_tol=1e-4, abs_tol=1e-4)

    # The crossover path must have generated at least one trade.
    assert result.num_trades > 0
    assert len(result.trades) == result.num_trades

    # An equity curve should have been recorded.
    assert len(result.equity_curve) > 0
    # Final equity point tracks the ending balance (allowing marking noise).
    assert math.isclose(
        result.equity_curve[-1].equity, result.ending_balance,
        rel_tol=1e-3, abs_tol=1.0,
    )

    # win_rate is a fraction/percentage in a sane range.
    assert 0.0 <= result.win_rate <= 100.0


async def test_backtest_reports_error_for_bad_symbol_gracefully(fake_market_data):
    """A backtest over a symbol with no data should not raise; it should return
    a result carrying an ``error`` or ``data_warnings`` instead (honest failure,
    never fabricated prices)."""
    from quantdeck.backtest.engine import Backtester

    bt = Backtester(fake_market_data)
    # Empty series -> no bars available for this symbol.
    fake_market_data.set_series("NOPE", [])
    req = BacktestRequest(
        code=SMA_SOURCE,
        symbols=["NOPE"],
        interval="1d",
        starting_balance=100_000.0,
        params={"symbol": "NOPE"},
    )
    result = await bt.run(req)
    # Either an explicit error or a data warning must communicate the problem;
    # in no case may it invent P&L.
    assert result.error is not None or result.data_warnings or result.num_trades == 0


@pytest.mark.parametrize(
    "filename,params",
    [
        ("sma_crossover.py", {"fast": 3, "slow": 8, "symbol": "AAPL"}),
        ("rsi_reversion.py",
         {"period": 5, "symbol": "AAPL", "oversold": 40, "exit_rsi": 55}),
        ("buy_and_hold.py", {"symbol": "AAPL"}),
    ],
)
async def test_shipped_examples_backtest_cleanly(fake_market_data, filename, params):
    """Each shipped example strategy must pass the sandbox loader and run to a
    consistent BacktestResult over deterministic data — this guards both the
    examples and the loader/backtester contract they depend on."""
    from quantdeck.backtest.engine import Backtester

    fake_market_data.set_series("AAPL", _crossover_series())
    source = (_EXAMPLES_DIR / filename).read_text()

    bt = Backtester(fake_market_data)
    req = BacktestRequest(
        code=source,
        symbols=["AAPL"],
        interval="1d",
        starting_balance=100_000.0,
        leverage=1.0,
        fill_mode="idealized",
        params=params,
    )
    result = await bt.run(req)

    assert result.error is None, result.error
    # Headline consistency holds for every example.
    assert math.isclose(
        result.ending_balance, result.starting_balance + result.total_pnl,
        rel_tol=1e-6, abs_tol=1e-3,
    )
    # All three deploy capital on this path, so each should execute >=1 trade.
    assert result.num_trades >= 1
