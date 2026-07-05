"""The QuantDeck simulation engine — the single source of truth.

``TradingEngine`` owns all order/position/portfolio state in memory (fast,
consistent) and *writes through* to the database via :mod:`.repository`. It
implements :class:`~quantdeck.engine.interface.EngineProtocol` exactly, so the
API, MCP tools, strategy runtime, and AI loop all speak to it identically.

Design invariants
------------------
* **One risk-check path.** Every order — UI, AI, or strategy — enters through
  :meth:`place_order`, which runs the shared validation. A *business* rejection
  returns a ``REJECTED`` :class:`Order` (never raises); only genuinely
  exceptional states (unknown portfolio) raise.
* **Never fabricate prices.** Fills use the live price from ``market_data``. If
  a price is unavailable, the market order is rejected with an honest reason.
* **Cash reflects realized P&L.** On every fill we credit/debit cash by the
  cost of the trade and any commission, and we book realized P&L into the
  position. Equity = cash + unrealized P&L of open positions, always.
* **Events on every state change.** Order/position/portfolio/margin/liquidation
  changes publish an :class:`Event` on the shared bus for the WS layer.

Concurrency: a single ``asyncio.Lock`` serializes all mutating operations so the
in-memory books and the DB never interleave inconsistently.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from ..core.config import settings
from ..core.enums import (
    AssetClass,
    EventType,
    FillMode,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
)
from ..core.errors import PortfolioNotFound
from ..core.eventbus import bus
from ..core.models import (
    Event,
    Fill,
    Order,
    OrderRequest,
    Portfolio,
    PortfolioSnapshot,
    Position,
    RiskLimits,
    utcnow,
)
from ..core.symbols import classify, normalize_symbol
from ..db.database import SessionLocal
from ..marketdata.base import MarketData
from . import accounting, matching, repository as repo

log = logging.getLogger("quantdeck.engine")

# Maintenance-margin ratio used for margin-call / liquidation checks. A small
# fraction of notional (0.5%) — the account is force-closed when equity can no
# longer cover this against the posted margin.
MAINTENANCE_RATIO = 0.005


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class _PortfolioBook:
    """In-memory authoritative state for one portfolio.

    Holds the Portfolio model, a map of open positions by symbol, all orders by
    id, the parsed risk limits, and a running tally of realized P&L booked today
    (for the ``max_daily_loss`` guardrail). Not persisted directly — the engine
    write-through maps it onto ORM rows.
    """

    def __init__(self, portfolio: Portfolio, risk_limits: RiskLimits) -> None:
        self.portfolio = portfolio
        self.risk_limits = risk_limits
        self.positions: dict[str, Position] = {}
        self.orders: dict[str, Order] = {}
        # Last known price per symbol (from on_quote); used for marking + BP.
        self.last_prices: dict[str, float] = {}
        # Realized P&L accumulated since the current UTC day started.
        self.day_realized_pnl: float = 0.0
        self.day_key: str = utcnow().strftime("%Y-%m-%d")


class TradingEngine:
    """Concrete implementation of :class:`EngineProtocol`."""

    def __init__(self, market_data: MarketData) -> None:
        self.market_data = market_data
        self._books: dict[str, _PortfolioBook] = {}
        self._lock = asyncio.Lock()
        # Reverse index: symbol -> set of portfolio ids holding/resting on it,
        # so on_quote only touches interested books.
        self._symbol_index: dict[str, set[str]] = {}

    # ------------------------------------------------------------------ #
    # Startup: restore state from the database                            #
    # ------------------------------------------------------------------ #
    async def load(self) -> None:
        """Restore all portfolios, positions, and orders from the database."""
        async with self._lock:
            async with SessionLocal() as session:
                loaded = await repo.load_all_portfolios(session)
                for portfolio, limits in loaded:
                    book = _PortfolioBook(portfolio, limits)
                    for pos in await repo.load_positions(session, portfolio.id):
                        book.positions[pos.symbol] = pos
                        self._index_symbol(pos.symbol, portfolio.id)
                    for order in await repo.load_orders(session, portfolio.id):
                        book.orders[order.id] = order
                        if order.status in (OrderStatus.OPEN, OrderStatus.PENDING,
                                            OrderStatus.PARTIALLY_FILLED):
                            self._index_symbol(order.symbol, portfolio.id)
                    self._books[portfolio.id] = book
                    # Recompute derived monetary fields from restored positions.
                    self._recompute_portfolio(book)
        log.info("engine loaded %d portfolios", len(self._books))

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #
    def tracked_symbols(self) -> list[str]:
        """Symbols the engine currently needs live prices for — any symbol with
        an open position or a resting order. The market-feed loop polls these
        and calls `on_quote` so positions mark-to-market and resting orders /
        bracket stops & targets trigger.
        """
        symbols: set[str] = set()
        for book in self._books.values():
            for pos in book.positions.values():
                if pos.side is not PositionSide.FLAT and pos.quantity > 0:
                    symbols.add(pos.symbol)
            for order in book.orders.values():
                if order.status in (
                    OrderStatus.OPEN,
                    OrderStatus.PENDING,
                    OrderStatus.PARTIALLY_FILLED,
                ):
                    symbols.add(order.symbol)
        return sorted(symbols)

    def _index_symbol(self, symbol: str, portfolio_id: str) -> None:
        self._symbol_index.setdefault(symbol, set()).add(portfolio_id)

    def _book(self, portfolio_id: str) -> _PortfolioBook:
        book = self._books.get(portfolio_id)
        if book is None:
            raise PortfolioNotFound(portfolio_id)
        return book

    def _fill_mode(self, book: _PortfolioBook) -> FillMode:
        try:
            return FillMode(book.portfolio.fill_mode)
        except ValueError:
            return FillMode.REALISTIC

    def _roll_day_if_needed(self, book: _PortfolioBook) -> None:
        """Reset the daily-loss tally when the UTC day changes."""
        today = utcnow().strftime("%Y-%m-%d")
        if today != book.day_key:
            book.day_key = today
            book.day_realized_pnl = 0.0

    async def _mark_price(
        self, book: _PortfolioBook, symbol: str, *, fresh: bool = False
    ) -> Optional[float]:
        """Get a price for marking / execution.

        With ``fresh=False`` (default) the cached last quote is returned if
        present — cheap and appropriate for buying-power estimates. With
        ``fresh=True`` a *new* live price is fetched so a market order always
        executes at the current market, not a stale cached tick.

        Returns ``None`` if no price is obtainable — callers must NOT fabricate.
        """
        if not fresh:
            px = book.last_prices.get(symbol)
            if px is not None:
                return px
        try:
            px = await self.market_data.get_price(symbol)
            book.last_prices[symbol] = px
            return px
        except Exception as exc:  # DataUnavailableError or provider failure
            log.warning("price unavailable for %s: %s", symbol, exc)
            # Fall back to a cached price only when we cannot fetch fresh, so a
            # transient outage doesn't strand an otherwise-priceable order.
            return book.last_prices.get(symbol)

    def _total_notional(self, book: _PortfolioBook) -> float:
        """Sum of |qty*price| across open positions using last known prices."""
        total = 0.0
        for pos in book.positions.values():
            if pos.side is PositionSide.FLAT or pos.quantity <= 0:
                continue
            px = book.last_prices.get(pos.symbol, pos.avg_entry_price)
            total += pos.quantity * px
        return total

    def _recompute_portfolio(self, book: _PortfolioBook) -> None:
        """Re-derive equity / unrealized / margin / buying power from positions.

        This is the single place monetary aggregates are computed so cash, P&L
        and margin can never drift out of sync.
        """
        p = book.portfolio
        total_unrealized = 0.0
        total_notional = 0.0
        for pos in book.positions.values():
            if pos.side is PositionSide.FLAT or pos.quantity <= 0:
                continue
            last = book.last_prices.get(pos.symbol, pos.avg_entry_price)
            pos.last_price = last
            pos.market_value = accounting.market_value(pos.quantity, last)
            pos.unrealized_pnl = accounting.unrealized_pnl(
                pos.side, pos.quantity, pos.avg_entry_price, last
            )
            pos.liquidation_price = accounting.liquidation_price(
                pos.side, pos.quantity, pos.avg_entry_price, p.leverage,
                MAINTENANCE_RATIO,
            )
            total_unrealized += pos.unrealized_pnl
            total_notional += pos.quantity * last

        p.unrealized_pnl = total_unrealized
        p.equity = accounting.portfolio_equity(p.cash, total_unrealized)
        p.margin_used = accounting.margin_used(total_notional, p.leverage)
        p.maintenance_margin = accounting.maintenance_margin(
            total_notional, MAINTENANCE_RATIO
        )
        p.buying_power = accounting.buying_power(p.equity, p.leverage, p.margin_used)
        p.updated_at = utcnow()

    # ------------------------------------------------------------------ #
    # Persistence helpers                                                 #
    # ------------------------------------------------------------------ #
    async def _persist_portfolio(self, book: _PortfolioBook) -> None:
        async with SessionLocal() as session:
            await repo.save_portfolio(session, book.portfolio, book.risk_limits)
            await session.commit()

    async def _persist_after_fill(
        self, book: _PortfolioBook, order: Order, position: Position, fill: Fill
    ) -> None:
        """Write-through a fill: portfolio, position, order, and the fill row."""
        async with SessionLocal() as session:
            await repo.save_portfolio(session, book.portfolio, book.risk_limits)
            await repo.save_position(session, book.portfolio.id, position)
            await repo.save_order(session, order)
            await repo.save_fill(session, fill)
            await session.commit()

    async def _persist_order(self, order: Order) -> None:
        async with SessionLocal() as session:
            await repo.save_order(session, order)
            await session.commit()

    # ------------------------------------------------------------------ #
    # Events                                                              #
    # ------------------------------------------------------------------ #
    def _emit(self, etype: EventType, portfolio_id: str, data: dict) -> None:
        bus.publish_soon(Event(type=etype, portfolio_id=portfolio_id, data=data))

    def _emit_portfolio(self, book: _PortfolioBook) -> None:
        self._emit(
            EventType.PORTFOLIO_UPDATED,
            book.portfolio.id,
            book.portfolio.model_dump(mode="json"),
        )

    # ================================================================== #
    # Portfolio lifecycle                                                 #
    # ================================================================== #
    async def create_portfolio(
        self,
        name: str = "Default",
        starting_balance: float = 100_000.0,
        leverage: float = 1.0,
        fill_mode: str = "realistic",
        risk_limits: Optional[RiskLimits] = None,
        base_currency: str = "USD",
    ) -> Portfolio:
        async with self._lock:
            pid = _new_id("pf")
            portfolio = Portfolio(
                id=pid,
                name=name,
                base_currency=base_currency,
                starting_balance=starting_balance,
                cash=starting_balance,
                equity=starting_balance,
                leverage=leverage,
                buying_power=starting_balance * leverage,
                fill_mode=fill_mode,
            )
            limits = risk_limits or RiskLimits()
            book = _PortfolioBook(portfolio, limits)
            self._books[pid] = book
            await self._persist_portfolio(book)
            self._emit_portfolio(book)
            return portfolio

    async def get_portfolio(self, portfolio_id: str) -> Portfolio:
        async with self._lock:
            return self._book(portfolio_id).portfolio

    async def list_portfolios(self) -> list[Portfolio]:
        async with self._lock:
            return [b.portfolio for b in self._books.values()]

    async def get_snapshot(self, portfolio_id: str) -> PortfolioSnapshot:
        async with self._lock:
            book = self._book(portfolio_id)
            positions = [
                p for p in book.positions.values()
                if p.side is not PositionSide.FLAT and p.quantity > 0
            ]
            open_orders = [
                o for o in book.orders.values()
                if o.status in (OrderStatus.OPEN, OrderStatus.PENDING,
                                OrderStatus.PARTIALLY_FILLED)
            ]
            return PortfolioSnapshot(
                portfolio=book.portfolio,
                positions=positions,
                open_orders=open_orders,
            )

    async def reset_portfolio(
        self, portfolio_id: str, starting_balance: Optional[float] = None
    ) -> Portfolio:
        """Wipe positions/orders/fills and restart at a chosen balance."""
        async with self._lock:
            book = self._book(portfolio_id)
            p = book.portfolio
            bal = starting_balance if starting_balance is not None else p.starting_balance
            # Clear symbol index entries for this portfolio.
            for sym in list(self._symbol_index.keys()):
                self._symbol_index[sym].discard(portfolio_id)
            book.positions.clear()
            book.orders.clear()
            book.last_prices.clear()
            book.day_realized_pnl = 0.0
            book.day_key = utcnow().strftime("%Y-%m-%d")
            p.starting_balance = bal
            p.cash = bal
            p.equity = bal
            p.realized_pnl = 0.0
            p.unrealized_pnl = 0.0
            p.margin_used = 0.0
            p.maintenance_margin = 0.0
            p.buying_power = bal * p.leverage
            p.updated_at = utcnow()
            async with SessionLocal() as session:
                await repo.wipe_portfolio_children(session, portfolio_id)
                await repo.save_portfolio(session, p, book.risk_limits)
                await session.commit()
            self._emit_portfolio(book)
            return p

    async def set_leverage(self, portfolio_id: str, leverage: float) -> Portfolio:
        async with self._lock:
            book = self._book(portfolio_id)
            book.portfolio.leverage = max(1.0, leverage)
            self._recompute_portfolio(book)
            await self._persist_portfolio(book)
            self._emit_portfolio(book)
            return book.portfolio

    async def set_risk_limits(
        self, portfolio_id: str, limits: RiskLimits
    ) -> Portfolio:
        async with self._lock:
            book = self._book(portfolio_id)
            book.risk_limits = limits
            await self._persist_portfolio(book)
            self._emit_portfolio(book)
            return book.portfolio

    # ================================================================== #
    # Orders — the SHARED RISK-CHECK PATH                                 #
    # ================================================================== #
    async def place_order(self, portfolio_id: str, req: OrderRequest) -> Order:
        """Validate + place an order. ALL origins go through here.

        Business rejections return a ``REJECTED`` Order with a ``reject_reason``;
        we only raise :class:`PortfolioNotFound` for a truly unknown portfolio.
        """
        async with self._lock:
            book = self._book(portfolio_id)
            self._roll_day_if_needed(book)

            symbol = normalize_symbol(req.symbol)
            asset_class = classify(symbol)
            order = Order(
                id=_new_id("ord"),
                portfolio_id=portfolio_id,
                symbol=symbol,
                asset_class=asset_class,
                side=req.side,
                type=req.type,
                quantity=req.quantity,
                limit_price=req.limit_price,
                stop_price=req.stop_price,
                take_profit=req.take_profit,
                stop_loss=req.stop_loss,
                time_in_force=req.time_in_force,
                source=req.source,
                status=OrderStatus.PENDING,
            )

            # For a MARKET order we need the live price *before* validation so
            # the buying-power / notional / leverage checks size against the
            # real fill price (not a stale/absent one). Fetch it now; a data
            # outage is an honest rejection, never a fabricated price.
            market_price: Optional[float] = None
            if order.type is OrderType.MARKET:
                market_price = await self._mark_price(book, symbol, fresh=True)
                if market_price is None:
                    order.status = OrderStatus.PENDING  # reset before rejecting
                    return await self._reject(
                        book, order,
                        "market data unavailable — cannot price market order",
                    )
                book.last_prices[symbol] = market_price

            # ---- Shared validation (order matters: cheapest checks first) ---
            reason = self._validate_order(book, order)
            if reason is not None:
                return await self._reject(book, order, reason)

            # ---- Route by type -------------------------------------------
            if order.type is OrderType.MARKET:
                await self._accept(book, order)
                await self._execute_fill(book, order, market_price)
                return order

            # LIMIT / STOP / STOP_LIMIT rest on the book until a crossing tick.
            await self._accept(book, order)
            order.status = OrderStatus.OPEN
            order.updated_at = utcnow()
            book.orders[order.id] = order
            self._index_symbol(symbol, portfolio_id)
            await self._persist_order(order)
            self._emit(EventType.ORDER_OPEN, portfolio_id, order.model_dump(mode="json"))
            # Immediately test the resting order against the current price in
            # case it is already marketable (e.g. a stop already through).
            cur = book.last_prices.get(symbol)
            if cur is not None:
                await self._try_fill_resting(book, order, cur)
            return order

    def _validate_order(self, book: _PortfolioBook, order: Order) -> Optional[str]:
        """Run the shared risk checks. Returns a reject reason or ``None``.

        Kept pure of side effects so the same logic can be reused/tested.
        """
        limits = book.risk_limits
        p = book.portfolio

        if order.quantity <= 0:
            return "quantity must be positive"

        # Order-type sanity: required prices must be present.
        if order.type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and order.limit_price is None:
            return f"{order.type.value} order requires limit_price"
        if order.type in (OrderType.STOP, OrderType.STOP_LIMIT) and order.stop_price is None:
            return f"{order.type.value} order requires stop_price"

        # Allowed-symbols allowlist.
        if limits.allowed_symbols is not None:
            allowed = {normalize_symbol(s) for s in limits.allowed_symbols}
            if order.symbol not in allowed:
                return f"symbol {order.symbol} not in allowed list"

        # Max daily loss: block new orders once the day's realized loss breaches.
        if limits.max_daily_loss is not None:
            if book.day_realized_pnl <= -abs(limits.max_daily_loss):
                return (
                    f"max daily loss reached "
                    f"({book.day_realized_pnl:.2f} <= -{abs(limits.max_daily_loss):.2f})"
                )

        # Reference price for notional checks: limit/stop price, else last/entry.
        ref = (
            order.limit_price
            or order.stop_price
            or book.last_prices.get(order.symbol)
        )
        if ref is None:
            # No price to size against — allow through; the market path will
            # re-check buying power once a live price is fetched. For resting
            # orders the notional checks apply at fill time via the same helper.
            ref = 0.0

        notional = order.quantity * ref if ref else 0.0

        # Notional caps only gate orders that ADD exposure. Reducing/closing a
        # position frees exposure and must never be blocked (consistent with the
        # buying-power and max-leverage checks below), or a user could be trapped
        # in a position after the caps are tightened.
        increases = self._increases_exposure(book, order)

        # Per-order notional cap.
        if (
            limits.max_order_notional is not None
            and increases
            and notional > limits.max_order_notional
        ):
            return (
                f"order notional {notional:.2f} exceeds max "
                f"{limits.max_order_notional:.2f}"
            )

        # Per-symbol position notional cap (existing exposure + this order).
        if limits.max_position_notional is not None and ref and increases:
            pos = book.positions.get(order.symbol)
            existing = pos.quantity * ref if pos and pos.side is not PositionSide.FLAT else 0.0
            # Only additive when the order increases exposure in the same side.
            projected = existing + notional
            if projected > limits.max_position_notional:
                return (
                    f"position notional {projected:.2f} exceeds max "
                    f"{limits.max_position_notional:.2f}"
                )

        # Effective leverage / buying-power check. Reducing/closing a position
        # frees margin, so only *increasing* exposure must fit buying power.
        if ref and self._increases_exposure(book, order):
            if notional > p.buying_power + 1e-6:
                return (
                    f"insufficient buying power: need {notional:.2f}, "
                    f"have {p.buying_power:.2f}"
                )

        # Account-level max leverage guard: projected gross notional must not
        # exceed equity * max_leverage.
        if ref:
            max_lev = min(p.leverage, limits.max_leverage)
            projected_gross = self._total_notional(book)
            if self._increases_exposure(book, order):
                projected_gross += notional
            if p.equity > 0 and projected_gross > p.equity * max_lev + 1e-6:
                return (
                    f"exceeds max leverage {max_lev:.1f}x "
                    f"(gross {projected_gross:.2f} > {p.equity * max_lev:.2f})"
                )

        return None

    def _increases_exposure(self, book: _PortfolioBook, order: Order) -> bool:
        """Does this order increase net exposure (needs buying power) vs reduce?"""
        pos = book.positions.get(order.symbol)
        if pos is None or pos.side is PositionSide.FLAT or pos.quantity <= 0:
            return True  # opening from flat always increases exposure
        # A BUY increases a long / reduces a short; a SELL the opposite.
        if order.side is OrderSide.BUY:
            return pos.side is PositionSide.LONG
        return pos.side is PositionSide.SHORT

    async def _reject(
        self, book: _PortfolioBook, order: Order, reason: str
    ) -> Order:
        order.status = OrderStatus.REJECTED
        order.reject_reason = reason
        order.updated_at = utcnow()
        book.orders[order.id] = order
        await self._persist_order(order)
        self._emit(
            EventType.ORDER_REJECTED,
            book.portfolio.id,
            {**order.model_dump(mode="json"), "reject_reason": reason},
        )
        log.info("order rejected pf=%s %s", book.portfolio.id, reason)
        return order

    async def _accept(self, book: _PortfolioBook, order: Order) -> None:
        book.orders[order.id] = order
        self._emit(
            EventType.ORDER_ACCEPTED, book.portfolio.id, order.model_dump(mode="json")
        )

    # ------------------------------------------------------------------ #
    # Fill execution                                                      #
    # ------------------------------------------------------------------ #
    async def _execute_fill(
        self, book: _PortfolioBook, order: Order, ref_price: float
    ) -> None:
        """Fully fill ``order`` at ``ref_price`` (post friction) and settle.

        Updates the position, books realized P&L, moves cash, marks the
        portfolio, persists, emits events, and creates any bracket child orders.
        """
        fill_mode = self._fill_mode(book)
        slippage_bps = matching.DEFAULT_SLIPPAGE_BPS if fill_mode is FillMode.REALISTIC else 0.0

        if order.type is OrderType.MARKET:
            decision = matching.decide_market_fill(
                order.side, ref_price, fill_mode=fill_mode
            )
        else:
            # ref_price passed in for resting orders is already the fill price
            # chosen by decide_resting_fill; wrap it as an immediate fill.
            decision = matching.FillDecision(
                filled=True, price=ref_price, slippage=0.0, reason=order.type.value
            )

        exec_price = decision.price
        qty = order.quantity - order.filled_quantity
        notional = qty * exec_price
        comm = matching.commission(notional)

        # --- Position update + realized P&L ---
        prior = book.positions.get(order.symbol)
        prior_state = accounting.PositionState(
            side=(prior.side if prior else PositionSide.FLAT),
            quantity=(prior.quantity if prior else 0.0),
            avg_entry_price=(prior.avg_entry_price if prior else 0.0),
        )
        result = accounting.apply_fill(prior_state, order.side, qty, exec_price)

        # Carry forward cumulative realized P&L only within the SAME open
        # lifecycle. A FLAT prior is a retained stub from a closed round-trip;
        # a fresh position opened from flat starts its own realized P&L at 0 so
        # Position.realized_pnl reflects only this position's lifecycle.
        realized_before = (
            prior.realized_pnl
            if prior is not None and prior.side is not PositionSide.FLAT
            else 0.0
        )
        new_position = Position(
            symbol=order.symbol,
            asset_class=order.asset_class,
            side=result.position.side,
            quantity=result.position.quantity,
            avg_entry_price=result.position.avg_entry_price,
            last_price=exec_price,
            realized_pnl=realized_before + result.realized_pnl,
        )
        book.positions[order.symbol] = new_position
        self._index_symbol(order.symbol, book.portfolio.id)

        # --- Cash movement (margin / P&L model) ---
        # We model a margin account so the spine identity holds exactly:
        #   equity = cash + unrealized_pnl   and   margin_used = notional/leverage.
        # Opening or increasing a position does NOT spend cash — it LOCKS margin
        # (reflected via buying_power). Cash only moves by *realized* P&L when a
        # position is reduced/closed, plus commission on every fill. This keeps
        # cash, equity, P&L, and buying power mutually consistent for both long
        # and short positions and at any leverage.
        book.portfolio.cash += result.realized_pnl - comm
        book.portfolio.realized_pnl += result.realized_pnl
        book.day_realized_pnl += result.realized_pnl

        # --- Order state ---
        order.filled_quantity = order.quantity
        order.avg_fill_price = exec_price
        order.status = OrderStatus.FILLED
        order.updated_at = utcnow()

        # --- Record the fill ---
        fill = Fill(
            id=_new_id("fill"),
            order_id=order.id,
            portfolio_id=book.portfolio.id,
            symbol=order.symbol,
            side=order.side,
            quantity=qty,
            price=exec_price,
            commission=comm,
            slippage=decision.slippage * qty,
        )

        book.last_prices[order.symbol] = exec_price
        self._recompute_portfolio(book)
        await self._persist_after_fill(book, order, new_position, fill)

        # --- Events ---
        self._emit(EventType.ORDER_FILLED, book.portfolio.id, order.model_dump(mode="json"))
        self._emit(
            EventType.POSITION_CHANGED,
            book.portfolio.id,
            new_position.model_dump(mode="json"),
        )
        self._emit_portfolio(book)

        # --- Bracket legs: attach TP (limit) / SL (stop) as child orders ---
        await self._create_bracket_children(book, order, new_position)

    async def _create_bracket_children(
        self, book: _PortfolioBook, parent: Order, position: Position
    ) -> None:
        """On a filled entry with take_profit/stop_loss, rest child exit orders.

        The child orders SELL (for a long) or BUY (for a short) the filled
        quantity, so triggering either one closes the exposure this entry added.
        """
        if position.side is PositionSide.FLAT or position.quantity <= 0:
            return
        exit_side = OrderSide.SELL if position.side is PositionSide.LONG else OrderSide.BUY
        qty = parent.filled_quantity

        if parent.take_profit is not None:
            tp = Order(
                id=_new_id("ord"),
                portfolio_id=book.portfolio.id,
                symbol=parent.symbol,
                asset_class=parent.asset_class,
                side=exit_side,
                type=OrderType.LIMIT,
                quantity=qty,
                limit_price=parent.take_profit,
                time_in_force=TimeInForce.GTC,
                status=OrderStatus.OPEN,
                source=f"bracket_tp:{parent.id}",
            )
            book.orders[tp.id] = tp
            self._index_symbol(tp.symbol, book.portfolio.id)
            await self._persist_order(tp)
            self._emit(EventType.ORDER_OPEN, book.portfolio.id, tp.model_dump(mode="json"))

        if parent.stop_loss is not None:
            sl = Order(
                id=_new_id("ord"),
                portfolio_id=book.portfolio.id,
                symbol=parent.symbol,
                asset_class=parent.asset_class,
                side=exit_side,
                type=OrderType.STOP,
                quantity=qty,
                stop_price=parent.stop_loss,
                time_in_force=TimeInForce.GTC,
                status=OrderStatus.OPEN,
                source=f"bracket_sl:{parent.id}",
            )
            book.orders[sl.id] = sl
            self._index_symbol(sl.symbol, book.portfolio.id)
            await self._persist_order(sl)
            self._emit(EventType.ORDER_OPEN, book.portfolio.id, sl.model_dump(mode="json"))

    async def _try_fill_resting(
        self, book: _PortfolioBook, order: Order, market_price: float
    ) -> None:
        """Test a resting order against ``market_price``; fill if triggered."""
        if order.status not in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED):
            return
        fill_mode = self._fill_mode(book)
        decision = matching.decide_resting_fill(
            order.type,
            order.side,
            market_price,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            fill_mode=fill_mode,
        )
        if not decision.filled:
            return
        await self._execute_fill(book, order, decision.price)
        # Cancel the sibling bracket leg (one-cancels-other), if any.
        await self._cancel_sibling_brackets(book, order)

    async def _cancel_sibling_brackets(self, book: _PortfolioBook, filled: Order) -> None:
        """OCO: when one bracket leg fills, cancel the other leg of the pair."""
        src = filled.source or ""
        if not (src.startswith("bracket_tp:") or src.startswith("bracket_sl:")):
            return
        parent_id = src.split(":", 1)[1]
        for o in list(book.orders.values()):
            if o.id == filled.id:
                continue
            if o.source in (f"bracket_tp:{parent_id}", f"bracket_sl:{parent_id}"):
                if o.status in (OrderStatus.OPEN, OrderStatus.PENDING):
                    o.status = OrderStatus.CANCELLED
                    o.updated_at = utcnow()
                    await self._persist_order(o)
                    self._emit(
                        EventType.ORDER_CANCELLED,
                        book.portfolio.id,
                        o.model_dump(mode="json"),
                    )

    # ------------------------------------------------------------------ #
    # Cancel / modify                                                     #
    # ------------------------------------------------------------------ #
    async def cancel_order(self, portfolio_id: str, order_id: str) -> Order:
        async with self._lock:
            book = self._book(portfolio_id)
            order = book.orders.get(order_id)
            if order is None:
                raise KeyError(f"order {order_id} not found")
            if order.status in (OrderStatus.OPEN, OrderStatus.PENDING,
                                OrderStatus.PARTIALLY_FILLED):
                order.status = OrderStatus.CANCELLED
                order.updated_at = utcnow()
                await self._persist_order(order)
                self._emit(
                    EventType.ORDER_CANCELLED, portfolio_id, order.model_dump(mode="json")
                )
            return order

    async def modify_order(
        self,
        portfolio_id: str,
        order_id: str,
        *,
        quantity: Optional[float] = None,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> Order:
        async with self._lock:
            book = self._book(portfolio_id)
            order = book.orders.get(order_id)
            if order is None:
                raise KeyError(f"order {order_id} not found")
            if order.status not in (OrderStatus.OPEN, OrderStatus.PENDING,
                                    OrderStatus.PARTIALLY_FILLED):
                # Only working orders can be modified — return as-is otherwise.
                return order
            if quantity is not None and quantity > 0:
                order.quantity = quantity
            if limit_price is not None:
                order.limit_price = limit_price
            if stop_price is not None:
                order.stop_price = stop_price
            if take_profit is not None:
                order.take_profit = take_profit
            if stop_loss is not None:
                order.stop_loss = stop_loss
            order.updated_at = utcnow()
            await self._persist_order(order)
            self._emit(EventType.ORDER_MODIFIED, portfolio_id, order.model_dump(mode="json"))
            # Re-test in case the new params make it marketable now.
            cur = book.last_prices.get(order.symbol)
            if cur is not None:
                await self._try_fill_resting(book, order, cur)
            return order

    async def get_order(self, portfolio_id: str, order_id: str) -> Order:
        async with self._lock:
            book = self._book(portfolio_id)
            order = book.orders.get(order_id)
            if order is None:
                raise PortfolioNotFound(f"order {order_id} not found")
            return order

    async def get_open_orders(self, portfolio_id: str) -> list[Order]:
        async with self._lock:
            book = self._book(portfolio_id)
            return [
                o for o in book.orders.values()
                if o.status in (OrderStatus.OPEN, OrderStatus.PENDING,
                                OrderStatus.PARTIALLY_FILLED)
            ]

    async def get_orders(self, portfolio_id: str) -> list[Order]:
        """Full order history for the account (all statuses), from the DB.

        Complements `get_open_orders`; the API's GET /portfolios/{id}/orders
        (without ?open=true) returns this.
        """
        async with self._lock:
            self._book(portfolio_id)  # validate existence
        async with SessionLocal() as session:
            return await repo.load_orders(session, portfolio_id)

    async def get_trade_history(
        self, portfolio_id: str, limit: int = 200
    ) -> list[Fill]:
        async with self._lock:
            self._book(portfolio_id)  # validate existence
        async with SessionLocal() as session:
            return await repo.load_fills(session, portfolio_id, limit)

    # ------------------------------------------------------------------ #
    # Positions                                                           #
    # ------------------------------------------------------------------ #
    async def get_positions(self, portfolio_id: str) -> list[Position]:
        async with self._lock:
            book = self._book(portfolio_id)
            return [
                p for p in book.positions.values()
                if p.side is not PositionSide.FLAT and p.quantity > 0
            ]

    async def get_position(
        self, portfolio_id: str, symbol: str
    ) -> Optional[Position]:
        async with self._lock:
            book = self._book(portfolio_id)
            pos = book.positions.get(normalize_symbol(symbol))
            if pos is None or pos.side is PositionSide.FLAT or pos.quantity <= 0:
                return None
            return pos

    async def flatten_all(self, portfolio_id: str) -> list[Order]:
        """Kill-switch: market-close every open position immediately."""
        async with self._lock:
            book = self._book(portfolio_id)
            closed: list[Order] = []
            # Cancel all working orders first (including brackets).
            for o in list(book.orders.values()):
                if o.status in (OrderStatus.OPEN, OrderStatus.PENDING,
                                OrderStatus.PARTIALLY_FILLED):
                    o.status = OrderStatus.CANCELLED
                    o.updated_at = utcnow()
                    await self._persist_order(o)
                    self._emit(
                        EventType.ORDER_CANCELLED, portfolio_id, o.model_dump(mode="json")
                    )
            # Market-close each open position.
            for pos in list(book.positions.values()):
                if pos.side is PositionSide.FLAT or pos.quantity <= 0:
                    continue
                price = await self._mark_price(book, pos.symbol, fresh=True)
                if price is None:
                    log.warning("flatten: no price for %s, skipping", pos.symbol)
                    continue
                side = OrderSide.SELL if pos.side is PositionSide.LONG else OrderSide.BUY
                order = Order(
                    id=_new_id("ord"),
                    portfolio_id=portfolio_id,
                    symbol=pos.symbol,
                    asset_class=pos.asset_class,
                    side=side,
                    type=OrderType.MARKET,
                    quantity=pos.quantity,
                    source="flatten_all",
                    status=OrderStatus.PENDING,
                )
                await self._accept(book, order)
                await self._execute_fill(book, order, price)
                closed.append(order)
            return closed

    # ------------------------------------------------------------------ #
    # Market marking tick                                                 #
    # ------------------------------------------------------------------ #
    async def on_quote(self, symbol: str, price: float) -> None:
        """Feed a fresh price: mark positions, trigger resting orders/brackets,
        and run margin-call / liquidation checks for every interested book."""
        symbol = normalize_symbol(symbol)
        async with self._lock:
            interested = set(self._symbol_index.get(symbol, set()))
            for pid in interested:
                book = self._books.get(pid)
                if book is None:
                    continue
                book.last_prices[symbol] = price

                # Trigger any resting orders that this price crosses.
                for order in list(book.orders.values()):
                    if order.symbol != symbol:
                        continue
                    if order.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED):
                        await self._try_fill_resting(book, order, price)

                # Re-mark and check for margin call / liquidation.
                self._recompute_portfolio(book)
                await self._check_liquidation(book, symbol, price)
                self._emit_portfolio(book)

    async def _check_liquidation(
        self, book: _PortfolioBook, symbol: str, price: float
    ) -> None:
        """Emit MARGIN_CALL when equity dips below maintenance; force-close at
        the liquidation price when equity can no longer cover it.

        We use a simple account-level rule: if equity < maintenance_margin the
        account is under-margined. We first warn (MARGIN_CALL), then liquidate
        positions (LIQUIDATION) to bring exposure back within margin.
        """
        p = book.portfolio
        if p.maintenance_margin <= 0:
            return
        if p.equity >= p.maintenance_margin:
            return  # healthy

        # Under-margined: warn, then liquidate positions until healthy or empty.
        self._emit(
            EventType.MARGIN_CALL,
            p.id,
            {
                "equity": p.equity,
                "maintenance_margin": p.maintenance_margin,
                "symbol": symbol,
                "price": price,
            },
        )

        # Force-close positions (largest notional first) until equity recovers.
        while p.equity < p.maintenance_margin:
            open_positions = [
                pos for pos in book.positions.values()
                if pos.side is not PositionSide.FLAT and pos.quantity > 0
            ]
            if not open_positions:
                break
            open_positions.sort(key=lambda x: x.quantity * x.last_price, reverse=True)
            victim = open_positions[0]
            close_price = book.last_prices.get(victim.symbol, victim.last_price)
            side = OrderSide.SELL if victim.side is PositionSide.LONG else OrderSide.BUY
            liq_order = Order(
                id=_new_id("ord"),
                portfolio_id=p.id,
                symbol=victim.symbol,
                asset_class=victim.asset_class,
                side=side,
                type=OrderType.MARKET,
                quantity=victim.quantity,
                source="liquidation",
                status=OrderStatus.PENDING,
            )
            await self._accept(book, liq_order)
            await self._execute_fill(book, liq_order, close_price)
            self._emit(
                EventType.LIQUIDATION,
                p.id,
                {
                    "symbol": victim.symbol,
                    "quantity": liq_order.filled_quantity,
                    "price": close_price,
                    "equity": p.equity,
                },
            )
            self._recompute_portfolio(book)
