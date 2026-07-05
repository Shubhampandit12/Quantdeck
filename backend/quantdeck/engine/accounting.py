"""Pure position / P&L / margin math.

Everything here is a *pure function*: no I/O, no globals, no engine state. Given
numbers in, deterministic numbers out. That makes long/short accounting easy to
unit-test in isolation and keeps the stateful engine (in ``engine.py``) thin.

Conventions
-----------
* Positions are represented with a NON-NEGATIVE ``quantity`` plus an explicit
  ``PositionSide`` (LONG / SHORT / FLAT), matching ``core.models.Position`` — we
  never carry a signed quantity in the canonical model.
* A "fill" is described by an :class:`~quantdeck.core.enums.OrderSide` (BUY/SELL)
  and a positive quantity. Applying a fill can *extend*, *reduce*, *close*, or
  *flip* a position; realized P&L is booked whenever a position is reduced.
* Notional is always ``quantity * price`` (absolute, unsigned).
* Margin used for a position is ``notional / leverage``. Buying power is
  ``equity * leverage - margin_used``. Equity is ``cash + unrealized_pnl``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..core.enums import OrderSide, PositionSide


# --------------------------------------------------------------------------- #
# Position state after applying a fill                                        #
# --------------------------------------------------------------------------- #
@dataclass
class PositionState:
    """The mutable numeric core of a position (side + qty + avg entry).

    Kept separate from the Pydantic ``Position`` model so the math functions can
    return a plain, cheap value object. The engine maps this onto the canonical
    model for persistence / API responses.
    """

    side: PositionSide = PositionSide.FLAT
    quantity: float = 0.0             # always >= 0
    avg_entry_price: float = 0.0


@dataclass
class FillResult:
    """The outcome of applying one fill to a position."""

    position: PositionState           # new position state after the fill
    realized_pnl: float               # P&L booked by this fill (may be 0.0)
    # Notional actually closed by this fill (used by callers if they need it).
    closed_notional: float = 0.0


def _signed_qty(state: PositionState) -> float:
    """Signed quantity: +qty when long, -qty when short, 0 when flat."""
    if state.side is PositionSide.LONG:
        return state.quantity
    if state.side is PositionSide.SHORT:
        return -state.quantity
    return 0.0


def apply_fill(
    state: PositionState,
    side: OrderSide,
    quantity: float,
    price: float,
) -> FillResult:
    """Apply a BUY/SELL fill of ``quantity`` @ ``price`` to ``state``.

    Handles all four transitions with correct realized-P&L booking:

    * **Extend** (same direction as the position, or opening from flat): the
      average entry price is re-weighted; no P&L is realized.
    * **Reduce** (opposite direction, smaller than the position): P&L is booked
      on the reduced quantity at the difference between entry and fill price.
    * **Close** (opposite direction, exactly the position size): full P&L booked,
      position goes flat.
    * **Flip** (opposite direction, larger than the position): the existing side
      is fully closed (P&L booked) and a fresh position opens in the new
      direction for the remainder, with ``price`` as its entry.

    Returns a :class:`FillResult` with the new state and the realized P&L.
    """
    if quantity <= 0:
        # Defensive: a zero/negative fill is a no-op. Callers validate qty > 0
        # upstream, but pure functions should never raise on trivial input.
        return FillResult(position=state, realized_pnl=0.0)

    # Signed representation makes the arithmetic uniform for long and short.
    fill_signed = quantity if side is OrderSide.BUY else -quantity
    cur_signed = _signed_qty(state)
    new_signed = cur_signed + fill_signed

    realized = 0.0
    closed_notional = 0.0

    if cur_signed == 0.0:
        # Opening from flat.
        new_side = PositionSide.LONG if new_signed > 0 else PositionSide.SHORT
        return FillResult(
            position=PositionState(
                side=new_side,
                quantity=abs(new_signed),
                avg_entry_price=price,
            ),
            realized_pnl=0.0,
        )

    same_direction = (cur_signed > 0) == (fill_signed > 0)

    if same_direction:
        # Extending an existing position: re-weight the average entry price.
        total_qty = abs(cur_signed) + quantity
        avg = (
            state.avg_entry_price * abs(cur_signed) + price * quantity
        ) / total_qty
        new_side = state.side  # unchanged (still LONG or SHORT)
        return FillResult(
            position=PositionState(
                side=new_side,
                quantity=total_qty,
                avg_entry_price=avg,
            ),
            realized_pnl=0.0,
        )

    # Opposite direction: we are reducing / closing / flipping.
    reduce_qty = min(abs(cur_signed), quantity)
    closed_notional = reduce_qty * price

    # Realized P&L on the closed quantity. For a long we earn (exit - entry);
    # for a short we earn (entry - exit). Using the signed current qty encodes
    # the direction automatically.
    if cur_signed > 0:  # closing part of a long
        realized = (price - state.avg_entry_price) * reduce_qty
    else:               # closing part of a short
        realized = (state.avg_entry_price - price) * reduce_qty

    if abs(new_signed) < 1e-12:
        # Exact close -> flat.
        return FillResult(
            position=PositionState(side=PositionSide.FLAT, quantity=0.0,
                                   avg_entry_price=0.0),
            realized_pnl=realized,
            closed_notional=closed_notional,
        )

    if (new_signed > 0) == (cur_signed > 0):
        # Reduced but not closed: entry price is unchanged, side unchanged.
        return FillResult(
            position=PositionState(
                side=state.side,
                quantity=abs(new_signed),
                avg_entry_price=state.avg_entry_price,
            ),
            realized_pnl=realized,
            closed_notional=closed_notional,
        )

    # Flip: old side fully closed, remainder opens the new side at fill price.
    new_side = PositionSide.LONG if new_signed > 0 else PositionSide.SHORT
    return FillResult(
        position=PositionState(
            side=new_side,
            quantity=abs(new_signed),
            avg_entry_price=price,
        ),
        realized_pnl=realized,
        closed_notional=closed_notional,
    )


# --------------------------------------------------------------------------- #
# Marking: unrealized P&L, market value                                       #
# --------------------------------------------------------------------------- #
def unrealized_pnl(
    side: PositionSide, quantity: float, avg_entry_price: float, last_price: float
) -> float:
    """Mark-to-market P&L for an open position at ``last_price``.

    Long: ``(last - entry) * qty``. Short: ``(entry - last) * qty``. Flat: 0.
    """
    if side is PositionSide.LONG:
        return (last_price - avg_entry_price) * quantity
    if side is PositionSide.SHORT:
        return (avg_entry_price - last_price) * quantity
    return 0.0


def market_value(quantity: float, last_price: float) -> float:
    """Absolute notional value of the position at the current price."""
    return quantity * last_price


def position_notional(quantity: float, price: float) -> float:
    """Notional exposure = |quantity| * price. Alias for readability."""
    return quantity * price


# --------------------------------------------------------------------------- #
# Portfolio-level aggregates                                                  #
# --------------------------------------------------------------------------- #
def portfolio_equity(cash: float, total_unrealized_pnl: float) -> float:
    """Account equity = settled cash + open-position unrealized P&L.

    NOTE: cash here already reflects realized P&L (the engine credits/debits
    cash on every fill), so equity is simply ``cash + unrealized``.
    """
    return cash + total_unrealized_pnl


def margin_used(total_notional: float, leverage: float) -> float:
    """Initial margin locked up by open positions.

    ``leverage`` is the account multiplier (>=1). Higher leverage frees more
    buying power for the same notional. Guarded against a zero/invalid leverage.
    """
    lev = leverage if leverage and leverage > 0 else 1.0
    return total_notional / lev


def buying_power(equity: float, leverage: float, used_margin: float) -> float:
    """Remaining purchasing capacity: ``equity * leverage - used_margin``.

    Clamped at zero — you can never have negative buying power exposed to the
    order path (a negative would incorrectly *permit* nothing but also isn't a
    meaningful number to show a user).
    """
    lev = leverage if leverage and leverage > 0 else 1.0
    bp = equity * lev - used_margin
    return bp if bp > 0 else 0.0


def maintenance_margin(total_notional: float, maintenance_ratio: float) -> float:
    """Maintenance margin required to keep positions open.

    ``maintenance_ratio`` is a fraction of notional (e.g. 0.005 = 0.5%). When
    equity falls below this, the account is in a margin call / liquidation.
    """
    return total_notional * maintenance_ratio


# --------------------------------------------------------------------------- #
# Liquidation price estimate                                                  #
# --------------------------------------------------------------------------- #
def liquidation_price(
    side: PositionSide,
    quantity: float,
    avg_entry_price: float,
    leverage: float,
    maintenance_ratio: float = 0.005,
) -> Optional[float]:
    """Estimate the mark price at which an isolated-position account liquidates.

    We model a single-position account: initial margin posted is
    ``notional / leverage``. The position is liquidated when equity for that
    position drops to the maintenance margin, i.e. when the adverse move eats the
    posted margin down to ``maintenance_ratio * notional``.

    Derivation (per unit, entry price ``E``, leverage ``L``, maint ratio ``m``):
        posted margin per unit    = E / L
        loss per unit at price P   = (E - P) for long,  (P - E) for short
        liquidation when loss = posted_margin - maintenance_margin
                                 = E/L - m*E
    So for a **long**:
        E - P_liq = E/L - m*E   =>   P_liq = E * (1 - 1/L + m)
    and for a **short**:
        P_liq - E = E/L - m*E   =>   P_liq = E * (1 + 1/L - m)

    Returns ``None`` for flat positions, non-positive leverage, or when the
    result is not a sensible positive price (e.g. leverage 1x long can never be
    liquidated by price alone — it would take the price to <= 0).
    """
    if side is PositionSide.FLAT or quantity <= 0 or avg_entry_price <= 0:
        return None
    lev = leverage if leverage and leverage > 0 else 1.0
    inv_lev = 1.0 / lev

    # At (or below) 1x leverage a LONG has no borrowed margin, so an adverse
    # price move can only ever eat the fully-posted margin down to zero — the
    # position is not liquidatable by price alone. Report None rather than a
    # misleading near-zero price.
    if side is PositionSide.LONG and lev <= 1.0:
        return None

    if side is PositionSide.LONG:
        p_liq = avg_entry_price * (1.0 - inv_lev + maintenance_ratio)
    else:  # SHORT
        p_liq = avg_entry_price * (1.0 + inv_lev - maintenance_ratio)

    # A non-positive liquidation price is not a meaningful, reachable level.
    if p_liq <= 0:
        return None
    return p_liq
