"""LiveStrategyContext — the ``ctx`` object handed to user strategies in live
(paper) mode.

A strategy's hooks are plain synchronous methods, but the engine is async and
lives in the parent process. This context therefore does NOT talk to the engine
directly. Instead it delegates every account/order action to a synchronous
"engine proxy" callable supplied by the runtime:

    proxy(action: str, **kwargs) -> Any

The runtime wires ``proxy`` to something that serializes the call (in the
subprocess sandbox that means writing a JSON ``intent`` line to stdout; the
parent then executes it against the real async engine). Market data —
``price``/``history``/``bars``/``quote`` — is served entirely from local ring
buffers that the runtime fills from streamed bars/quotes, so read paths never
block.

It implements the :class:`~quantdeck.strategies.sdk.StrategyContext` surface
that user strategies are written against.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Callable, Deque, Optional, Sequence

from ..core.eventbus import bus
from ..core.models import Bar, Event, Order, Position, Quote
from ..core.enums import EventType

# How many bars/quotes we keep per symbol for lookback queries. Generous enough
# for typical moving-average windows without unbounded memory growth.
_DEFAULT_RING = 1000


class LiveStrategyContext:
    """Concrete :class:`StrategyContext` for live-paper strategies.

    Parameters
    ----------
    engine_proxy:
        Synchronous callable ``proxy(action, **kwargs)`` that performs the
        account/order side effect. Recognized actions:
        ``buy``, ``sell``, ``close``, ``cancel``, ``set_leverage``,
        ``get_position``, ``positions``, ``cash``, ``equity``,
        ``buying_power``, ``max_shares``.
    strategy_id:
        Identifier used to tag ``strategy_log`` events.
    portfolio_id:
        Portfolio the strategy trades in (used for event routing).
    params:
        User-supplied strategy parameters, read via :meth:`param`.
    ring_size:
        Per-symbol ring-buffer capacity for bars/quotes.
    """

    def __init__(
        self,
        engine_proxy: Callable[..., Any],
        *,
        strategy_id: str,
        portfolio_id: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        ring_size: int = _DEFAULT_RING,
    ) -> None:
        self._proxy = engine_proxy
        self._strategy_id = strategy_id
        self._portfolio_id = portfolio_id
        self._params = dict(params or {})
        self._ring = ring_size

        # Local market-data buffers, filled by the runtime.
        self._bars: dict[str, Deque[Bar]] = {}
        self._quotes: dict[str, Quote] = {}
        self._subscribed: list[str] = []

        # Captured log lines (the runtime reads these for the /logs endpoint).
        self.logs: list[str] = []

    # ------------------------------------------------------------------ #
    # Runtime-facing helpers (not part of the strategy-visible surface).  #
    # These are how the runner pushes market data into the buffers.       #
    # ------------------------------------------------------------------ #
    def push_bar(self, bar: Bar) -> None:
        """Append a freshly-closed bar; called by the runtime before on_bar."""
        buf = self._bars.get(bar.symbol)
        if buf is None:
            buf = deque(maxlen=self._ring)
            self._bars[bar.symbol] = buf
        buf.append(bar)
        # Keep a synthetic last-price handy even if no real quote has arrived.
        # Only seed/refresh it when the current quote is itself bar-derived, so a
        # genuine streamed quote is never clobbered by a (possibly staler) bar.
        existing = self._quotes.get(bar.symbol)
        if existing is None or existing.source == "bar":
            self._quotes[bar.symbol] = Quote(
                symbol=bar.symbol,
                asset_class=_classify(bar.symbol),
                price=bar.close,
                timestamp=bar.timestamp,
                source="bar",
            )

    def push_quote(self, quote: Quote) -> None:
        """Record the latest quote; called by the runtime before on_tick."""
        self._quotes[quote.symbol] = quote

    # ------------------------------------------------------------------ #
    # StrategyContext: subscriptions / data                               #
    # ------------------------------------------------------------------ #
    def subscribe(self, symbols: Sequence[str]) -> None:
        for s in symbols:
            if s not in self._subscribed:
                self._subscribed.append(s)
        # Let the runtime know it needs to stream these symbols.
        try:
            self._proxy("subscribe", symbols=list(symbols))
        except Exception:  # noqa: BLE001 - subscription is best-effort
            pass

    @property
    def subscriptions(self) -> list[str]:
        return list(self._subscribed)

    def price(self, symbol: str) -> Optional[float]:
        q = self._quotes.get(symbol)
        if q is not None:
            return q.price
        buf = self._bars.get(symbol)
        if buf:
            return buf[-1].close
        return None

    def history(
        self, symbol: str, field: str = "close", lookback: int = 100
    ) -> list[float]:
        buf = self._bars.get(symbol)
        if not buf:
            return []
        recent = list(buf)[-lookback:]
        try:
            return [float(getattr(b, field)) for b in recent]
        except AttributeError:
            # Unknown field name: return empty rather than crash the strategy.
            return []

    def bars(self, symbol: str, lookback: int = 100) -> list[Bar]:
        buf = self._bars.get(symbol)
        if not buf:
            return []
        return list(buf)[-lookback:]

    def quote(self, symbol: str) -> Optional[Quote]:
        return self._quotes.get(symbol)

    # ------------------------------------------------------------------ #
    # StrategyContext: account                                            #
    # ------------------------------------------------------------------ #
    def get_position(self, symbol: str) -> Optional[Position]:
        return _as_position(self._proxy("get_position", symbol=symbol))

    def positions(self) -> list[Position]:
        result = self._proxy("positions")
        return [_as_position(p) for p in result] if result else []

    @property
    def cash(self) -> float:
        return float(self._proxy("cash") or 0.0)

    @property
    def equity(self) -> float:
        return float(self._proxy("equity") or 0.0)

    @property
    def buying_power(self) -> float:
        return float(self._proxy("buying_power") or 0.0)

    def max_shares(self, symbol: str, fraction: float = 1.0) -> float:
        return float(self._proxy("max_shares", symbol=symbol, fraction=fraction) or 0.0)

    # ------------------------------------------------------------------ #
    # StrategyContext: orders                                             #
    # ------------------------------------------------------------------ #
    def buy(
        self,
        symbol: str,
        quantity: float,
        *,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> Optional[Order]:
        return _as_order(
            self._proxy(
                "buy",
                symbol=symbol,
                quantity=quantity,
                order_type=order_type,
                limit_price=limit_price,
                stop_price=stop_price,
                take_profit=take_profit,
                stop_loss=stop_loss,
            )
        )

    def sell(
        self,
        symbol: str,
        quantity: float,
        *,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> Optional[Order]:
        return _as_order(
            self._proxy(
                "sell",
                symbol=symbol,
                quantity=quantity,
                order_type=order_type,
                limit_price=limit_price,
                stop_price=stop_price,
                take_profit=take_profit,
                stop_loss=stop_loss,
            )
        )

    def close(self, symbol: str) -> Optional[Order]:
        return _as_order(self._proxy("close", symbol=symbol))

    def cancel(self, order_id: str) -> None:
        self._proxy("cancel", order_id=order_id)

    def set_leverage(self, leverage: float) -> None:
        self._proxy("set_leverage", leverage=leverage)

    # ------------------------------------------------------------------ #
    # StrategyContext: misc                                               #
    # ------------------------------------------------------------------ #
    def log(self, *args: Any) -> None:
        """Append a log line to the captured buffer AND emit a strategy_log
        event so the WS layer streams it to the UI in real time."""
        text = " ".join(str(a) for a in args)
        self.logs.append(text)
        bus.publish_soon(
            Event(
                type=EventType.STRATEGY_LOG,
                portfolio_id=self._portfolio_id,
                data={"strategy_id": self._strategy_id, "message": text},
            )
        )

    def param(self, key: str, default: Any = None) -> Any:
        return self._params.get(key, default)


def _classify(symbol: str):
    """Local import wrapper to avoid a hard import cycle at module load."""
    from ..core.symbols import classify

    return classify(symbol)


def _as_position(value: Any) -> Optional[Position]:
    """Coerce a proxy reply into a :class:`Position`.

    The subprocess proxy answers with a JSON dict (the parent serializes engine
    models via ``model_dump(mode="json")``), but the ``StrategyContext`` contract
    requires a real ``Position`` so user code can do ``pos.side.value``. Accept an
    already-materialized ``Position`` too (e.g. an in-process proxy)."""
    if value is None:
        return None
    if isinstance(value, Position):
        return value
    if isinstance(value, dict):
        return Position.model_validate(value)
    return None


def _as_order(value: Any) -> Optional[Order]:
    """Coerce a proxy reply into an :class:`Order` (see :func:`_as_position`)."""
    if value is None:
        return None
    if isinstance(value, Order):
        return value
    if isinstance(value, dict):
        return Order.model_validate(value)
    return None
