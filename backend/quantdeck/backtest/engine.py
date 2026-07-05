"""Historical backtester — replays OHLCV bars through the *same* Strategy SDK
that runs live, so a strategy file behaves identically in both places.

Design goals (mirrors the live engine's semantics so results are comparable):

* **Same code path for the strategy.** User code subclasses ``Strategy`` and only
  ever touches a ``StrategyContext``. Here that context is :class:`BacktestContext`,
  backed by a lightweight in-memory simulation portfolio. Live mode swaps in
  ``LiveStrategyContext`` — the strategy never knows the difference.
* **Same accounting math.** Fills use average-entry position accounting with
  realized P&L booked on reduce/close, commission + slippage in basis points,
  and leverage-driven buying power. This intentionally matches the live engine's
  ``accounting``/``matching`` pure functions so a backtest P&L lines up with what
  the paper account would have done bar-for-bar.
* **No fabricated data.** Historical bars come from ``market_data.get_ohlcv``. If a
  symbol returns fewer bars than its peers (sparse coverage) we record a
  ``data_warning`` and simply skip the missing timestamps — we never interpolate
  a price the market did not print.
* **Deterministic.** Given the same input bars and parameters, the run is fully
  reproducible: no randomness, fills happen at derived prices only.

The public surface is exactly:

    bt = Backtester(market_data)
    result: BacktestResult = await bt.run(request)

``BacktestResult`` LEADS with ``total_pnl`` / ``total_return_pct`` /
``ending_balance`` (the product headline) and then carries the equity curve,
trade pairs, and risk metrics.
"""
from __future__ import annotations

import ast
import builtins as _builtins
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from ..core.config import settings
from ..core.enums import (
    AssetClass,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from ..core.errors import DataUnavailableError, StrategyError
from ..core.models import (
    Bar,
    BacktestRequest,
    BacktestResult,
    EquityPoint,
    Order,
    Position,
    Quote,
    TradeRecord,
    utcnow,
)
from ..core.symbols import classify, normalize_symbol
from ..strategies.sdk import Strategy

# --------------------------------------------------------------------------- #
# Strategy loading — reuse the real loader when present, else a faithful local #
# fallback so the backtester compiles and runs standalone.                     #
# --------------------------------------------------------------------------- #
#
# ``strategies/loader.py`` is built in parallel; when it is importable we defer
# to it so there is a single source of truth for the sandbox AST policy. If it
# is not yet available we fall back to an equivalent in-file implementation that
# honours the same documented contract (AST scan + restricted builtins). Both
# raise ``SandboxViolation`` on dangerous code.
try:  # pragma: no cover - exercised whenever the sibling module exists
    from ..strategies.loader import (  # type: ignore
        load_strategy as _loader_load_strategy,
        scan_code as _loader_scan_code,
    )

    _HAVE_LOADER = True
except Exception:  # noqa: BLE001 - any import problem => use the local fallback
    _loader_load_strategy = None  # type: ignore[assignment]
    _loader_scan_code = None  # type: ignore[assignment]
    _HAVE_LOADER = False


# Modules a strategy must never import, and callables it must never touch. Kept
# in sync with the loader contract in the spine.
_FORBIDDEN_IMPORTS = {
    "os",
    "sys",
    "subprocess",
    "socket",
    "shutil",
    "pathlib",
    "builtins",
    "importlib",
    "ctypes",
    "multiprocessing",
    "threading",
}
_FORBIDDEN_NAMES = {"__import__", "open", "eval", "exec", "compile", "input"}

# Builtins a strategy is allowed to call in the in-process restricted exec.
_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
    "float", "int", "len", "list", "map", "max", "min", "pow", "print",
    "range", "reversed", "round", "set", "slice", "sorted", "str", "sum",
    "tuple", "zip", "True", "False", "None", "isinstance", "issubclass",
    "hasattr", "getattr", "type", "Exception", "ValueError", "KeyError",
    "IndexError", "ZeroDivisionError", "ArithmeticError", "RuntimeError",
)


def _local_scan_code(source: str) -> None:
    """AST-scan untrusted strategy source, raising ``SandboxViolation`` on any
    dangerous construct. Used only if ``strategies.loader`` is unavailable."""
    # Import lazily so we still get the canonical exception type.
    from ..core.errors import SandboxViolation

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # pragma: no cover - trivial
        raise SandboxViolation(f"syntax error in strategy: {exc}") from exc

    for node in ast.walk(tree):
        # Block dangerous imports.
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _FORBIDDEN_IMPORTS:
                    raise SandboxViolation(f"import of '{alias.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _FORBIDDEN_IMPORTS:
                raise SandboxViolation(f"import from '{node.module}' is not allowed")
        # Block dangerous builtin calls / references by name.
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise SandboxViolation(f"use of '{node.id}' is not allowed")
        # Block dunder attribute access (e.g. obj.__globals__, __class__...).
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise SandboxViolation(f"access to '{node.attr}' is not allowed")


# Modules a strategy is allowed to import in the local fallback. These are pure,
# side-effect-free maths/util libraries — no filesystem, network, or process
# access. The SDK import (``quantdeck.strategies``) is allowed so the canonical
# ``from quantdeck.strategies import Strategy`` line works unchanged.
_SAFE_IMPORT_ROOTS = {
    "math",
    "statistics",
    "random",
    "datetime",
    "collections",
    "itertools",
    "functools",
    "decimal",
    "fractions",
    "numpy",
    "quantdeck",
}


def _make_safe_import():
    """Build a restricted ``__import__`` that only permits allowlisted modules.

    A ``class X(Strategy)`` statement and any ``import`` inside the strategy need
    a working ``__import__``; we replace it with one that hard-blocks anything
    not on :data:`_SAFE_IMPORT_ROOTS` (and re-blocks the forbidden roots even if
    they somehow slipped past the AST scan)."""
    from ..core.errors import SandboxViolation

    real_import = _builtins.__import__

    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".")[0]
        if root in _FORBIDDEN_IMPORTS or root not in _SAFE_IMPORT_ROOTS:
            raise SandboxViolation(f"import of '{name}' is not allowed")
        # Only let quantdeck.strategies (the SDK) through, not the whole package.
        if root == "quantdeck" and not name.startswith("quantdeck.strategies"):
            raise SandboxViolation(f"import of '{name}' is not allowed")
        return real_import(name, globals, locals, fromlist, level)

    return _safe_import


