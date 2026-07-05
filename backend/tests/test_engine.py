"""Integration-style tests for the ``TradingEngine`` via its frozen surface.

Everything here goes through ``EngineProtocol`` (place_order / on_quote /
flatten_all / reset_portfolio / ...), which is frozen, so the tests are stable
against internal refactors. All prices come from the deterministic
``FakeMarketData`` stub in ``conftest`` — no network, no randomness.

Portfolios in these tests use ``fill_mode="idealized"`` so market orders fill at
the exact quote and P&L math is easy to assert without slippage noise.
"""
from __future__ import annotations

import math

import pytest

from quantdeck.core.enums import OrderSide, OrderStatus, OrderType, PositionSide
from quantdeck.core.models import OrderRequest, RiskLimits

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Market buy: position opens; margin is locked and buying power falls.         #
# --------------------------------------------------------------------------- #
async def test_market_buy_opens_position_and_locks_margin(engine_obj, portfolio):
    # The engine models a MARGIN account: opening a position does not spend
    # settled cash — it locks margin (notional / leverage) and reduces buying
    # power. Cash only moves by realized P&L (+ commission) when a position is
    # reduced/closed. Verify that behavior here.
    starting_cash = (await engine_obj.get_portfolio(portfolio.id)).cash
    starting_bp = (await engine_obj.get_portfolio(portfolio.id)).buying_power

    # AAPL is 150.0 in the fake feed. Buy 10 shares.
    req = OrderRequest(symbol="AAPL", side=OrderSide.BUY,
                       type=OrderType.MARKET, quantity=10.0)
    order = await engine_obj.place_order(portfolio.id, req)

    assert order.status == OrderStatus.FILLED
    assert math.isclose(order.filled_quantity, 10.0, rel_tol=1e-9)
    assert order.avg_fill_price is not None
    assert math.isclose(order.avg_fill_price, 150.0, rel_tol=1e-9)  # idealized

    pos = await engine_obj.get_position(portfolio.id, "AAPL")
    assert pos is not None
    assert pos.side == PositionSide.LONG
    assert math.isclose(pos.quantity, 10.0, rel_tol=1e-9)
    assert math.isclose(pos.avg_entry_price, 150.0, rel_tol=1e-9)

    pf = await engine_obj.get_portfolio(portfolio.id)
    notional = 10.0 * order.avg_fill_price  # 1500
    # No realized P&L yet and commission-free -> cash is unchanged.
    assert math.isclose(pf.cash, starting_cash, rel_tol=1e-9)
    # Margin locked = notional / leverage (1x here) -> 1500.
    assert math.isclose(pf.margin_used, notional / pf.leverage, rel_tol=1e-6)
    # Buying power falls by the locked margin.
    assert pf.buying_power < starting_bp
    assert math.isclose(starting_bp - pf.buying_power, notional / pf.leverage,
                        rel_tol=1e-6, abs_tol=1e-6)
    # Fresh fill marked at entry -> equity is still ~ starting equity.
    assert math.isclose(pf.equity, starting_cash, rel_tol=1e-6)


# --------------------------------------------------------------------------- #
# Resting limit order fills when a quote crosses it                           #
# --------------------------------------------------------------------------- #
async def test_limit_order_rests_then_fills_on_quote_cross(
    engine_obj, portfolio, fake_market_data
):
    # Buy limit BELOW the current 150 -> should rest, not fill immediately.
    req = OrderRequest(
        symbol="AAPL", side=OrderSide.BUY, type=OrderType.LIMIT,
        quantity=5.0, limit_price=140.0,
    )
    order = await engine_obj.place_order(portfolio.id, req)
    assert order.status in (OrderStatus.OPEN, OrderStatus.PENDING)

    open_orders = await engine_obj.get_open_orders(portfolio.id)
    assert any(o.id == order.id for o in open_orders)

    # Price ticks down through the limit -> the resting order should fill.
    fake_market_data.set_price("AAPL", 139.0)
    await engine_obj.on_quote("AAPL", 139.0)

    filled = await engine_obj.get_order(portfolio.id, order.id)
    assert filled.status == OrderStatus.FILLED
    assert filled.avg_fill_price is not None
    # A buy limit must not fill worse than its limit price.
    assert filled.avg_fill_price <= 140.0 + 1e-9

    pos = await engine_obj.get_position(portfolio.id, "AAPL")
    assert pos is not None and pos.side == PositionSide.LONG
    assert math.isclose(pos.quantity, 5.0, rel_tol=1e-9)


