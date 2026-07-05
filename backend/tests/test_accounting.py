"""Unit tests for the engine's pure accounting math (``engine/accounting.py``).

These functions are pure — numbers in, deterministic numbers out — so we can
assert exact values for long/short average entry, realized P&L on reduce/close/
flip, unrealized mark-to-market, buying power under leverage, margin, and the
liquidation-price direction. The engine builds all of its stateful behavior on
top of these, so pinning them down here guards the whole accounting layer.
"""
from __future__ import annotations

import math

import pytest

from quantdeck.core.enums import OrderSide, PositionSide

accounting = pytest.importorskip(
    "quantdeck.engine.accounting",
    reason="engine/accounting.py not present",
)

PositionState = accounting.PositionState
apply_fill = accounting.apply_fill


# --------------------------------------------------------------------------- #
# Average entry via apply_fill (extend case)                                  #
# --------------------------------------------------------------------------- #
def test_long_avg_entry_reweighted_on_add():
    flat = PositionState()
    # Buy 10 @ 100 (open), then buy 10 @ 120 (extend) -> avg 110, qty 20.
    after_first = apply_fill(flat, OrderSide.BUY, 10.0, 100.0).position
    assert after_first.side == PositionSide.LONG
    assert math.isclose(after_first.avg_entry_price, 100.0, rel_tol=1e-9)

    res = apply_fill(after_first, OrderSide.BUY, 10.0, 120.0)
    assert res.realized_pnl == 0.0  # extending never books P&L
    assert res.position.side == PositionSide.LONG
    assert math.isclose(res.position.quantity, 20.0, rel_tol=1e-9)
    assert math.isclose(res.position.avg_entry_price, 110.0, rel_tol=1e-9)


def test_short_avg_entry_reweighted_on_add():
    flat = PositionState()
    # Short 5 @ 200 (open), then short 5 @ 210 (extend) -> avg 205, qty 10.
    after_first = apply_fill(flat, OrderSide.SELL, 5.0, 200.0).position
    assert after_first.side == PositionSide.SHORT
    res = apply_fill(after_first, OrderSide.SELL, 5.0, 210.0)
    assert res.realized_pnl == 0.0
    assert res.position.side == PositionSide.SHORT
    assert math.isclose(res.position.quantity, 10.0, rel_tol=1e-9)
    assert math.isclose(res.position.avg_entry_price, 205.0, rel_tol=1e-9)


# --------------------------------------------------------------------------- #
# Realized P&L on reduce / close / flip                                       #
# --------------------------------------------------------------------------- #
def test_realized_pnl_on_full_close_long():
    pos = PositionState(side=PositionSide.LONG, quantity=10.0, avg_entry_price=100.0)
    # Sell all 10 @ 110 -> +100 realized, flat afterwards.
    res = apply_fill(pos, OrderSide.SELL, 10.0, 110.0)
    assert math.isclose(res.realized_pnl, 100.0, rel_tol=1e-9)
    assert res.position.side == PositionSide.FLAT
    assert res.position.quantity == 0.0


def test_realized_pnl_on_partial_reduce_long():
    pos = PositionState(side=PositionSide.LONG, quantity=10.0, avg_entry_price=100.0)
    # Sell 4 @ 110 -> +40 realized; 6 remain at the SAME entry price.
    res = apply_fill(pos, OrderSide.SELL, 4.0, 110.0)
    assert math.isclose(res.realized_pnl, 40.0, rel_tol=1e-9)
    assert res.position.side == PositionSide.LONG
    assert math.isclose(res.position.quantity, 6.0, rel_tol=1e-9)
    assert math.isclose(res.position.avg_entry_price, 100.0, rel_tol=1e-9)


def test_realized_pnl_on_close_short():
    pos = PositionState(side=PositionSide.SHORT, quantity=8.0, avg_entry_price=200.0)
    # Cover all 8 @ 190 -> short earns (entry - exit) * qty = (200-190)*8 = +80.
    res = apply_fill(pos, OrderSide.BUY, 8.0, 190.0)
    assert math.isclose(res.realized_pnl, 80.0, rel_tol=1e-9)
    assert res.position.side == PositionSide.FLAT