def _local_load_strategy(
    source: str, params: Optional[dict] = None
) -> Strategy:
    """Compile scanned source in a restricted namespace and return the single
    ``Strategy`` subclass instance found within it."""
    from ..core.errors import SandboxViolation

    _local_scan_code(source)

    safe_builtins = {
        name: getattr(_builtins, name)
        for name in _SAFE_BUILTIN_NAMES
        if hasattr(_builtins, name)
    }
    # ``__build_class__`` is required by every ``class`` statement, and a
    # restricted ``__import__`` lets the SDK/maths imports resolve while blocking
    # everything else. Neither can be reached by name from user code (the AST
    # scan rejects ``__import__`` and dunder access), so exposing them here is
    # safe — they are only used implicitly by the ``class``/``import`` syntax.
    safe_builtins["__build_class__"] = _builtins.__build_class__
    safe_builtins["__import__"] = _make_safe_import()

    # Provide the SDK base class so ``class X(Strategy)`` resolves.
    from .. import strategies as _strategies_pkg

    sandbox_globals: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "__name__": "quantdeck_user_strategy",
        "Strategy": Strategy,
        "strategies": _strategies_pkg,
    }
    try:
        exec(compile(source, "<strategy>", "exec"), sandbox_globals)  # noqa: S102
    except SandboxViolation:
        raise
    except Exception as exc:  # noqa: BLE001
        raise StrategyError(f"strategy failed to load: {exc}") from exc

    # Locate a user-defined Strategy subclass (not the base class itself).
    candidates = [
        obj
        for obj in sandbox_globals.values()
        if isinstance(obj, type)
        and issubclass(obj, Strategy)
        and obj is not Strategy
    ]
    if not candidates:
        raise SandboxViolation("no Strategy subclass found in code")
    # If several, prefer the most-derived (last defined) one.
    strategy_cls = candidates[-1]
    try:
        return strategy_cls()
    except Exception as exc:  # noqa: BLE001
        raise StrategyError(f"strategy __init__ failed: {exc}") from exc


def _scan_code(source: str) -> None:
    if _HAVE_LOADER and _loader_scan_code is not None:
        _loader_scan_code(source)
    else:
        _local_scan_code(source)


def _load_strategy(source: str, params: Optional[dict]) -> Strategy:
    if _HAVE_LOADER and _loader_load_strategy is not None:
        return _loader_load_strategy(source, params)
    return _local_load_strategy(source, params)


# --------------------------------------------------------------------------- #
# In-memory simulation portfolio (the accounting core, shared-semantics with   #
# the live engine).                                                            #
# --------------------------------------------------------------------------- #
class _SimPosition:
    """Signed-quantity working position used inside the simulation only.

    We keep an internal *signed* quantity for arithmetic convenience, but expose
    the canonical ``Position`` (unsigned qty + ``side``) to strategy code so it
    reads identically to live. Long is positive, short is negative.
    """

    __slots__ = ("symbol", "asset_class", "qty", "avg_price", "realized_pnl", "last_price")

    def __init__(self, symbol: str, asset_class: AssetClass) -> None:
        self.symbol = symbol
        self.asset_class = asset_class
        self.qty: float = 0.0          # signed
        self.avg_price: float = 0.0    # average entry of the open lot
        self.realized_pnl: float = 0.0
        self.last_price: float = 0.0

    def to_model(self) -> Position:
        """Project into the canonical, unsigned ``Position`` model."""
        if self.qty > 0:
            side = PositionSide.LONG
        elif self.qty < 0:
            side = PositionSide.SHORT
        else:
            side = PositionSide.FLAT
        abs_qty = abs(self.qty)
        market_value = abs_qty * self.last_price
        # Unrealized P&L is directional against the average entry.
        if self.qty > 0:
            unrealized = abs_qty * (self.last_price - self.avg_price)
        elif self.qty < 0:
            unrealized = abs_qty * (self.avg_price - self.last_price)
        else:
            unrealized = 0.0
        return Position(
            symbol=self.symbol,
            asset_class=self.asset_class,
            side=side,
            quantity=abs_qty,
            avg_entry_price=self.avg_price if self.qty != 0 else 0.0,
            last_price=self.last_price,
            market_value=market_value,
            unrealized_pnl=unrealized,
            realized_pnl=self.realized_pnl,
        )