# --------------------------------------------------------------------------- #
# Short sell then cover                                                        #
# --------------------------------------------------------------------------- #
async def test_short_sell_then_cover(engine_obj, portfolio, fake_market_data):
    # Sell 4 AAPL short at 150.
    sell = OrderRequest(symbol="AAPL", side=OrderSide.SELL,
                        type=OrderType.MARKET, quantity=4.0)
    o1 = await engine_obj.place_order(portfolio.id, sell)
    assert o1.status == OrderStatus.FILLED

    pos = await engine_obj.get_position(portfolio.id, "AAPL")
    assert pos is not None
    assert pos.side == PositionSide.SHORT
    assert math.isclose(pos.quantity, 4.0, rel_tol=1e-9)

    # Price drops to 140 -> short is in profit. Mark it.
    fake_market_data.set_price("AAPL", 140.0)
    await engine_obj.on_quote("AAPL", 140.0)
    pos = await engine_obj.get_position(portfolio.id, "AAPL")
    assert pos is not None
    # Short profits when price falls below entry.
    assert pos.unrealized_pnl > 0.0

    # Cover the short with a buy.
    cover = OrderRequest(symbol="AAPL", side=OrderSide.BUY,
                         type=OrderType.MARKET, quantity=4.0)
    o2 = await engine_obj.place_order(portfolio.id, cover)
    assert o2.status == OrderStatus.FILLED

    pos = await engine_obj.get_position(portfolio.id, "AAPL")
    # Fully covered -> flat (either a FLAT position or no position at all).
    assert pos is None or pos.side == PositionSide.FLAT or pos.quantity == 0.0

    pf = await engine_obj.get_portfolio(portfolio.id)
    # Sold at 150, covered at 140, 4 shares -> ~ +40 realized.
    assert pf.realized_pnl > 0.0


# --------------------------------------------------------------------------- #
# Leverage expands buying power                                               #
# --------------------------------------------------------------------------- #
async def test_leverage_expands_buying_power(engine_obj):
    pf = await engine_obj.create_portfolio(
        name="Levered", starting_balance=10_000.0,
        leverage=1.0, fill_mode="idealized",
    )
    base_bp = (await engine_obj.get_portfolio(pf.id)).buying_power

    updated = await engine_obj.set_leverage(pf.id, 5.0)
    assert math.isclose(updated.leverage, 5.0, rel_tol=1e-9)
    assert updated.buying_power > base_bp
    # With no positions, 5x leverage on 10k equity -> ~50k buying power.
    assert math.isclose(updated.buying_power, 50_000.0, rel_tol=1e-6)


# --------------------------------------------------------------------------- #
# Risk rejection: insufficient buying power -> REJECTED order, not an exception #
# --------------------------------------------------------------------------- #
async def test_insufficient_buying_power_returns_rejected(engine_obj):
    pf = await engine_obj.create_portfolio(
        name="Tiny", starting_balance=1_000.0,
        leverage=1.0, fill_mode="idealized",
    )
    # 100 BTC @ 40k = $4M notional on a $1k account. A LIMIT order carries an
    # explicit reference price so the shared buying-power check can size it and
    # reject before it ever rests. (A market order with no cached price cannot
    # be pre-sized; this test targets the deterministic sizing path.)
    req = OrderRequest(symbol="BTC/USDT", side=OrderSide.BUY,
                       type=OrderType.LIMIT, quantity=100.0, limit_price=40_000.0)
    order = await engine_obj.place_order(pf.id, req)

    assert order.status == OrderStatus.REJECTED
    assert order.reject_reason  # a human-readable reason is attached
    assert "buying power" in order.reject_reason.lower()
    # The rejected order must not have moved cash and must not rest.
    after = await engine_obj.get_portfolio(pf.id)
    assert math.isclose(after.cash, 1_000.0, rel_tol=1e-9)
    assert all(o.id != order.id for o in await engine_obj.get_open_orders(pf.id))


# --------------------------------------------------------------------------- #
# Risk rejection: exceeding the max_leverage risk limit                        #
# --------------------------------------------------------------------------- #
async def test_exceed_max_leverage_rejected(engine_obj):
    # Portfolio leverage 5x, but the risk limit caps effective leverage at 2x.
    pf = await engine_obj.create_portfolio(
        name="Capped", starting_balance=10_000.0,
        leverage=5.0, fill_mode="idealized",
        risk_limits=RiskLimits(max_leverage=2.0),
    )
    # 200 AAPL @ 150 = $30k gross. Equity*max_leverage = 10k*2 = 20k, so this
    # must be rejected for exceeding max leverage — even though the portfolio's
    # own 5x would otherwise allow up to $50k.
    over = OrderRequest(symbol="AAPL", side=OrderSide.BUY, type=OrderType.LIMIT,
                        quantity=200.0, limit_price=150.0)
    rejected = await engine_obj.place_order(pf.id, over)
    assert rejected.status == OrderStatus.REJECTED
    assert rejected.reject_reason and "leverage" in rejected.reject_reason.lower()

    # A $15k order (within the 2x = $20k cap) must be accepted.
    within = OrderRequest(symbol="MSFT", side=OrderSide.BUY, type=OrderType.LIMIT,
                          quantity=100.0, limit_price=150.0)
    ok = await engine_obj.place_order(pf.id, within)
    assert ok.status in (OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.FILLED)
    assert ok.reject_reason is None


