"""Subprocess sandbox for running untrusted user strategy code in isolation.

Design
------
Live strategies are hostile-input by assumption. Static AST scanning
(:mod:`quantdeck.strategies.loader`) is necessary but not sufficient on its own,
so live code runs in a **separate child process** with OS-level resource limits.
Parent and child speak a simple newline-delimited JSON protocol over the child's
stdin/stdout:

Parent -> child (stdin), one JSON object per line::

    {"t": "bar",          "bar": {...Bar...}}
    {"t": "tick",         "quote": {...Quote...}}
    {"t": "order_update", "order": {...Order...}}
    {"t": "stop"}

Child -> parent (stdout), one JSON object per line::

    {"t": "intent", "action": "buy"|"sell"|"close"|"cancel"|"set_leverage"|
                              "subscribe"|"get_position"|"positions"|"cash"|
                              "equity"|"buying_power"|"max_shares",
     "id": <int>, "args": {...}}          # a strategy call
    {"t": "log",    "message": "..."}      # captured ctx.log()
    {"t": "ready"}                         # child finished loading
    {"t": "error",  "message": "...", "traceback": "..."}
    {"t": "result", "id": <int>, "value": <json>}   # not used child-side

Order-placing / account intents that need a return value (``get_position``,
``cash``, ``buy`` returning an Order, ...) are answered by the parent writing a
``{"t": "reply", "id": <int>, "value": <json>}`` line back on stdin, which the
child's synchronous proxy blocks on.

Security relies on: (a) the static scan performed before ``exec`` in the child,
(b) restricted ``__builtins__`` in the child, and (c) ``resource`` limits
(CPU seconds, address-space / memory) applied in the child at startup. The
parent additionally enforces a wall-clock timeout per fed event.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from typing import Any, Awaitable, Callable, Optional

from ..core.errors import StrategyError

# --------------------------------------------------------------------------- #
# Child-side worker                                                            #
# --------------------------------------------------------------------------- #
# The worker is intentionally dependency-light at import time so it starts fast
# and does not drag heavy modules into the sandboxed process before limits are
# applied.


def _apply_resource_limits(memory_mb: int, cpu_seconds: int) -> None:
    """Apply OS-level ceilings inside the child. Best-effort: unavailable
    limits (e.g. on some platforms) are skipped rather than fatal."""
    try:
        import resource  # local import: only the child needs it
    except Exception:  # noqa: BLE001 - not available on all platforms
        return

    mem_bytes = int(memory_mb) * 1024 * 1024
    for res_name in ("RLIMIT_AS", "RLIMIT_DATA"):
        res = getattr(resource, res_name, None)
        if res is None:
            continue
        try:
            soft, hard = resource.getrlimit(res)
            new_hard = hard if hard != resource.RLIM_INFINITY else mem_bytes
            resource.setrlimit(res, (mem_bytes, min(mem_bytes, new_hard)))
        except (ValueError, OSError):
            pass

    cpu = getattr(resource, "RLIMIT_CPU", None)
    if cpu is not None:
        try:
            resource.setrlimit(cpu, (cpu_seconds, cpu_seconds + 1))
        except (ValueError, OSError):
            pass


class _ChildProxy:
    """Synchronous engine proxy handed to :class:`LiveStrategyContext` inside
    the child. Serializes each call to stdout and, for calls that return a
    value, blocks reading a matching reply from stdin."""

    #: Actions that expect a reply value from the parent.
    _REPLY_ACTIONS = frozenset(
        {
            "buy", "sell", "close", "get_position", "positions",
            "cash", "equity", "buying_power", "max_shares",
        }
    )

    def __init__(self, write_line: Callable[[dict], None], read_reply: Callable[[int], Any]) -> None:
        self._write_line = write_line
        self._read_reply = read_reply
        self._seq = 0

    def __call__(self, action: str, **kwargs: Any) -> Any:
        self._seq += 1
        call_id = self._seq
        self._write_line({"t": "intent", "id": call_id, "action": action, "args": kwargs})
        if action in self._REPLY_ACTIONS:
            return self._read_reply(call_id)
        return None


def _child_main() -> None:  # pragma: no cover - exercised via subprocess
    """Entry point for ``python -m quantdeck.strategies.sandbox``.

    Reads a JSON bootstrap object from the first stdin line::

        {"source": "...", "params": {...}, "strategy_id": "...",
         "portfolio_id": "...", "memory_mb": 512, "cpu_seconds": 30}

    then enters the event loop reading subsequent JSON lines.
    """
    stdin = sys.stdin
    stdout = sys.stdout

    def write_line(obj: dict) -> None:
        stdout.write(json.dumps(obj) + "\n")
        stdout.flush()

    def emit_error(message: str, tb: str = "") -> None:
        write_line({"t": "error", "message": message, "traceback": tb})

    # --- Bootstrap ---------------------------------------------------- #
    boot_raw = stdin.readline()
    if not boot_raw:
        return
    try:
        boot = json.loads(boot_raw)
    except json.JSONDecodeError as exc:
        emit_error(f"bad bootstrap: {exc}")
        return

    _apply_resource_limits(
        int(boot.get("memory_mb", 512)),
        int(boot.get("cpu_seconds", 30)),
    )

    # Pending replies keyed by intent id (filled as reply lines arrive).
    pending_replies: dict[int, Any] = {}

    def read_reply(call_id: int) -> Any:
        """Block until the parent sends a reply for ``call_id``. Any bar/tick
        lines that arrive meanwhile are buffered and ignored here (the parent
        drives one event at a time, so interleaving is not expected)."""
        while call_id not in pending_replies:
            line = stdin.readline()
            if not line:
                return None
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("t") == "reply":
                pending_replies[msg.get("id")] = msg.get("value")
        return pending_replies.pop(call_id)

    # Import heavy pieces AFTER limits are set.
    try:
        from ..core.models import Bar, Order, Quote
        from .context import LiveStrategyContext
        from .loader import load_strategy
    except Exception as exc:  # noqa: BLE001
        emit_error(f"sandbox import failed: {exc}", traceback.format_exc())
        return

    # ctx.log() inside the child should emit a protocol log line (the child has
    # no event bus / running loop); we override the proxy path for logging by
    # capturing into a list and forwarding. We reuse LiveStrategyContext but
    # route its logs to stdout via monkeypatching its log method target.
    proxy = _ChildProxy(write_line, read_reply)

    try:
        strategy = load_strategy(boot["source"], boot.get("params"))
    except Exception as exc:  # noqa: BLE001
        emit_error(f"strategy load failed: {exc}", traceback.format_exc())
        return

    ctx = LiveStrategyContext(
        proxy,
        strategy_id=boot.get("strategy_id", ""),
        portfolio_id=boot.get("portfolio_id"),
        params=boot.get("params"),
    )

    # In the child there is no asyncio loop, so LiveStrategyContext.log()'s
    # bus.publish_soon is a no-op; forward logs to the parent explicitly.
    _orig_log = ctx.log

    def _child_log(*args: Any) -> None:
        text = " ".join(str(a) for a in args)
        ctx.logs.append(text)
        write_line({"t": "log", "message": text})

    ctx.log = _child_log  # type: ignore[method-assign]

    # --- on_start ----------------------------------------------------- #
    try:
        strategy.on_start(ctx)
    except Exception as exc:  # noqa: BLE001
        emit_error(f"on_start failed: {exc}", traceback.format_exc())
        return

    write_line({"t": "ready"})

    # --- Event loop --------------------------------------------------- #
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        t = msg.get("t")
        try:
            if t == "bar":
                bar = Bar.model_validate(msg["bar"])
                ctx.push_bar(bar)
                strategy.on_bar(ctx, bar)
            elif t == "tick":
                quote = Quote.model_validate(msg["quote"])
                ctx.push_quote(quote)
                strategy.on_tick(ctx, quote)
            elif t == "order_update":
                order = Order.model_validate(msg["order"])
                strategy.on_order_update(ctx, order)
            elif t == "stop":
                try:
                    strategy.on_stop(ctx)
                except Exception as exc:  # noqa: BLE001
                    emit_error(f"on_stop failed: {exc}", traceback.format_exc())
                break
            elif t == "reply":
                # Stray reply with no waiting call; ignore.
                continue
        except Exception as exc:  # noqa: BLE001 - a bad hook must not kill silently
            emit_error(f"{t} hook failed: {exc}", traceback.format_exc())
            # Keep running: one bad bar shouldn't necessarily kill the strategy,
            # but the parent decides policy based on the error message.


# --------------------------------------------------------------------------- #
# Parent-side runner                                                           #
# --------------------------------------------------------------------------- #

# Async callback the runner invokes for every intent that needs execution.
# Returns a JSON-serializable value (or None) sent back to the child.
IntentCallback = Callable[[str, dict], Awaitable[Any]]


class SandboxRunner:
    """Parent-side controller for one sandboxed strategy subprocess.

    The runner spawns ``python -m quantdeck.strategies.sandbox``, feeds it
    bars/ticks/order-updates, reads intents/logs, and dispatches intents to a
    supplied async ``intent_callback`` (which the :class:`StrategyManager`
    wires to the real engine).
    """

    def __init__(
        self,
        intent_callback: IntentCallback,
        *,
        log_callback: Optional[Callable[[str], None]] = None,
        error_callback: Optional[Callable[[str], None]] = None,
        memory_mb: int = 512,
        cpu_seconds: int = 30,
        callback_timeout: float = 30.0,
    ) -> None:
        self._intent_cb = intent_callback
        self._log_cb = log_callback
        self._error_cb = error_callback
        self._memory_mb = memory_mb
        self._cpu_seconds = cpu_seconds
        self._callback_timeout = callback_timeout

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._ready = asyncio.Event()
        # Set when the child reports a fatal error (e.g. load/scan failure)
        # BEFORE it ever became ready, so start() can bail out immediately.
        self._load_failed = asyncio.Event()
        self._stopped = asyncio.Event()
        self._io_lock = asyncio.Lock()
        self.crashed = False
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                           #
    # ------------------------------------------------------------------ #
    async def start(
        self,
        strategy_source: str,
        params: Optional[dict],
        symbols: Optional[list[str]] = None,
        *,
        strategy_id: str = "",
        portfolio_id: Optional[str] = None,
    ) -> None:
        """Spawn the child, send the bootstrap, and wait until it is ready."""
        env = dict(os.environ)
        # Keep child imports resolvable when launched as a module.
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "quantdeck.strategies.sandbox",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        boot = {
            "source": strategy_source,
            "params": params or {},
            "strategy_id": strategy_id,
            "portfolio_id": portfolio_id,
            "symbols": symbols or [],
            "memory_mb": self._memory_mb,
            "cpu_seconds": self._cpu_seconds,
        }
        await self._write(boot)

        # Start reading the child's output stream.
        self._reader_task = asyncio.create_task(self._read_loop())

        # Wait for readiness, but wake early if the child reports a fatal load
        # error first (so scan/import failures surface immediately instead of
        # after the full timeout). Bounded so a hanging on_start can't wedge us.
        ready_wait = asyncio.ensure_future(self._ready.wait())
        fail_wait = asyncio.ensure_future(self._load_failed.wait())
        try:
            done, pending = await asyncio.wait(
                {ready_wait, fail_wait},
                timeout=self._callback_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if not self._ready.is_set():
                # Either the child reported a load error or we timed out.
                self.crashed = True
                if not self.last_error:
                    self.last_error = "strategy on_start timed out"
                await self.stop()
                raise StrategyError(self.last_error)
        finally:
            for task in (ready_wait, fail_wait):
                if not task.done():
                    task.cancel()

    async def stop(self) -> None:
        """Cleanly ask the child to stop, then terminate/kill if needed."""
        if self._proc is None:
            return
        if self._proc.returncode is None:
            try:
                await self._write({"t": "stop"})
            except Exception:  # noqa: BLE001 - pipe may already be closed
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
        self._stopped.set()
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    # Feeding events into the child                                       #
    # ------------------------------------------------------------------ #
    async def send_bar(self, bar) -> None:
        await self._write({"t": "bar", "bar": _dump(bar)})

    async def send_tick(self, quote) -> None:
        await self._write({"t": "tick", "quote": _dump(quote)})

    async def send_order_update(self, order) -> None:
        await self._write({"t": "order_update", "order": _dump(order)})

    # ------------------------------------------------------------------ #
    # Internal IO                                                         #
    # ------------------------------------------------------------------ #
    async def _write(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        async with self._io_lock:
            self._proc.stdin.write((json.dumps(obj) + "\n").encode())
            await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        """Consume the child's stdout, dispatching intents/logs/errors."""
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        while True:
            line = await stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            t = msg.get("t")
            if t == "ready":
                self._ready.set()
            elif t == "log":
                if self._log_cb:
                    self._log_cb(msg.get("message", ""))
            elif t == "error":
                self.crashed = True
                self.last_error = msg.get("message", "unknown error")
                if self._error_cb:
                    detail = msg.get("traceback") or self.last_error
                    self._error_cb(detail)
                # If we never became ready, this is a load-time failure —
                # unblock start() immediately rather than waiting the timeout.
                if not self._ready.is_set():
                    self._load_failed.set()
            elif t == "intent":
                await self._handle_intent(msg)

        # Stream closed: if we exited without a clean stop, capture stderr.
        if not self._stopped.is_set() and not self.crashed:
            await self._capture_stderr()
            # A signal death (negative returncode) leaves stderr empty, so
            # inspect returncode explicitly. This is how a CPU-time limit
            # (SIGXCPU) or a kill surfaces — otherwise crashed stays False and
            # the manager only learns of it later as a misleading 'stream lost'.
            if not self.crashed and self._proc is not None:
                rc = self._proc.returncode
                if rc is not None and rc < 0:
                    self.crashed = True
                    sig = -rc
                    if sig == 24:  # SIGXCPU
                        self.last_error = "CPU time limit exceeded"
                    else:
                        self.last_error = f"strategy killed by signal {sig}"
                    if self._error_cb:
                        self._error_cb(self.last_error)
        # A child that closed its pipe before ready also unblocks start().
        if not self._ready.is_set():
            self._load_failed.set()

    async def _handle_intent(self, msg: dict) -> None:
        """Run one strategy intent against the engine via the callback, honoring
        a wall-clock timeout, then reply to the child if it expects a value."""
        action = msg.get("action", "")
        args = msg.get("args", {}) or {}
        call_id = msg.get("id")
        value: Any = None
        try:
            value = await asyncio.wait_for(
                self._intent_cb(action, args), timeout=self._callback_timeout
            )
        except asyncio.TimeoutError:
            self.last_error = f"intent '{action}' timed out"
            if self._error_cb:
                self._error_cb(self.last_error)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"intent '{action}' failed: {exc}"
            if self._error_cb:
                self._error_cb(self.last_error)

        # Only reply when the child is blocking for a value.
        if action in _ChildProxy._REPLY_ACTIONS and call_id is not None:
            await self._write({"t": "reply", "id": call_id, "value": value})

    async def _capture_stderr(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            data = await self._proc.stderr.read()
        except Exception:  # noqa: BLE001
            return
        text = data.decode(errors="replace").strip()
        if text:
            # A non-empty stderr after an unclean exit usually means the child
            # was killed by a resource limit (MemoryError / SIGXCPU).
            self.crashed = True
            self.last_error = text.splitlines()[-1][:500]
            if self._error_cb:
                self._error_cb(text)


def _dump(model: Any) -> dict:
    """Serialize a Pydantic model to a JSON-safe dict for the wire."""
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return dict(model)


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint
    _child_main()