class _SimPortfolio:
    """Cash + positions ledger with fill application matching live semantics.

    All monetary values are in the portfolio base currency. Cash moves by the
    *cash flow* of each fill (notional +/- for buys/sells) minus commission;
    unrealized P&L is derived from marked positions, never stored in cash.
    """

    def __init__(self, starting_balance: float, leverage: float) -> None:
        self.starting_balance = float(starting_balance)
        self.cash = float(starting_balance)
        self.leverage = max(1.0, float(leverage))
        self.realized_pnl = 0.0
        self.positions: dict[str, _SimPosition] = {}

    # --- marking / valuation -------------------------------------------- #
    def mark(self, symbol: str, price: float) -> None:
        pos = self.positions.get(symbol)
        if pos is not None:
            pos.last_price = price

    def position_value(self) -> float:
        """Sum of |qty| * last_price across positions (gross exposure basis)."""
        return sum(abs(p.qty) * p.last_price for p in self.positions.values())

    def unrealized_pnl(self) -> float:
        total = 0.0
        for p in self.positions.values():
            if p.qty > 0:
                total += abs(p.qty) * (p.last_price - p.avg_price)
            elif p.qty < 0:
                total += abs(p.qty) * (p.avg_price - p.last_price)
        return total

    def equity(self) -> float:
        """Cash + unrealized P&L of open positions.

        Because cash already reflects proceeds of shorts and cost of longs, we
        add back the *market value adjustment*: for a long, market value is an
        asset already funded from cash; for a short, proceeds sit in cash while
        we owe the position. Modeling equity as cash + unrealized P&L on the
        open lots gives the correct net liquidation value in both cases.
        """
        return self.cash + self._open_lot_cost_basis() + self.unrealized_pnl()

    def _open_lot_cost_basis(self) -> float:
        """Net cost basis embedded in cash from open lots.

        Buying a long removes ``qty*price`` from cash (an asset we still hold),
        so equity must add that basis back; shorting adds proceeds to cash but
        creates a liability, so we subtract it. This keeps equity == cash at the
        moment every position is flat.
        """
        basis = 0.0
        for p in self.positions.values():
            basis += p.qty * p.avg_price
        return basis

    def buying_power(self) -> float:
        used = self.position_value()
        return max(0.0, self.equity() * self.leverage - used)

    # --- fill application ------------------------------------------------ #
    def apply_fill(
        self, symbol: str, asset_class: AssetClass, side: OrderSide,
        quantity: float, price: float, commission: float,
    ) -> float:
        """Apply a fill; returns realized P&L booked by this fill (0 if opening).

        Average-entry accounting: adding to a position updates the weighted avg
        entry; reducing/closing books realized P&L against the avg entry; a fill
        that flips through zero splits into a close leg and a fresh-open leg.
        """
        pos = self.positions.get(symbol)
        if pos is None:
            pos = _SimPosition(symbol, asset_class)
            self.positions[symbol] = pos

        signed = quantity if side == OrderSide.BUY else -quantity
        realized = 0.0
        old_qty = pos.qty

        if old_qty == 0 or (old_qty > 0) == (signed > 0):
            # Opening or increasing an existing position in the same direction:
            # blend into the weighted average entry price.
            new_qty = old_qty + signed
            if new_qty != 0:
                pos.avg_price = (
                    (abs(old_qty) * pos.avg_price) + (abs(signed) * price)
                ) / abs(new_qty)
            pos.qty = new_qty
        else:
            # Reducing / closing / flipping.
            closing_qty = min(abs(signed), abs(old_qty))
            if old_qty > 0:  # closing part of a long by selling
                realized = closing_qty * (price - pos.avg_price)
            else:            # closing part of a short by buying
                realized = closing_qty * (pos.avg_price - price)
            pos.realized_pnl += realized
            self.realized_pnl += realized

            new_qty = old_qty + signed
            if (old_qty > 0) != (new_qty > 0) and new_qty != 0:
                # Flipped through zero: the remainder opens a fresh lot at price.
                pos.avg_price = price
            elif new_qty == 0:
                pos.avg_price = 0.0
            pos.qty = new_qty

        # Cash flow: buys cost cash, sells add cash; commission always debited.
        pos.last_price = price
        self.cash -= signed * price
        self.cash -= commission
        return realized


