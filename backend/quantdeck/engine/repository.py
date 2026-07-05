"""Async persistence helpers: convert between ORM rows and Pydantic models.

The :class:`~quantdeck.engine.engine.TradingEngine` keeps authoritative state in
memory for speed and consistency, but *write-through* persists every change so
state survives a restart. This module is the only place that touches the DB
session for engine data; it maps ``db.models`` (SQLAlchemy) <-> ``core.models``
(Pydantic) in both directions.

All functions are ``async`` and operate on a caller-provided ``AsyncSession`` so
the engine can batch several writes into a single transaction when it wants to.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, select

from ..core.enums import (
    AssetClass,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
)
from ..core.models import Fill, Order, Portfolio, Position, RiskLimits
from ..db import models as orm


# --------------------------------------------------------------------------- #
# RiskLimits <-> JSON                                                          #
# --------------------------------------------------------------------------- #
def risk_limits_to_json(limits: Optional[RiskLimits]) -> Optional[str]:
    """Serialize RiskLimits to a JSON string for the portfolio row (or None)."""
    if limits is None:
        return None
    return limits.model_dump_json()


def risk_limits_from_json(raw: Optional[str]) -> RiskLimits:
    """Deserialize the stored JSON back into RiskLimits (defaults if empty)."""
    if not raw:
        return RiskLimits()
    try:
        return RiskLimits.model_validate_json(raw)
    except Exception:
        # Corrupt/legacy row: fall back to safe defaults rather than crash load.
        return RiskLimits()


# --------------------------------------------------------------------------- #
# ORM row -> Pydantic model                                                    #
# --------------------------------------------------------------------------- #
def portfolio_from_orm(row: orm.PortfolioORM) -> Portfolio:
    """Build a Portfolio model from a row.

    The derived monetary fields (equity, buying_power, margin_used, unrealized)
    are recomputed live by the engine after marking positions; here we seed them
    to a consistent baseline (equity == cash) so a freshly-loaded portfolio is
    valid before the first ``on_quote``.
    """
    return Portfolio(
        id=row.id,
        name=row.name,
        base_currency=row.base_currency,
        starting_balance=row.starting_balance,
        cash=row.cash,
        equity=row.cash,
        realized_pnl=row.realized_pnl,
        unrealized_pnl=0.0,
        leverage=row.leverage,
        buying_power=row.cash * row.leverage,
        margin_used=0.0,
        maintenance_margin=0.0,
        fill_mode=row.fill_mode,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def position_from_orm(row: orm.PositionORM) -> Position:
    """Build a Position model from a row (unmarked — last_price/pnl set later)."""
    return Position(
        symbol=row.symbol,
        asset_class=AssetClass(row.asset_class),
        side=PositionSide(row.side),
        quantity=row.quantity,
        avg_entry_price=row.avg_entry_price,
        last_price=row.avg_entry_price,
        market_value=row.quantity * row.avg_entry_price,
        unrealized_pnl=0.0,
        realized_pnl=row.realized_pnl,
        liquidation_price=None,
    )


def order_from_orm(row: orm.OrderORM) -> Order:
    return Order(
        id=row.id,
        portfolio_id=row.portfolio_id,
        symbol=row.symbol,
        asset_class=AssetClass(row.asset_class),
        side=OrderSide(row.side),
        type=OrderType(row.type),
        quantity=row.quantity,
        filled_quantity=row.filled_quantity,
        limit_price=row.limit_price,
        stop_price=row.stop_price,
        take_profit=row.take_profit,
        stop_loss=row.stop_loss,
        time_in_force=TimeInForce(row.time_in_force),
        status=OrderStatus(row.status),
        avg_fill_price=row.avg_fill_price,
        source=row.source,
        reject_reason=row.reject_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def fill_from_orm(row: orm.FillORM) -> Fill:
    return Fill(
        id=row.id,
        order_id=row.order_id,
        portfolio_id=row.portfolio_id,
        symbol=row.symbol,
        side=OrderSide(row.side),
        quantity=row.quantity,
        price=row.price,
        commission=row.commission,
        slippage=row.slippage,
        timestamp=row.timestamp,
    )


# --------------------------------------------------------------------------- #
# Upserts: Pydantic model -> ORM row                                           #
# --------------------------------------------------------------------------- #
async def save_portfolio(
    session, portfolio: Portfolio, risk_limits: Optional[RiskLimits] = None
) -> None:
    """Insert or update the portfolio row (durable fields only)."""
    row = await session.get(orm.PortfolioORM, portfolio.id)
    if row is None:
        row = orm.PortfolioORM(id=portfolio.id)
        session.add(row)
    row.name = portfolio.name
    row.base_currency = portfolio.base_currency
    row.starting_balance = portfolio.starting_balance
    row.cash = portfolio.cash
    row.realized_pnl = portfolio.realized_pnl
    row.leverage = portfolio.leverage
    row.fill_mode = portfolio.fill_mode
    if risk_limits is not None:
        row.risk_limits_json = risk_limits_to_json(risk_limits)


async def save_position(session, portfolio_id: str, position: Position) -> None:
    """Insert/update the single row for ``(portfolio_id, symbol)``.

    Flat positions are removed so the table only holds open exposure.
    """
    stmt = select(orm.PositionORM).where(
        orm.PositionORM.portfolio_id == portfolio_id,
        orm.PositionORM.symbol == position.symbol,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()

    if position.side is PositionSide.FLAT or position.quantity <= 0:
        if row is not None:
            await session.delete(row)
        return

    if row is None:
        row = orm.PositionORM(portfolio_id=portfolio_id, symbol=position.symbol)
        session.add(row)
    row.asset_class = position.asset_class.value
    row.side = position.side.value
    row.quantity = position.quantity
    row.avg_entry_price = position.avg_entry_price
    row.realized_pnl = position.realized_pnl


async def save_order(session, order: Order) -> None:
    """Insert or update an order row (full state)."""
    row = await session.get(orm.OrderORM, order.id)
    if row is None:
        row = orm.OrderORM(id=order.id)
        session.add(row)
    row.portfolio_id = order.portfolio_id
    row.symbol = order.symbol
    row.asset_class = order.asset_class.value
    row.side = order.side.value
    row.type = order.type.value
    row.quantity = order.quantity
    row.filled_quantity = order.filled_quantity
    row.limit_price = order.limit_price
    row.stop_price = order.stop_price
    row.take_profit = order.take_profit
    row.stop_loss = order.stop_loss
    row.time_in_force = order.time_in_force.value
    row.status = order.status.value
    row.avg_fill_price = order.avg_fill_price
    row.source = order.source
    row.reject_reason = order.reject_reason
    row.created_at = order.created_at
    row.updated_at = order.updated_at


async def save_fill(session, fill: Fill) -> None:
    """Insert a fill row (fills are immutable — always an insert)."""
    row = orm.FillORM(
        id=fill.id,
        order_id=fill.order_id,
        portfolio_id=fill.portfolio_id,
        symbol=fill.symbol,
        side=fill.side.value,
        quantity=fill.quantity,
        price=fill.price,
        commission=fill.commission,
        slippage=fill.slippage,
        timestamp=fill.timestamp,
    )
    session.add(row)


# --------------------------------------------------------------------------- #
# Loads                                                                        #
# --------------------------------------------------------------------------- #
async def load_all_portfolios(session) -> list[tuple[Portfolio, RiskLimits]]:
    """Load every portfolio row with its parsed RiskLimits."""
    rows = (await session.execute(select(orm.PortfolioORM))).scalars().all()
    out: list[tuple[Portfolio, RiskLimits]] = []
    for row in rows:
        out.append((portfolio_from_orm(row), risk_limits_from_json(row.risk_limits_json)))
    return out


async def load_positions(session, portfolio_id: str) -> list[Position]:
    stmt = select(orm.PositionORM).where(orm.PositionORM.portfolio_id == portfolio_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [position_from_orm(r) for r in rows]


async def load_orders(session, portfolio_id: str) -> list[Order]:
    stmt = select(orm.OrderORM).where(orm.OrderORM.portfolio_id == portfolio_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [order_from_orm(r) for r in rows]


async def load_fills(
    session, portfolio_id: str, limit: int = 200
) -> list[Fill]:
    """Load the most recent ``limit`` fills, newest first."""
    stmt = (
        select(orm.FillORM)
        .where(orm.FillORM.portfolio_id == portfolio_id)
        .order_by(orm.FillORM.timestamp.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [fill_from_orm(r) for r in rows]


# --------------------------------------------------------------------------- #
# Deletes (used by reset_portfolio)                                            #
# --------------------------------------------------------------------------- #
async def wipe_portfolio_children(session, portfolio_id: str) -> None:
    """Delete all positions, orders, and fills for a portfolio (reset)."""
    await session.execute(
        delete(orm.PositionORM).where(orm.PositionORM.portfolio_id == portfolio_id)
    )
    await session.execute(
        delete(orm.OrderORM).where(orm.OrderORM.portfolio_id == portfolio_id)
    )
    await session.execute(
        delete(orm.FillORM).where(orm.FillORM.portfolio_id == portfolio_id)
    )


async def delete_order(session, order_id: str) -> None:
    row = await session.get(orm.OrderORM, order_id)
    if row is not None:
        await session.delete(row)
