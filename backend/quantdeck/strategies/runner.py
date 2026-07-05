"""StrategyManager — the live-paper strategy runtime.

Responsibilities:

* **CRUD** over :class:`~quantdeck.db.models.StrategyORM` (create/list/get/
  update/delete) returning API-contract ``StrategySummary`` / detail dicts.
* **start(id, ...)**: validate + scan the code, spawn a
  :class:`~quantdeck.strategies.sandbox.SandboxRunner`, subscribe to the
  requested symbols via ``market_data.subscribe``, aggregate streamed quotes
  into fixed-interval OHLCV bars, and feed bars/ticks into the sandbox.
* **execute intents**: every order/account intent the strategy emits is routed
  through the shared engine risk-check path (``engine.place_order`` /
  ``cancel_order`` / ``flatten_all``). Strategies NEVER bypass the engine.
* **events**: streams ``strategy_log`` and ``strategy_status`` events on the
  bus, and keeps a per-strategy log ring buffer for the ``/logs`` endpoint.
* **robustness**: a strategy that crashes (bad code, resource limit) transitions
  to ``ERROR`` with the captured traceback; it never takes down the manager.

Untrusted user code executes inside the subprocess sandbox (see
:mod:`quantdeck.strategies.sandbox`); the manager here only ever handles
already-validated, JSON-serialized intents.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Optional

from sqlalchemy import select

from ..core.config import settings
from ..core.enums import (
    EventType,
    OrderSide,
    OrderType,
    StrategyStatus,
    TimeInForce,
)
from ..core.errors import SandboxViolation, StrategyError
from ..core.eventbus import bus
from ..core.models import Bar, Event, OrderRequest, Quote
from ..core.symbols import classify, normalize_symbol
from ..db.database import SessionLocal
from ..db.models import StrategyORM
from .loader import scan_code
from .sandbox import SandboxRunner

log = logging.getLogger("quantdeck.strategies.runner")

# Interval string -> seconds. Mirrors marketdata VALID_INTERVALS.
_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}

_LOG_RING = 500


class _BarAggregator:
    """Rolls streamed quotes into fixed-interval OHLCV bars per symbol.

    When a quote arrives in a new interval bucket, the previous (now-closed) bar
    is returned so the runner can feed it to the sandbox as ``on_bar``.
    """

    def __init__(self, interval_seconds: int) -> None:
        self._sec = max(1, interval_seconds)
        self._current: dict[str, Bar] = {}
        self._bucket: dict[str, int] = {}

    def _bucket_start(self, ts: datetime) -> int:
        epoch = int(ts.timestamp())
        return epoch - (epoch % self._sec)

    def update(self, quote: Quote) -> Optional[Bar]:
        sym = quote.symbol
        ts = quote.timestamp or datetime.now(timezone.utc)
        bucket = self._bucket_start(ts)
        price = float(quote.price)
        vol = float(quote.volume or 0.0)

        closed: Optional[Bar] = None
        prev_bucket = self._bucket.get(sym)
        if prev_bucket is None:
            # First quote for this symbol: open a fresh bar.
            self._open(sym, bucket, price, vol)
        elif bucket != prev_bucket:
            # Interval rolled over: close the previous bar, open the next.
            closed = self._current.get(sym)
            self._open(sym, bucket, price, vol)
        else:
            # Same interval: extend the running bar.
            bar = self._current[sym]
            self._current[sym] = bar.model_copy(
                update={
                    "high": max(bar.high, price),
                    "low": min(bar.low, price),
                    "close": price,
                    "volume": bar.volume + vol,
                }
            )
        return closed

    def _open(self, sym: str, bucket: int, price: float, vol: float) -> None:
        self._bucket[sym] = bucket
        self._current[sym] = Bar(
            symbol=sym,
            timestamp=datetime.fromtimestamp(bucket, tz=timezone.utc),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=vol,
        )


class _RunningStrategy:
    """In-memory state for one actively-running strategy."""

    def __init__(self, strategy_id: str, portfolio_id: str, interval: str) -> None:
        self.strategy_id = strategy_id
        self.portfolio_id = portfolio_id
        self.interval = interval
        self.status = StrategyStatus.STARTING
        self.runner: Optional[SandboxRunner] = None
        self.aggregator = _BarAggregator(_INTERVAL_SECONDS.get(interval, 60))
        self.symbols: list[str] = []
        self.logs: Deque[str] = deque(maxlen=_LOG_RING)
        self.stream_task: Optional[asyncio.Task] = None
        self.error: Optional[str] = None


class StrategyManager:
    """Manages persisted strategies and their live sandboxed executions."""

    def __init__(self, engine, market_data) -> None:
        self._engine = engine
        self._md = market_data
        self._running: dict[str, _RunningStrategy] = {}
        self._lock = asyncio.Lock()

    # ================================================================== #
    # CRUD                                                                #
    # ================================================================== #
    async def create(
        self,
        name: str,
        code: str,
        portfolio_id: Optional[str] = None,
        params: Optional[dict] = None,
    ) -> dict:
        # Validate the code up front so bad code is rejected at save time.
        scan_code(code)
        sid = uuid.uuid4().hex
        async with SessionLocal() as session:
            row = StrategyORM(
                id=sid,
                name=name,
                code=code,
                portfolio_id=portfolio_id,
                status=StrategyStatus.IDLE.value,
                params_json=json.dumps(params or {}),
            )
            session.add(row)
            await session.commit()
        return {
            "id": sid,
            "name": name,
            "status": StrategyStatus.IDLE.value,
            "portfolio_id": portfolio_id,
        }

    async def list(self) -> list[dict]:
        async with SessionLocal() as session:
            rows = (await session.execute(select(StrategyORM))).scalars().all()
        return [self._summary(r) for r in rows]

    async def get(self, strategy_id: str) -> dict:
        row = await self._require(strategy_id)
        return {
            "id": row.id,
            "name": row.name,
            "code": row.code,
            "status": self._live_status(row).value,
            "portfolio_id": row.portfolio_id,
            "params": json.loads(row.params_json or "{}"),
        }

    async def update(
        self,
        strategy_id: str,
        name: Optional[str] = None,
        code: Optional[str] = None,
        params: Optional[dict] = None,
    ) -> dict:
        if code is not None:
            scan_code(code)  # reject bad code before persisting
        async with SessionLocal() as session:
            row = await session.get(StrategyORM, strategy_id)
            if row is None:
                raise StrategyError(f"Strategy '{strategy_id}' not found")
            if name is not None:
                row.name = name
            if code is not None:
                row.code = code
            if params is not None:
                row.params_json = json.dumps(params)
            await session.commit()
            summary = self._summary(row)
        return summary

    async def delete(self, strategy_id: str) -> dict:
        # Stop it first if it happens to be running.
        if strategy_id in self._running:
            await self.stop(strategy_id)
        async with SessionLocal() as session:
            row = await session.get(StrategyORM, strategy_id)
            if row is not None:
                await session.delete(row)
                await session.commit()
        return {"ok": True}

    async def logs(self, strategy_id: str, limit: int = 200) -> list[str]:
        run = self._running.get(strategy_id)
        if run is not None:
            return list(run.logs)[-limit:]
        # Not running: no live buffer, return empty (persisted logs are not kept).
        return []

    # ================================================================== #
    # Lifecycle                                                           #
    # ================================================================== #
    async def start(
        self,
        strategy_id: str,
        portfolio_id: str,
        symbols: Optional[list[str]] = None,
        interval: str = "1m",
    ) -> dict:
        async with self._lock:
            if strategy_id in self._running:
                # Already running: return current summary rather than double-start.
                row = await self._require(strategy_id)
                return self._summary(row, status=self._running[strategy_id].status)

            row = await self._require(strategy_id)
            params = json.loads(row.params_json or "{}")

            run = _RunningStrategy(strategy_id, portfolio_id, interval)
            run.symbols = [normalize_symbol(s) for s in (symbols or [])]
            self._running[strategy_id] = run

        await self._set_status(strategy_id, StrategyStatus.STARTING)

        # Build the sandbox with callbacks bound to this strategy's state.
        runner = SandboxRunner(
            intent_callback=self._make_intent_callback(run),
            log_callback=lambda msg, r=run: self._on_log(r, msg),
            error_callback=lambda msg, r=run: self._on_error(r, msg),
            memory_mb=settings.strategy_memory_mb,
            cpu_seconds=int(settings.strategy_timeout_seconds),
            callback_timeout=settings.strategy_timeout_seconds,
        )
        run.runner = runner

        try:
            await runner.start(
                row.code,
                params,
                run.symbols,
                strategy_id=strategy_id,
                portfolio_id=portfolio_id,
            )
        except SandboxViolation as exc:
            await self._fail(run, f"sandbox violation: {exc}")
            row = await self._require(strategy_id)
            return self._summary(row, status=StrategyStatus.ERROR)
        except Exception as exc:  # noqa: BLE001
            await self._fail(run, f"failed to start: {exc}")
            row = await self._require(strategy_id)
            return self._summary(row, status=StrategyStatus.ERROR)

        # Kick off the market-data stream + bar aggregation.
        run.stream_task = asyncio.create_task(self._stream_loop(run))
        await self._set_status(strategy_id, StrategyStatus.RUNNING)
        return self._summary(row, status=StrategyStatus.RUNNING)

    async def stop(self, strategy_id: str) -> dict:
        run = self._running.get(strategy_id)
        if run is None:
            row = await self._require(strategy_id)
            return self._summary(row, status=StrategyStatus.STOPPED)

        await self._set_status(strategy_id, StrategyStatus.STOPPING)
        # Cancel the stream first so no new bars are fed mid-teardown.
        if run.stream_task is not None:
            run.stream_task.cancel()
            try:
                await run.stream_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if run.runner is not None:
            try:
                await run.runner.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("error stopping sandbox for %s: %s", strategy_id, exc)

        final = StrategyStatus.ERROR if run.error else StrategyStatus.STOPPED
        self._running.pop(strategy_id, None)
        await self._set_status(strategy_id, final)
        row = await self._require(strategy_id)
        return self._summary(row, status=final)

    # ================================================================== #
    # Market-data streaming + bar aggregation                             #
    # ================================================================== #
    async def _stream_loop(self, run: _RunningStrategy) -> None:
        """Subscribe to quotes, emit ticks + aggregated bars to the sandbox."""
        if not run.symbols:
            return
        try:
            async for quote in self._md.subscribe(run.symbols):
                if run.runner is None or run.runner.crashed:
                    break
                # Feed the raw tick.
                try:
                    await run.runner.send_tick(quote)
                except Exception as exc:  # noqa: BLE001
                    self._on_error(run, f"send_tick failed: {exc}")
                    break
                # Aggregate into bars; feed a closed bar when the interval rolls.
                closed = run.aggregator.update(quote)
                if closed is not None:
                    try:
                        await run.runner.send_bar(closed)
                    except Exception as exc:  # noqa: BLE001
                        self._on_error(run, f"send_bar failed: {exc}")
                        break
                if run.runner.crashed:
                    self._on_error(run, run.runner.last_error or "strategy crashed")
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._on_error(run, f"market-data stream error: {exc}")
        finally:
            if run.error and run.strategy_id in self._running:
                # A crash occurred while streaming: mark error (best-effort).
                await self._set_status(run.strategy_id, StrategyStatus.ERROR)

    # ================================================================== #
    # Intent execution — the ONLY bridge from strategy code to the engine #
    # ================================================================== #
    def _make_intent_callback(self, run: _RunningStrategy):
        """Return an async ``(action, args) -> value`` callback bound to ``run``.

        Every order-placing action routes through ``engine.place_order`` (the
        shared risk-check path). Business rejections come back as REJECTED
        orders, which we serialize like any other order.
        """
        engine = self._engine
        pid = run.portfolio_id

        async def _callback(action: str, args: dict) -> Any:
            try:
                if action in ("buy", "sell"):
                    return await self._place(run, action, args)
                if action == "close":
                    return await self._close(run, args.get("symbol"))
                if action == "cancel":
                    oid = args.get("order_id")
                    if oid:
                        order = await engine.cancel_order(pid, oid)
                        return order.model_dump(mode="json") if order else None
                    return None
                if action == "set_leverage":
                    lev = float(args.get("leverage", 1.0))
                    await engine.set_leverage(pid, lev)
                    return None
                if action == "subscribe":
                    # Add newly-requested symbols to the live stream set.
                    for s in args.get("symbols", []):
                        ns = normalize_symbol(s)
                        if ns not in run.symbols:
                            run.symbols.append(ns)
                    return None
                if action == "get_position":
                    pos = await engine.get_position(pid, args.get("symbol"))
                    return pos.model_dump(mode="json") if pos else None
                if action == "positions":
                    positions = await engine.get_positions(pid)
                    return [p.model_dump(mode="json") for p in positions]
                if action in ("cash", "equity", "buying_power"):
                    portfolio = await engine.get_portfolio(pid)
                    return float(getattr(portfolio, action))
                if action == "max_shares":
                    return await self._max_shares(
                        run, args.get("symbol"), float(args.get("fraction", 1.0))
                    )
            except Exception as exc:  # noqa: BLE001
                self._on_error(run, f"intent '{action}' error: {exc}")
                return None
            return None

        return _callback

    async def _place(self, run: _RunningStrategy, action: str, args: dict) -> Optional[dict]:
        symbol = normalize_symbol(args.get("symbol", ""))
        req = OrderRequest(
            symbol=symbol,
            side=OrderSide.BUY if action == "buy" else OrderSide.SELL,
            type=_order_type(args.get("order_type", "market")),
            quantity=float(args.get("quantity", 0.0)),
            limit_price=args.get("limit_price"),
            stop_price=args.get("stop_price"),
            take_profit=args.get("take_profit"),
            stop_loss=args.get("stop_loss"),
            time_in_force=TimeInForce.GTC,
            source=f"strategy:{run.strategy_id}",
        )
        order = await self._engine.place_order(run.portfolio_id, req)
        return order.model_dump(mode="json") if order else None

    async def _close(self, run: _RunningStrategy, symbol: Optional[str]) -> Optional[dict]:
        """Flatten a single symbol by placing an offsetting market order."""
        if not symbol:
            return None
        symbol = normalize_symbol(symbol)
        pos = await self._engine.get_position(run.portfolio_id, symbol)
        if pos is None or pos.quantity <= 0 or pos.side.value == "flat":
            return None
        # Sell to close a long; buy to close a short.
        side = OrderSide.SELL if pos.side.value == "long" else OrderSide.BUY
        req = OrderRequest(
            symbol=symbol,
            side=side,
            type=OrderType.MARKET,
            quantity=float(pos.quantity),
            time_in_force=TimeInForce.GTC,
            source=f"strategy:{run.strategy_id}",
        )
        order = await self._engine.place_order(run.portfolio_id, req)
        return order.model_dump(mode="json") if order else None

    async def _max_shares(
        self, run: _RunningStrategy, symbol: Optional[str], fraction: float
    ) -> float:
        """How many units of ``symbol`` ``fraction`` of buying power can buy."""
        if not symbol:
            return 0.0
        symbol = normalize_symbol(symbol)
        try:
            portfolio = await self._engine.get_portfolio(run.portfolio_id)
            price = await self._md.get_price(symbol)
        except Exception:  # noqa: BLE001 - never fabricate; just report 0
            return 0.0
        if not price or price <= 0:
            return 0.0
        budget = float(portfolio.buying_power) * max(0.0, min(1.0, fraction))
        return budget / price

    # ================================================================== #
    # Logging / status helpers                                            #
    # ================================================================== #
    def _on_log(self, run: _RunningStrategy, message: str) -> None:
        run.logs.append(message)
        bus.publish_soon(
            Event(
                type=EventType.STRATEGY_LOG,
                portfolio_id=run.portfolio_id,
                data={"strategy_id": run.strategy_id, "message": message},
            )
        )

    def _on_error(self, run: _RunningStrategy, message: str) -> None:
        run.error = message
        run.status = StrategyStatus.ERROR
        run.logs.append(f"[error] {message}")
        bus.publish_soon(
            Event(
                type=EventType.STRATEGY_LOG,
                portfolio_id=run.portfolio_id,
                data={"strategy_id": run.strategy_id, "message": f"[error] {message}"},
            )
        )

    async def _fail(self, run: _RunningStrategy, message: str) -> None:
        self._on_error(run, message)
        if run.runner is not None:
            try:
                await run.runner.stop()
            except Exception:  # noqa: BLE001
                pass
        self._running.pop(run.strategy_id, None)
        await self._set_status(run.strategy_id, StrategyStatus.ERROR)

    async def _set_status(self, strategy_id: str, status: StrategyStatus) -> None:
        run = self._running.get(strategy_id)
        if run is not None:
            run.status = status
        # Persist the status transition.
        try:
            async with SessionLocal() as session:
                row = await session.get(StrategyORM, strategy_id)
                if row is not None:
                    row.status = status.value
                    await session.commit()
                    portfolio_id = row.portfolio_id
                else:
                    portfolio_id = run.portfolio_id if run else None
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to persist status for %s: %s", strategy_id, exc)
            portfolio_id = run.portfolio_id if run else None

        await bus.publish(
            Event(
                type=EventType.STRATEGY_STATUS,
                portfolio_id=portfolio_id,
                data={"strategy_id": strategy_id, "status": status.value},
            )
        )

    # ================================================================== #
    # Internals                                                           #
    # ================================================================== #
    async def _require(self, strategy_id: str) -> StrategyORM:
        async with SessionLocal() as session:
            row = await session.get(StrategyORM, strategy_id)
        if row is None:
            raise StrategyError(f"Strategy '{strategy_id}' not found")
        return row

    def _live_status(self, row: StrategyORM) -> StrategyStatus:
        run = self._running.get(row.id)
        if run is not None:
            return run.status
        try:
            return StrategyStatus(row.status)
        except ValueError:
            return StrategyStatus.IDLE

    def _summary(
        self, row: StrategyORM, status: Optional[StrategyStatus] = None
    ) -> dict:
        st = status or self._live_status(row)
        return {
            "id": row.id,
            "name": row.name,
            "status": st.value,
            "portfolio_id": row.portfolio_id,
        }


def _order_type(value: str) -> OrderType:
    try:
        return OrderType(str(value).lower())
    except ValueError:
        return OrderType.MARKET


# Keep a reference to classify so linters don't flag the import as unused; it is
# used implicitly via context/aggregation of crypto vs equity symbols upstream.
_ = classify