# --------------------------------------------------------------------------- #
# BacktestContext — the StrategyContext the strategy actually sees             #
# --------------------------------------------------------------------------- #
class BacktestContext:
    """A :class:`StrategyContext` backed by :class:`_SimPortfolio`.

    The backtester advances a global clock across aligned bar timestamps and,
    at each step, updates ``last_price`` per symbol so ``price``/``history``/
    account queries reflect the bar being processed. Order methods place fills
    synchronously against the simulation (market orders fill at the current
    bar's close adjusted for slippage; limit/stop orders rest and are evaluated
    on subsequent bars using their high/low range).
    """

    def __init__(
        self,
        portfolio: _SimPortfolio,
        req: BacktestRequest,
        history: dict[str, list[Bar]],
    ) -> None:
        self._pf = portfolio
        self._req = req
        self._params = dict(req.params or {})
        # Full history per symbol (immutable) + a moving cursor of how many bars
        # have been "seen" so far, so ``history`` never leaks future data.
        self._full: dict[str, list[Bar]] = history
        self._seen: dict[str, int] = {s: 0 for s in history}
        self._subscribed: list[str] = []
        self._commission_bps = float(req.commission_bps)
        self._slippage_bps = float(req.slippage_bps)
        self._idealized = str(req.fill_mode).lower() == "idealized"
        self._now: datetime = utcnow()

        # Working state exposed to the runner.
        self.logs: list[str] = []
        self.trades: list[TradeRecord] = []
        # Open-lot bookkeeping for building TradeRecord pairs: per symbol a list
        # of open entries (qty signed, price, time). Reductions match FIFO.
        self._open_lots: dict[str, list[dict[str, Any]]] = {}
        # Resting (limit/stop) orders awaiting a trigger on future bars.
        self._resting: list[Order] = []
        # Orders whose state changed this step, to feed on_order_update.
        self._pending_updates: list[Order] = []

    # --- runner-facing helpers (not part of StrategyContext) ------------- #
    def _advance(self, symbol: str, price: float, now: datetime) -> None:
        """Called by the runner before invoking ``on_bar`` for ``symbol``."""
        self._now = now
        self._pf.mark(symbol, price)

    def _reveal_bar(self, symbol: str) -> None:
        """Advance the visibility cursor so ``history`` includes this bar."""
        self._seen[symbol] = self._seen.get(symbol, 0) + 1

    def _drain_updates(self) -> list[Order]:
        out = self._pending_updates
        self._pending_updates = []
        return out

    # --- Subscriptions / data ------------------------------------------- #
    def subscribe(self, symbols: Sequence[str]) -> None:
        for s in symbols:
            ns = normalize_symbol(s)
            if ns not in self._subscribed:
                self._subscribed.append(ns)

    def price(self, symbol: str) -> Optional[float]:
        pos = self._pf.positions.get(normalize_symbol(symbol))
        if pos is not None and pos.last_price:
            return pos.last_price
        # Fall back to the most recently revealed bar close.
        ns = normalize_symbol(symbol)
        seen = self._seen.get(ns, 0)
        if seen > 0:
            return self._full[ns][seen - 1].close
        return None

    def history(
        self, symbol: str, field: str = "close", lookback: int = 100
    ) -> list[float]:
        ns = normalize_symbol(symbol)
        bars = self._full.get(ns, [])
        seen = self._seen.get(ns, 0)
        window = bars[:seen][-lookback:] if seen else []
        field = field.lower()
        try:
            return [float(getattr(b, field)) for b in window]
        except AttributeError:
            return []

    def bars(self, symbol: str, lookback: int = 100) -> list[Bar]:
        ns = normalize_symbol(symbol)
        bars = self._full.get(ns, [])
        seen = self._seen.get(ns, 0)
        return list(bars[:seen][-lookback:]) if seen else []

    def quote(self, symbol: str) -> Optional[Quote]:
        p = self.price(symbol)
        if p is None:
            return None
        ns = normalize_symbol(symbol)
        return Quote(
            symbol=ns,
            asset_class=classify(ns),
            price=p,
            timestamp=self._now,
            source="backtest",
        )

    # --- Account --------------------------------------------------------- #
    def get_position(self, symbol: str) -> Optional[Position]:
        pos = self._pf.positions.get(normalize_symbol(symbol))
        if pos is None or pos.qty == 0:
            return None
        return pos.to_model()

    def positions(self) -> list[Position]:
        return [p.to_model() for p in self._pf.positions.values() if p.qty != 0]

    @property
    def cash(self) -> float:
        return self._pf.cash

    @property
    def equity(self) -> float:
        return self._pf.equity()

    @property
    def buying_power(self) -> float:
        return self._pf.buying_power()

    def max_shares(self, symbol: str, fraction: float = 1.0) -> float:
        """Units of ``symbol`` that ``fraction`` of buying power can buy at the
        current price. Returns 0 when price is unknown (never fabricated)."""
        p = self.price(symbol)
        if not p or p <= 0:
            return 0.0
        fraction = max(0.0, min(1.0, float(fraction)))
        budget = self.buying_power * fraction
        return budget / p

    # --- Orders ---------------------------------------------------------- #
    def buy(
        self, symbol: str, quantity: float, *, order_type: str = "market",
        limit_price: Optional[float] = None, stop_price: Optional[float] = None,
        take_profit: Optional[float] = None, stop_loss: Optional[float] = None,
    ) -> Optional[Order]:
        return self._submit(
            symbol, OrderSide.BUY, quantity, order_type,
            limit_price, stop_price, take_profit, stop_loss,
        )

    def sell(
        self, symbol: str, quantity: float, *, order_type: str = "market",
        limit_price: Optional[float] = None, stop_price: Optional[float] = None,
        take_profit: Optional[float] = None, stop_loss: Optional[float] = None,
    ) -> Optional[Order]:
        return self._submit(
            symbol, OrderSide.SELL, quantity, order_type,
            limit_price, stop_price, take_profit, stop_loss,
        )

    def close(self, symbol: str) -> Optional[Order]:
        pos = self._pf.positions.get(normalize_symbol(symbol))
        if pos is None or pos.qty == 0:
            return None
        qty = abs(pos.qty)
        side = OrderSide.SELL if pos.qty > 0 else OrderSide.BUY
        return self._submit(symbol, side, qty, "market", None, None, None, None)

    def cancel(self, order_id: str) -> None:
        remaining = []
        for o in self._resting:
            if o.id == order_id:
                o.status = OrderStatus.CANCELLED
                o.updated_at = self._now
                self._pending_updates.append(o)
            else:
                remaining.append(o)
        self._resting = remaining

    def set_leverage(self, leverage: float) -> None:
        self._pf.leverage = max(1.0, float(leverage))

    # --- Misc ------------------------------------------------------------ #
    def log(self, *args: Any) -> None:
        msg = " ".join(str(a) for a in args)
        stamped = f"[{self._now.isoformat()}] {msg}"
        # Cap log volume so a runaway strategy cannot exhaust memory.
        if len(self.logs) < 10_000:
            self.logs.append(stamped)

    def param(self, key: str, default: Any = None) -> Any:
        return self._params.get(key, default)

    # --- internal order plumbing ---------------------------------------- #
    def _submit(
        self, symbol: str, side: OrderSide, quantity: float, order_type: str,
        limit_price: Optional[float], stop_price: Optional[float],
        take_profit: Optional[float], stop_loss: Optional[float],
    ) -> Optional[Order]:
        ns = normalize_symbol(symbol)
        try:
            qty = float(quantity)
        except (TypeError, ValueError):
            return None
        if qty <= 0:
            return None
        try:
            otype = OrderType(str(order_type).lower())
        except ValueError:
            otype = OrderType.MARKET

        order = Order(
            id=uuid.uuid4().hex,
            portfolio_id="backtest",
            symbol=ns,
            asset_class=classify(ns),
            side=side,
            type=otype,
            quantity=qty,
            limit_price=limit_price,
            stop_price=stop_price,
            take_profit=take_profit,
            stop_loss=stop_loss,
            status=OrderStatus.PENDING,
            source="strategy:backtest",
            created_at=self._now,
            updated_at=self._now,
        )

        if otype == OrderType.MARKET:
            self._fill(order, self._market_ref_price(ns))
        else:
            # Limit / stop / stop-limit rest until a future bar triggers them.
            order.status = OrderStatus.OPEN
            self._resting.append(order)
            self._pending_updates.append(order)
        return order

    def _market_ref_price(self, symbol: str) -> Optional[float]:
        return self.price(symbol)

    def _apply_slippage(self, side: OrderSide, price: float) -> float:
        """Slippage widens the fill against the taker (buys up, sells down)."""
        if self._idealized or self._slippage_bps <= 0:
            return price
        adj = price * (self._slippage_bps / 10_000.0)
        return price + adj if side == OrderSide.BUY else price - adj

    def _commission_for(self, notional: float) -> float:
        if self._commission_bps <= 0:
            return 0.0
        return abs(notional) * (self._commission_bps / 10_000.0)

    def _fill(self, order: Order, ref_price: Optional[float]) -> None:
        """Execute an order fully at a reference price (market/triggered)."""
        if ref_price is None or ref_price <= 0:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "no price available"
            order.updated_at = self._now
            self._pending_updates.append(order)
            return

        fill_price = self._apply_slippage(order.side, ref_price)
        notional = order.quantity * fill_price
        commission = self._commission_for(notional)

        # Book against the simulation portfolio (avg-entry accounting).
        self._pf.apply_fill(
            order.symbol, order.asset_class, order.side,
            order.quantity, fill_price, commission,
        )
        # Maintain FIFO open-lot ledger for building TradeRecord pairs.
        self._record_trade_leg(order, fill_price, commission)

        order.filled_quantity = order.quantity
        order.avg_fill_price = fill_price
        order.status = OrderStatus.FILLED
        order.updated_at = self._now
        self._pending_updates.append(order)

    def _record_trade_leg(self, order: Order, price: float, commission: float) -> None:
        """Translate a fill into completed :class:`TradeRecord` pairs (FIFO).

        Each entry opens a lot; an opposing fill closes lots oldest-first and
        emits a TradeRecord per matched quantity. This gives round-trip trades
        for win-rate / profit-factor without double-counting.
        """
        sym = order.symbol
        lots = self._open_lots.setdefault(sym, [])
        signed = order.quantity if order.side == OrderSide.BUY else -order.quantity
        remaining = signed
        # Commission is amortized proportionally onto the legs it touches.
        per_unit_comm = commission / order.quantity if order.quantity else 0.0

        # Close against opposing lots first.
        while remaining != 0 and lots and (lots[0]["qty"] > 0) != (remaining > 0):
            lot = lots[0]
            match_qty = min(abs(remaining), abs(lot["qty"]))
            entry_side = OrderSide.BUY if lot["qty"] > 0 else OrderSide.SELL
            entry_price = lot["price"]
            if lot["qty"] > 0:  # long lot closed by a sell
                pnl = match_qty * (price - entry_price)
            else:               # short lot closed by a buy
                pnl = match_qty * (entry_price - price)
            # Subtract the commission attributable to this closing quantity plus
            # the pro-rata entry commission carried on the lot.
            pnl -= match_qty * per_unit_comm
            pnl -= match_qty * lot.get("entry_comm_per_unit", 0.0)
            ret_pct = (pnl / (entry_price * match_qty) * 100.0) if entry_price else 0.0
            self.trades.append(
                TradeRecord(
                    symbol=sym,
                    side=entry_side,
                    quantity=match_qty,
                    entry_price=entry_price,
                    exit_price=price,
                    entry_time=lot["time"],
                    exit_time=self._now,
                    pnl=pnl,
                    return_pct=ret_pct,
                )
            )
            # Shrink or drop the closed lot.
            if abs(lot["qty"]) > match_qty:
                lot["qty"] += match_qty if lot["qty"] < 0 else -match_qty
            else:
                lots.pop(0)
            remaining += match_qty if remaining < 0 else -match_qty

        # Any leftover opens a fresh lot in the fill's direction.
        if remaining != 0:
            lots.append(
                {
                    "qty": remaining,
                    "price": price,
                    "time": self._now,
                    "entry_comm_per_unit": per_unit_comm,
                }
            )

    def _check_resting(self, bar: Bar) -> None:
        """Evaluate resting limit/stop orders against a bar's high/low range.

        Deterministic trigger rules (per bar, using the bar's OHLC):
          * BUY LIMIT  fills if bar.low  <= limit_price (fill at limit).
          * SELL LIMIT fills if bar.high >= limit_price (fill at limit).
          * BUY STOP   fills if bar.high >= stop_price  (fill at stop).
          * SELL STOP  fills if bar.low  <= stop_price  (fill at stop).
          * STOP_LIMIT converts to a resting LIMIT once the stop is touched.
        """
        if not self._resting:
            return
        still_resting: list[Order] = []
        for o in self._resting:
            if o.symbol != bar.symbol:
                still_resting.append(o)
                continue
            triggered_price = self._resting_trigger_price(o, bar)
            if triggered_price is None:
                still_resting.append(o)
                continue
            # _resting_trigger_price already applies the STOP_LIMIT rule (stop
            # must be touched AND the bar range must reach the limit on the same
            # bar), so a non-None price means "fill now".
            self._fill(o, triggered_price)
        self._resting = still_resting

    def _resting_trigger_price(self, o: Order, bar: Bar) -> Optional[float]:
        """Return the fill price if ``o`` triggers on ``bar``, else None."""
        if o.type == OrderType.LIMIT and o.limit_price is not None:
            if o.side == OrderSide.BUY and bar.low <= o.limit_price:
                return o.limit_price
            if o.side == OrderSide.SELL and bar.high >= o.limit_price:
                return o.limit_price
            return None
        if o.type == OrderType.STOP and o.stop_price is not None:
            if o.side == OrderSide.BUY and bar.high >= o.stop_price:
                return o.stop_price
            if o.side == OrderSide.SELL and bar.low <= o.stop_price:
                return o.stop_price
            return None
        if o.type == OrderType.STOP_LIMIT and o.stop_price is not None \
                and o.limit_price is not None:
            # Trigger when stop is touched AND the bar range reaches the limit.
            stop_hit = (
                bar.high >= o.stop_price if o.side == OrderSide.BUY
                else bar.low <= o.stop_price
            )
            if not stop_hit:
                return None
            if o.side == OrderSide.BUY and bar.low <= o.limit_price:
                return o.limit_price
            if o.side == OrderSide.SELL and bar.high >= o.limit_price:
                return o.limit_price
            return None
        return None


