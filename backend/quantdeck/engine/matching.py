"""Fill model — turn an order + a market price into a fill price.

The engine owns *state*; this module owns the *pricing policy* of a fill:

* **IDEALIZED** mode fills exactly at the reference price (no cost). Good for
  clean strategy math and deterministic tests.
* **REALISTIC** mode applies a half-spread and configurable slippage so a buy
  pays up and a sell gives up a little, mimicking real friction.

It also decides whether a resting LIMIT / STOP / STOP_LIMIT order is *triggered*
by an incoming price, and exposes a commission hook. Everything here is pure
(price in, price out) so it is trivially unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..core.enums import FillMode, OrderSide, OrderType


# Default realistic-mode friction if the engine/portfolio does not override it.
DEFAULT_SLIPPAGE_BPS = 2.0        # 0.02% adverse slippage per side
DEFAULT_HALF_SPREAD_BPS = 1.0     # 0.01% half-spread each side of the mid
DEFAULT_COMMISSION_BPS = 0.0      # commission-free by default (paper trading)

_BPS = 1e-4  # one basis point as a fraction


@dataclass
class FillDecision:
    """Result of pricing an order against the current market."""

    filled: bool                  # did the order fill on this tick?
    price: float = 0.0            # execution price (post friction)
    slippage: float = 0.0         # per-unit price impact charged (>= 0)
    reason: str = ""              # human note (e.g. "market", "limit crossed")


def apply_slippage(
    ref_price: float,
    side: OrderSide,
    *,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    half_spread_bps: float = DEFAULT_HALF_SPREAD_BPS,
) -> float:
    """Adjust ``ref_price`` for spread + slippage in the adverse direction.

    A BUY pays the ask side and eats positive slippage (price goes *up*); a SELL
    hits the bid side and gives up slippage (price goes *down*). The two costs
    are additive in basis points of the reference price.
    """
    frac = (slippage_bps + half_spread_bps) * _BPS
    if side is OrderSide.BUY:
        return ref_price * (1.0 + frac)
    return ref_price * (1.0 - frac)


def commission(notional: float, commission_bps: float = DEFAULT_COMMISSION_BPS) -> float:
    """Commission charged on a fill of ``notional`` currency at ``commission_bps``."""
    return abs(notional) * commission_bps * _BPS


def _price_after_friction(
    ref_price: float,
    side: OrderSide,
    fill_mode: FillMode,
    slippage_bps: float,
    half_spread_bps: float,
) -> tuple[float, float]:
    """Return ``(exec_price, per_unit_slippage)`` for a fill.

    IDEALIZED -> exact reference price, zero slippage. REALISTIC -> friction
    applied; slippage reported as the absolute per-unit deviation from the ref.
    """
    if fill_mode is FillMode.IDEALIZED:
        return ref_price, 0.0
    exec_price = apply_slippage(
        ref_price,
        side,
        slippage_bps=slippage_bps,
        half_spread_bps=half_spread_bps,
    )
    return exec_price, abs(exec_price - ref_price)


def decide_market_fill(
    side: OrderSide,
    ref_price: float,
    *,
    fill_mode: FillMode = FillMode.REALISTIC,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    half_spread_bps: float = DEFAULT_HALF_SPREAD_BPS,
) -> FillDecision:
    """A market order always fills immediately at the (friction-adjusted) price."""
    exec_price, slip = _price_after_friction(
        ref_price, side, fill_mode, slippage_bps, half_spread_bps
    )
    return FillDecision(filled=True, price=exec_price, slippage=slip, reason="market")


def is_limit_triggered(side: OrderSide, limit_price: float, market_price: float) -> bool:
    """A BUY limit fills when the market trades at/below the limit; a SELL limit
    fills at/above it."""
    if side is OrderSide.BUY:
        return market_price <= limit_price
    return market_price >= limit_price


def is_stop_triggered(side: OrderSide, stop_price: float, market_price: float) -> bool:
    """A BUY stop triggers when the market rises to/above the stop; a SELL stop
    triggers when it falls to/below the stop."""
    if side is OrderSide.BUY:
        return market_price >= stop_price
    return market_price <= stop_price


def decide_resting_fill(
    order_type: OrderType,
    side: OrderSide,
    market_price: float,
    *,
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    fill_mode: FillMode = FillMode.REALISTIC,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    half_spread_bps: float = DEFAULT_HALF_SPREAD_BPS,
) -> FillDecision:
    """Decide whether a resting LIMIT / STOP / STOP_LIMIT order fills at
    ``market_price``, and at what price.

    * LIMIT: fills when crossed. To be conservative (and to avoid granting the
      trader a better-than-limit price the sim can't justify), we fill AT the
      limit price rather than the market — no positive slippage on a limit fill.
    * STOP: once the stop triggers it becomes a market order and fills with
      friction applied to the market price.
    * STOP_LIMIT: once the stop triggers, it behaves like a resting limit at
      ``limit_price``; it only fills if the market is also crossing that limit.

    Returns ``filled=False`` if the trigger condition is not met this tick.
    """
    if order_type is OrderType.LIMIT:
        if limit_price is None or not is_limit_triggered(side, limit_price, market_price):
            return FillDecision(filled=False, reason="limit not crossed")
        # Fill exactly at the limit price (no friction benefit on a limit).
        return FillDecision(
            filled=True, price=limit_price, slippage=0.0, reason="limit crossed"
        )

    if order_type is OrderType.STOP:
        if stop_price is None or not is_stop_triggered(side, stop_price, market_price):
            return FillDecision(filled=False, reason="stop not triggered")
        exec_price, slip = _price_after_friction(
            market_price, side, fill_mode, slippage_bps, half_spread_bps
        )
        return FillDecision(
            filled=True, price=exec_price, slippage=slip, reason="stop triggered"
        )

    if order_type is OrderType.STOP_LIMIT:
        if stop_price is None or not is_stop_triggered(side, stop_price, market_price):
            return FillDecision(filled=False, reason="stop not triggered")
        # Stop has triggered: now behave like a limit at limit_price.
        if limit_price is None or not is_limit_triggered(side, limit_price, market_price):
            return FillDecision(filled=False, reason="stop-limit limit not crossed")
        return FillDecision(
            filled=True, price=limit_price, slippage=0.0, reason="stop-limit crossed"
        )

    # A MARKET order should never reach here (it fills via decide_market_fill),
    # but handle it defensively as an immediate fill at the market price.
    exec_price, slip = _price_after_friction(
        market_price, side, fill_mode, slippage_bps, half_spread_bps
    )
    return FillDecision(filled=True, price=exec_price, slippage=slip, reason="market")