# --------------------------------------------------------------------------- #
# reset_portfolio wipes state                                                 #
# --------------------------------------------------------------------------- #
async def test_reset_portfolio(engine_obj, portfolio):
    await engine_obj.place_order(
        portfolio.id,
        OrderRequest(symbol="AAPL", side=OrderSide.BUY,
                     type=OrderType.MARKET, quantity=10.0),
    )
    # Sanity: a position now exists.
    assert await engine_obj.get_position(portfolio.id, "AAPL") is not None

    reset = await engine_obj.reset_portfolio(portfolio.id, starting_balance=50_000.0)
    assert math.isclose(reset.cash, 50_000.0, rel_tol=1e-9)
    assert math.isclose(reset.starting_balance, 50_000.0, rel_tol=1e-9)

    positions = await engine_obj.get_positions(portfolio.id)
    assert all(p.side == PositionSide.FLAT or p.quantity == 0.0 for p in positions)
    assert await engine_obj.get_open_orders(portfolio.id) == []


# --------------------------------------------------------------------------- #
# flatten_all closes everything (kill-switch primitive)                        #
# --------------------------------------------------------------------------- #
async def test_flatten_all(engine_obj, portfolio):
    await engine_obj.place_order(
        portfolio.id,
        OrderRequest(symbol="AAPL", side=OrderSide.BUY,
                     type=OrderType.MARKET, quantity=6.0),
    )
    await engine_obj.place_order(
        portfolio.id,
        OrderRequest(symbol="MSFT", side=OrderSide.BUY,
                     type=OrderType.MARKET, quantity=3.0),
    )

    closing_orders = await engine_obj.flatten_all(portfolio.id)
    assert isinstance(closing_orders, list)
    # One closing order per open position.
    assert len(closing_orders) >= 2

    positions = await engine_obj.get_positions(portfolio.id)
    assert all(p.side == PositionSide.FLAT or p.quantity == 0.0 for p in positions)


# --------------------------------------------------------------------------- #
# Bracket order: attached take-profit / stop-loss trigger on a quote          #
# --------------------------------------------------------------------------- #
async def test_bracket_take_profit_triggers(engine_obj, portfolio, fake_market_data):
    # Buy AAPL at 150 with a take-profit at 160 and a stop-loss at 145.
    req = OrderRequest(
        symbol="AAPL", side=OrderSide.BUY, type=OrderType.MARKET,
        quantity=5.0, take_profit=160.0, stop_loss=145.0,
    )
    entry = await engine_obj.place_order(portfolio.id, req)
    assert entry.status == OrderStatus.FILLED

    # Price rallies through the take-profit -> the bracket should close the long.
    fake_market_data.set_price("AAPL", 161.0)
    await engine_obj.on_quote("AAPL", 161.0)

    pos = await engine_obj.get_position(portfolio.id, "AAPL")
    assert pos is None or pos.side == PositionSide.FLAT or pos.quantity == 0.0

    pf = await engine_obj.get_portfolio(portfolio.id)
    # Sold ~160 vs bought ~150 -> realized profit.
    assert pf.realized_pnl > 0.0


async def test_bracket_stop_loss_triggers(engine_obj, portfolio, fake_market_data):
    req = OrderRequest(
        symbol="AAPL", side=OrderSide.BUY, type=OrderType.MARKET,
        quantity=5.0, take_profit=160.0, stop_loss=145.0,
    )
    entry = await engine_obj.place_order(portfolio.id, req)
    assert entry.status == OrderStatus.FILLED

    # Price falls through the stop-loss -> the bracket should close the long.
    fake_market_data.set_price("AAPL", 144.0)
    await engine_obj.on_quote("AAPL", 144.0)

    pos = await engine_obj.get_position(portfolio.id, "AAPL")
    assert pos is None or pos.side == PositionSide.FLAT or pos.quantity == 0.0

    pf = await engine_obj.get_portfolio(portfolio.id)
    # Stopped out below entry -> realized loss.
    assert pf.realized_pnl < 0.0


# --------------------------------------------------------------------------- #
# Cancel a resting order                                                       #
# --------------------------------------------------------------------------- #
async def test_cancel_resting_order(engine_obj, portfolio):
    order = await engine_obj.place_order(
        portfolio.id,
        OrderRequest(symbol="AAPL", side=OrderSide.BUY, type=OrderType.LIMIT,
                     quantity=5.0, limit_price=100.0),
    )
    assert order.status in (OrderStatus.OPEN, OrderStatus.PENDING)

    cancelled = await engine_obj.cancel_order(portfolio.id, order.id)
    assert cancelled.status == OrderStatus.CANCELLED
    assert all(o.id != order.id for o in await engine_obj.get_open_orders(portfolio.id))