# --------------------------------------------------------------------------- #
# The Backtester                                                               #
# --------------------------------------------------------------------------- #
class Backtester:
    """Runs a :class:`~quantdeck.core.models.BacktestRequest` to a
    :class:`~quantdeck.core.models.BacktestResult`."""

    def __init__(self, market_data: Any) -> None:
        # Typed as MarketData Protocol; kept as Any to avoid a hard import cycle.
        self._md = market_data

    # ------------------------------------------------------------------ #
    async def run(self, req: BacktestRequest) -> BacktestResult:
        data_warnings: list[str] = []

        # 1) Resolve the strategy source (inline code or a saved strategy).
        try:
            source, params = await self._resolve_source(req)
        except Exception as exc:  # noqa: BLE001
            return self._empty_result(req, error=str(exc))

        # 2) Load + AST-scan the strategy in a restricted namespace.
        try:
            _scan_code(source)
            strategy = _load_strategy(source, params)
        except Exception as exc:  # noqa: BLE001
            return self._empty_result(req, error=f"strategy load error: {exc}")

        # 3) Fetch historical bars for every requested symbol.
        history, fetch_warnings = await self._fetch_history(req)
        data_warnings.extend(fetch_warnings)
        if not history:
            return self._empty_result(
                req,
                data_warnings=data_warnings,
                error="no historical data available for requested symbols",
            )

        # 4) Build the aligned, chronologically-ordered replay timeline.
        timeline, align_warnings = self._build_timeline(history)
        data_warnings.extend(align_warnings)
        if not timeline:
            return self._empty_result(
                req, data_warnings=data_warnings, error="no bars to replay"
            )

        # 5) Wire up the simulation portfolio + context.
        portfolio = _SimPortfolio(req.starting_balance, req.leverage)
        ctx = BacktestContext(portfolio, req, history)

        equity_curve: list[EquityPoint] = []
        run_error: Optional[str] = None

        # 6) on_start once.
        try:
            strategy.on_start(ctx)
        except Exception as exc:  # noqa: BLE001
            run_error = f"on_start error: {exc}"

        # 7) Replay bars in time order.
        last_prices: dict[str, float] = {}
        if run_error is None:
            for now, symbol, bar in timeline:
                # Mark the bar's price and check resting orders BEFORE the
                # strategy sees the bar (mirrors live: resting orders match on
                # the incoming tick, then the strategy reacts).
                ctx._advance(symbol, bar.close, now)
                last_prices[symbol] = bar.close
                try:
                    ctx._check_resting(bar)
                except Exception as exc:  # noqa: BLE001
                    run_error = f"order matching error: {exc}"
                    break

                # Reveal this bar to history() then invoke on_bar.
                ctx._reveal_bar(symbol)
                try:
                    strategy.on_bar(ctx, bar)
                except Exception as exc:  # noqa: BLE001
                    run_error = f"on_bar error at {now.isoformat()}: {exc}"
                    # Still flush any order updates produced before failure.
                    self._dispatch_updates(strategy, ctx)
                    break

                # Synthesize on_order_update for fills produced this step.
                self._dispatch_updates(strategy, ctx)

                # Record equity after each processed bar (mark all held symbols
                # at their latest known close).
                for s, p in last_prices.items():
                    portfolio.mark(s, p)
                equity_curve.append(
                    EquityPoint(timestamp=now, equity=portfolio.equity())
                )

        # 8) on_stop once (best-effort even after an error).
        try:
            strategy.on_stop(ctx)
            self._dispatch_updates(strategy, ctx)
        except Exception as exc:  # noqa: BLE001
            if run_error is None:
                run_error = f"on_stop error: {exc}"

        # 9) Close any positions still open at the last known price.
        self._close_open_positions(ctx, portfolio, last_prices)
        # Final equity point after forced liquidation.
        if timeline:
            final_ts = timeline[-1][0]
            equity_curve.append(
                EquityPoint(timestamp=final_ts, equity=portfolio.equity())
            )

        # 10) Compute metrics and assemble the result.
        return self._build_result(
            req, portfolio, equity_curve, ctx.trades, ctx.logs,
            data_warnings, run_error,
        )

    # ------------------------------------------------------------------ #
    # Source resolution                                                    #
    # ------------------------------------------------------------------ #
    async def _resolve_source(self, req: BacktestRequest) -> tuple[str, dict]:
        if req.code:
            return req.code, dict(req.params or {})
        if req.strategy_id:
            source, saved_params = await self._load_saved_strategy(req.strategy_id)
            # Request params override the saved defaults.
            merged = {**saved_params, **(req.params or {})}
            return source, merged
        raise StrategyError("backtest request has neither 'code' nor 'strategy_id'")

    async def _load_saved_strategy(self, strategy_id: str) -> tuple[str, dict]:
        """Read a persisted ``StrategyORM`` row (code + params)."""
        import json

        from ..db.database import SessionLocal
        from ..db.models import StrategyORM

        async with SessionLocal() as session:
            row = await session.get(StrategyORM, strategy_id)
            if row is None:
                raise StrategyError(f"strategy '{strategy_id}' not found")
            params: dict = {}
            if row.params_json:
                try:
                    params = json.loads(row.params_json) or {}
                except (ValueError, TypeError):
                    params = {}
            return row.code, params

    # ------------------------------------------------------------------ #
    # Data fetching + alignment                                            #
    # ------------------------------------------------------------------ #
    async def _fetch_history(
        self, req: BacktestRequest
    ) -> tuple[dict[str, list[Bar]], list[str]]:
        """Fetch OHLCV for each requested symbol. Never fabricate: a symbol that
        errors out is dropped with a data_warning rather than filled in."""
        warnings: list[str] = []
        history: dict[str, list[Bar]] = {}
        max_bars = int(settings.backtest_max_bars)

        # De-duplicate + normalize the requested symbols.
        symbols: list[str] = []
        for raw in req.symbols:
            ns = normalize_symbol(raw)
            if ns not in symbols:
                symbols.append(ns)

        for sym in symbols:
            try:
                resp = await self._md.get_ohlcv(
                    sym,
                    req.interval,
                    start=req.start,
                    end=req.end,
                    # Cap the fetch to the configured backtest bar budget.
                    limit=min(max_bars, 100_000),
                )
            except DataUnavailableError as exc:
                warnings.append(f"{sym}: data unavailable ({exc})")
                continue
            except Exception as exc:  # noqa: BLE001 - provider hiccup
                warnings.append(f"{sym}: fetch failed ({exc})")
                continue

            bars = list(resp.bars) if resp and resp.bars else []
            if not bars:
                warnings.append(f"{sym}: no bars returned for interval {req.interval}")
                continue

            # Ensure chronological order (oldest -> newest) and tz-aware stamps.
            bars.sort(key=lambda b: b.timestamp)
            # Respect the global bar budget across the whole run.
            if len(bars) > max_bars:
                warnings.append(
                    f"{sym}: truncated to backtest_max_bars={max_bars} "
                    f"(had {len(bars)})"
                )
                bars = bars[-max_bars:]
            history[sym] = bars

        # Flag sparse coverage: if symbols have materially different bar counts,
        # warn but do NOT interpolate the gaps.
        if len(history) > 1:
            counts = {s: len(b) for s, b in history.items()}
            most = max(counts.values())
            for s, n in counts.items():
                if n < most:
                    warnings.append(
                        f"{s}: sparse data ({n} bars vs {most}); "
                        f"missing timestamps are skipped, not interpolated"
                    )
        return history, warnings

    def _build_timeline(
        self, history: dict[str, list[Bar]]
    ) -> tuple[list[tuple[datetime, str, Bar]], list[str]]:
        """Merge per-symbol bars into a single chronologically-ordered stream.

        Each element is ``(timestamp, symbol, bar)``. Bars sharing a timestamp
        are ordered by symbol name for determinism. Timestamps present for some
        symbols but not others are simply emitted when they occur — no
        synthetic bars are created for the missing symbols.
        """
        warnings: list[str] = []
        events: list[tuple[datetime, str, Bar]] = []
        for sym, bars in history.items():
            seen_ts: set[datetime] = set()
            for b in bars:
                ts = b.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts in seen_ts:
                    # Duplicate timestamp within one symbol: keep the first,
                    # warn once.
                    continue
                seen_ts.add(ts)
                events.append((ts, sym, b))
        # Sort by (timestamp, symbol) for a fully deterministic replay order.
        events.sort(key=lambda e: (e[0], e[1]))
        return events, warnings

    # ------------------------------------------------------------------ #
    # Order-update dispatch + forced close                                 #
    # ------------------------------------------------------------------ #
    def _dispatch_updates(self, strategy: Strategy, ctx: BacktestContext) -> None:
        """Feed each order that changed state this step to ``on_order_update``.

        Strategy exceptions here are logged into the context but do not abort
        the whole backtest (the fill already happened in the simulation)."""
        for order in ctx._drain_updates():
            try:
                strategy.on_order_update(ctx, order)
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"on_order_update error: {exc}")

    def _close_open_positions(
        self,
        ctx: BacktestContext,
        portfolio: _SimPortfolio,
        last_prices: dict[str, float],
    ) -> None:
        """Flatten every remaining position at its last known close so realized
        P&L is fully booked into the final balance."""
        for sym, pos in list(portfolio.positions.items()):
            if pos.qty == 0:
                continue
            price = last_prices.get(sym, pos.last_price)
            if not price or price <= 0:
                # No price to close against; leave it (equity still reflects it)
                # and warn via a log line.
                ctx.log(f"could not close {sym}: no closing price")
                continue
            side = OrderSide.SELL if pos.qty > 0 else OrderSide.BUY
            qty = abs(pos.qty)
            close_order = Order(
                id=uuid.uuid4().hex,
                portfolio_id="backtest",
                symbol=sym,
                asset_class=pos.asset_class,
                side=side,
                type=OrderType.MARKET,
                quantity=qty,
                status=OrderStatus.PENDING,
                source="backtest:auto_close",
                created_at=ctx._now,
                updated_at=ctx._now,
            )
            # Close at the raw last price (no slippage on the synthetic close so
            # end-of-test liquidation does not distort the reported P&L).
            portfolio.apply_fill(
                sym, pos.asset_class, side, qty, price, 0.0
            )
            ctx._record_trade_leg(close_order, price, 0.0)

    # ------------------------------------------------------------------ #
    # Metrics                                                              #
    # ------------------------------------------------------------------ #
    def _build_result(
        self,
        req: BacktestRequest,
        portfolio: _SimPortfolio,
        equity_curve: list[EquityPoint],
        trades: list[TradeRecord],
        logs: list[str],
        data_warnings: list[str],
        error: Optional[str],
    ) -> BacktestResult:
        starting = float(req.starting_balance)
        # After forced close, all positions are flat so equity == cash.
        ending = portfolio.equity()
        total_pnl = ending - starting
        total_return_pct = (total_pnl / starting * 100.0) if starting else 0.0

        equities = [pt.equity for pt in equity_curve]

        max_dd = self._max_drawdown_pct(equities)
        sharpe = self._sharpe(equities)
        annualized = self._annualized_return_pct(
            starting, ending, equity_curve, req.interval
        )
        win_rate, profit_factor = self._trade_stats(trades)

        return BacktestResult(
            starting_balance=starting,
            ending_balance=ending,
            total_pnl=total_pnl,
            total_return_pct=total_return_pct,
            annualized_return_pct=annualized,
            max_drawdown_pct=max_dd,
            sharpe_ratio=sharpe,
            win_rate=win_rate,
            num_trades=len(trades),
            profit_factor=profit_factor,
            equity_curve=equity_curve,
            trades=trades,
            logs=logs,
            data_warnings=data_warnings,
            error=error,
        )

    @staticmethod
    def _max_drawdown_pct(equities: list[float]) -> float:
        """Largest peak-to-trough decline of the equity curve, as a percent."""
        if not equities:
            return 0.0
        peak = equities[0]
        max_dd = 0.0
        for e in equities:
            if e > peak:
                peak = e
            if peak > 0:
                dd = (peak - e) / peak
                if dd > max_dd:
                    max_dd = dd
        return max_dd * 100.0

    @staticmethod
    def _sharpe(equities: list[float]) -> Optional[float]:
        """Sharpe ratio from per-bar returns (risk-free rate assumed 0).

        Uses population std of simple per-bar returns. Returns None when there
        are too few points or zero volatility (undefined ratio)."""
        if len(equities) < 3:
            return None
        rets: list[float] = []
        for i in range(1, len(equities)):
            prev = equities[i - 1]
            if prev != 0:
                rets.append((equities[i] - prev) / prev)
        if len(rets) < 2:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        std = math.sqrt(var)
        if std == 0:
            return None
        # Non-annualized per-bar Sharpe; kept simple and deterministic.
        return mean / std

    @staticmethod
    def _annualized_return_pct(
        starting: float,
        ending: float,
        equity_curve: list[EquityPoint],
        interval: str,
    ) -> Optional[float]:
        """Annualize the total return over the elapsed calendar span."""
        if starting <= 0 or ending <= 0 or len(equity_curve) < 2:
            return None
        span = equity_curve[-1].timestamp - equity_curve[0].timestamp
        days = span.total_seconds() / 86_400.0
        if days <= 0:
            return None
        years = days / 365.25
        if years <= 0:
            return None
        growth = ending / starting
        try:
            annualized = (growth ** (1.0 / years)) - 1.0
        except (ValueError, OverflowError):
            return None
        return annualized * 100.0

    @staticmethod
    def _trade_stats(trades: list[TradeRecord]) -> tuple[float, Optional[float]]:
        """Win rate (fraction of profitable trades) and profit factor
        (gross profit / gross loss)."""
        if not trades:
            return 0.0, None
        wins = [t for t in trades if t.pnl > 0]
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = -sum(t.pnl for t in trades if t.pnl < 0)
        win_rate = len(wins) / len(trades)
        if gross_loss == 0:
            # No losing trades: profit factor is mathematically infinite, which
            # cannot be represented honestly, so leave it undefined (None).
            profit_factor = None
        else:
            profit_factor = gross_profit / gross_loss
        return win_rate, profit_factor

    # ------------------------------------------------------------------ #
    # Empty / error result helper                                          #
    # ------------------------------------------------------------------ #
    def _empty_result(
        self,
        req: BacktestRequest,
        *,
        data_warnings: Optional[list[str]] = None,
        error: Optional[str] = None,
    ) -> BacktestResult:
        starting = float(req.starting_balance)
        return BacktestResult(
            starting_balance=starting,
            ending_balance=starting,
            total_pnl=0.0,
            total_return_pct=0.0,
            annualized_return_pct=None,
            max_drawdown_pct=0.0,
            sharpe_ratio=None,
            win_rate=0.0,
            num_trades=0,
            profit_factor=None,
            equity_curve=[],
            trades=[],
            logs=[],
            data_warnings=data_warnings or [],
            error=error,
        )