def test_flip_long_to_short_books_pnl_and_opens_remainder():
    pos = PositionState(side=PositionSide.LONG, quantity=5.0, avg_entry_price=100.0)
    # Sell 8 @ 110: closes 5 long (+50) then opens 3 short @ 110.
    res = apply_fill(pos, OrderSide.SELL, 8.0, 110.0)
    assert math.isclose(res.realized_pnl, 50.0, rel_tol=1e-9)
    assert res.position.side == PositionSide.SHORT
    assert math.isclose(res.position.quantity, 3.0, rel_tol=1e-9)
    assert math.isclose(res.position.avg_entry_price, 110.0, rel_tol=1e-9)


# --------------------------------------------------------------------------- #
# Unrealized P&L                                                              #
# --------------------------------------------------------------------------- #
def test_unrealized_pnl_long_and_short():
    long_pnl = accounting.unrealized_pnl(PositionSide.LONG, 10.0, 100.0, 110.0)
    short_pnl = accounting.unrealized_pnl(PositionSide.SHORT, 10.0, 100.0, 110.0)
    flat_pnl = accounting.unrealized_pnl(PositionSide.FLAT, 0.0, 0.0, 110.0)
    assert math.isclose(long_pnl, 100.0, rel_tol=1e-9)     # +10/share on 10
    assert math.isclose(short_pnl, -100.0, rel_tol=1e-9)   # short loses on a rise
    assert flat_pnl == 0.0


def test_market_value_and_notional():
    assert math.isclose(accounting.market_value(10.0, 110.0), 1_100.0, rel_tol=1e-9)
    assert math.isclose(accounting.position_notional(4.0, 250.0), 1_000.0, rel_tol=1e-9)


# --------------------------------------------------------------------------- #
# Buying power / margin / equity                                              #
# --------------------------------------------------------------------------- #
def test_buying_power_with_leverage_and_margin():
    # equity 100k, 3x leverage, 50k margin used -> 250k buying power.
    bp = accounting.buying_power(100_000.0, 3.0, 50_000.0)
    assert math.isclose(bp, 250_000.0, rel_tol=1e-9)


def test_buying_power_no_leverage_no_margin():
    bp = accounting.buying_power(100_000.0, 1.0, 0.0)
    assert math.isclose(bp, 100_000.0, rel_tol=1e-9)


def test_buying_power_clamped_at_zero():
    # Used margin exceeding leveraged equity must not go negative.
    bp = accounting.buying_power(10_000.0, 1.0, 25_000.0)
    assert bp == 0.0


def test_margin_used_scales_with_leverage():
    # 100k notional at 4x -> 25k margin.
    assert math.isclose(accounting.margin_used(100_000.0, 4.0), 25_000.0, rel_tol=1e-9)
    # Guarded against non-positive leverage (treated as 1x).
    assert math.isclose(accounting.margin_used(100_000.0, 0.0), 100_000.0, rel_tol=1e-9)


def test_portfolio_equity_is_cash_plus_unrealized():
    assert math.isclose(
        accounting.portfolio_equity(100_000.0, 2_500.0), 102_500.0, rel_tol=1e-9
    )


def test_maintenance_margin():
    assert math.isclose(
        accounting.maintenance_margin(100_000.0, 0.005), 500.0, rel_tol=1e-9
    )


# --------------------------------------------------------------------------- #
# Liquidation price                                                           #
# --------------------------------------------------------------------------- #
def test_liquidation_price_direction():
    # Leveraged long liquidates BELOW entry; short liquidates ABOVE entry.
    long_liq = accounting.liquidation_price(PositionSide.LONG, 10.0, 100.0, 5.0)
    short_liq = accounting.liquidation_price(PositionSide.SHORT, 10.0, 100.0, 5.0)
    assert long_liq is not None and long_liq < 100.0
    assert short_liq is not None and short_liq > 100.0


def test_liquidation_price_none_for_unlevered_long_and_flat():
    # A 1x long cannot be liquidated by price alone -> None.
    assert accounting.liquidation_price(PositionSide.LONG, 10.0, 100.0, 1.0) is None
    # Flat -> None.
    assert accounting.liquidation_price(PositionSide.FLAT, 0.0, 0.0, 5.0) is None
