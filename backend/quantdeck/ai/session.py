"""AI trading layer — manual NL chat + autonomous trading sessions.

Two modes, one toolbox (``ai.tools``):

* **chat(portfolio_id, message)** — a single agentic loop. The model may call
  tools (quotes, history, place_order, backtest, ...) up to
  ``settings.ai_max_steps`` times, then returns a natural-language ``reply`` plus
  a structured ``trace`` of every thought / tool call / tool result. Ideal for
  "buy 10 AAPL" or "how is my portfolio doing?".

* **start_session(portfolio_id, strategy_prompt, guardrails)** — spawns a long
  running asyncio task that periodically gathers market context, asks the model
  to act on the ``strategy_prompt`` under the ``guardrails``, and executes any
  tool calls. It streams ``ai_trace`` and ``ai_status`` events on the bus.
  ``pause``/``resume``/``stop`` control the task; ``stop`` is a kill-switch that
  halts trading and flattens all positions.

Everything degrades gracefully: if ``settings.ai_enabled`` is False (no
Anthropic key), chat returns a friendly "disabled" reply and sessions no-op with
an error status. No real money is ever involved — every order flows through the
engine's shared risk-check path via ``tools.dispatch``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

from ..core.config import settings
from ..core.enums import AISessionStatus, EventType
from ..core.eventbus import bus
from ..core.models import Event, utcnow
from . import tools

log = logging.getLogger("quantdeck.ai.session")

# System prompt shared by both modes — grounds the model in the paper-trading,
# demo-money reality and the tools it has.
_SYSTEM_PROMPT = (
    "You are QuantDeck's AI trading copilot operating on a PAPER-TRADING "
    "account with DEMO MONEY ONLY — no real funds are ever at risk and no real "
    "broker exists. You have tools to read market data and portfolio state and "
    "to place, cancel, and modify orders, and to run backtests. Always base "
    "decisions on real data you fetch via tools; never invent prices. When you "
    "place an order it goes through a risk-check path and may come back "
    "REJECTED with a reason — read the reason and adapt. Be concise and explain "
    "your reasoning briefly before acting."
)

_MAX_TRACE = 500  # cap in-memory trace length per session to bound memory


def _trace_step(kind: str, **fields: Any) -> dict:
    """Build an AITraceStep dict per the API contract.

    kind is one of: thought | tool_call | tool_result | message.
    """
    step: dict[str, Any] = {"kind": kind, "timestamp": utcnow().isoformat()}
    step.update({k: v for k, v in fields.items() if v is not None})
    return step


class _Session:
    """In-memory state for one autonomous trading session."""

    def __init__(
        self,
        session_id: str,
        portfolio_id: str,
        strategy_prompt: str,
        guardrails: dict,
    ) -> None:
        self.id = session_id
        self.portfolio_id = portfolio_id
        self.strategy_prompt = strategy_prompt
        self.guardrails = guardrails or {}
        self.status: AISessionStatus = AISessionStatus.IDLE
        self.message: str = ""
        self.trace: list[dict] = []
        self.task: Optional[asyncio.Task] = None
        # Pause gate: set() == running, cleared == paused.
        self.resume_event = asyncio.Event()
        self.resume_event.set()
        self.stop_flag = False
        # Rate-limiting + risk tracking.
        self._order_times: list[float] = []
        self.starting_equity: Optional[float] = None

    def add_trace(self, step: dict) -> None:
        self.trace.append(step)
        if len(self.trace) > _MAX_TRACE:
            del self.trace[: len(self.trace) - _MAX_TRACE]


class AISessionManager:
    """Owns manual chat and the pool of autonomous trading sessions."""

    def __init__(self, engine, market_data, backtester) -> None:
        self.engine = engine
        self.market_data = market_data
        self.backtester = backtester
        self._sessions: dict[str, _Session] = {}
        self._client = None  # lazily-created AsyncAnthropic

    # ------------------------------------------------------------------ #
    # Anthropic client                                                   #
    # ------------------------------------------------------------------ #
    def _get_client(self):
        """Lazily construct the AsyncAnthropic client (only when enabled)."""
        if self._client is None:
            # Imported lazily so the module imports fine without the SDK/key.
            from anthropic import AsyncAnthropic

            key = settings.anthropic_api_key or None
            # AsyncAnthropic also reads ANTHROPIC_API_KEY from the environment.
            self._client = AsyncAnthropic(api_key=key) if key else AsyncAnthropic()
        return self._client

    # ------------------------------------------------------------------ #
    # Manual one-shot chat                                               #
    # ------------------------------------------------------------------ #
    async def chat(self, portfolio_id: str, message: str) -> dict:
        """Run a single agentic loop and return {reply, trace}."""
        if not settings.ai_enabled:
            return {
                "reply": "AI is disabled — set ANTHROPIC_API_KEY to enable it.",
                "trace": [],
            }

        trace: list[dict] = []
        try:
            reply = await self._run_agent_loop(
                portfolio_id=portfolio_id,
                user_content=message,
                trace=trace,
                on_order=None,
            )
        except Exception as exc:  # noqa: BLE001 - report, don't crash the API
            log.exception("AI chat failed")
            trace.append(_trace_step("message", text=f"error: {exc}"))
            return {"reply": f"Sorry, the AI request failed: {exc}", "trace": trace}
        return {"reply": reply, "trace": trace}

    async def _run_agent_loop(
        self,
        *,
        portfolio_id: str,
        user_content: str,
        trace: list[dict],
        on_order,
    ) -> str:
        """Drive the Anthropic tool-use loop until the model stops calling tools.

        Appends AITraceStep dicts to ``trace``. ``on_order`` (if given) is an
        async callback invoked with (tool_name, result_dict) after each tool
        call — used by autonomous sessions to enforce guardrails/rate limits and
        stream events. Returns the model's final text reply.

        The loop is capped at ``settings.ai_max_steps`` iterations.
        """
        client = self._get_client()
        specs = tools.tool_specs()
        messages: list[dict] = [{"role": "user", "content": user_content}]
        final_text = ""

        for _step in range(max(1, settings.ai_max_steps)):
            resp = await client.messages.create(
                model=settings.ai_model,
                max_tokens=2048,
                system=_SYSTEM_PROMPT,
                tools=specs,
                messages=messages,
            )

            # Record assistant text (thoughts) and collect tool-use blocks.
            assistant_blocks: list[dict] = []
            tool_uses: list[Any] = []
            for block in resp.content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    text = block.text
                    if text:
                        trace.append(_trace_step("thought", text=text))
                        final_text = text
                    assistant_blocks.append({"type": "text", "text": text})
                elif btype == "tool_use":
                    tool_uses.append(block)
                    assistant_blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )

            messages.append({"role": "assistant", "content": assistant_blocks})

            # No tool calls -> the model is done answering.
            if not tool_uses:
                break

            # Execute each requested tool and feed results back.
            tool_results: list[dict] = []
            for tu in tool_uses:
                tool_name = tu.name
                tool_args = tu.input or {}
                trace.append(
                    _trace_step("tool_call", tool=tool_name, args=tool_args)
                )
                result = await tools.dispatch(
                    tool_name,
                    tool_args,
                    engine=self.engine,
                    market_data=self.market_data,
                    backtester=self.backtester,
                    portfolio_id=portfolio_id,
                )
                trace.append(
                    _trace_step("tool_result", tool=tool_name, result=result)
                )
                if on_order is not None:
                    await on_order(tool_name, tool_args, result)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(result),
                    }
                )
            messages.append({"role": "user", "content": tool_results})
        else:
            # Loop exhausted the step budget without a natural stop.
            trace.append(
                _trace_step(
                    "message",
                    text=f"(stopped after {settings.ai_max_steps} steps)",
                )
            )

        return final_text or "(no reply)"

    # ------------------------------------------------------------------ #
    # Autonomous sessions                                                #
    # ------------------------------------------------------------------ #
    async def start_session(
        self,
        portfolio_id: str,
        strategy_prompt: str,
        guardrails: Optional[dict] = None,
    ) -> dict:
        """Create and launch an autonomous trading session."""
        if not settings.ai_enabled:
            return {
                "session_id": "",
                "status": AISessionStatus.ERROR.value,
                "message": "AI is disabled — set ANTHROPIC_API_KEY to enable it.",
            }

        session_id = uuid.uuid4().hex
        session = _Session(session_id, portfolio_id, strategy_prompt, guardrails or {})
        session.status = AISessionStatus.RUNNING
        self._sessions[session_id] = session
        session.task = asyncio.create_task(self._session_loop(session))
        await self._emit_status(session)
        return {"session_id": session_id, "status": session.status.value}

    async def pause(self, session_id: str) -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            return {"status": AISessionStatus.ERROR.value, "message": "no such session"}
        if session.status == AISessionStatus.RUNNING:
            session.status = AISessionStatus.PAUSED
            session.resume_event.clear()
            await self._emit_status(session)
        return {"status": session.status.value}

    async def resume(self, session_id: str) -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            return {"status": AISessionStatus.ERROR.value, "message": "no such session"}
        if session.status == AISessionStatus.PAUSED:
            session.status = AISessionStatus.RUNNING
            session.resume_event.set()
            await self._emit_status(session)
        return {"status": session.status.value}

    async def stop(self, session_id: str) -> dict:
        """Kill-switch: stop the loop and flatten all positions."""
        session = self._sessions.get(session_id)
        if session is None:
            return {"status": AISessionStatus.ERROR.value, "message": "no such session"}
        session.stop_flag = True
        session.status = AISessionStatus.STOPPED
        session.resume_event.set()  # unblock a paused loop so it can exit
        if session.task is not None:
            session.task.cancel()
            try:
                await session.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Kill-switch semantics: flatten everything the session may hold.
        await self._flatten(session, reason="session stopped (kill-switch)")
        await self._emit_status(session)
        return {"status": session.status.value}

    def get_session(self, session_id: str) -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            return {
                "session_id": session_id,
                "status": AISessionStatus.ERROR.value,
                "trace": [],
                "message": "no such session",
            }
        return {
            "session_id": session.id,
            "status": session.status.value,
            "trace": list(session.trace),
        }

    # ------------------------------------------------------------------ #
    # Autonomous loop internals                                          #
    # ------------------------------------------------------------------ #
    async def _session_loop(self, session: _Session) -> None:
        """The background task body for an autonomous session.

        Each cycle: honor pause/stop, check the daily-loss guardrail, gather a
        compact market+account context, ask the model to act, execute its tool
        calls (with per-order guardrail enforcement), then sleep before the next
        cycle. Runs until stopped or an unrecoverable error occurs.
        """
        try:
            # Snapshot starting equity for the max_daily_loss guardrail.
            try:
                p = await self.engine.get_portfolio(session.portfolio_id)
                session.starting_equity = p.equity
            except Exception:  # noqa: BLE001
                session.starting_equity = None

            interval_s = float(session.guardrails.get("interval_seconds", 30.0))

            while not session.stop_flag:
                # Block here while paused (resume_event is cleared).
                await session.resume_event.wait()
                if session.stop_flag:
                    break

                # Daily-loss kill-switch check before doing anything.
                if await self._daily_loss_breached(session):
                    session.add_trace(
                        _trace_step(
                            "message",
                            text="max_daily_loss breached — stopping session.",
                        )
                    )
                    await self._emit_trace(session, session.trace[-1])
                    session.stop_flag = True
                    session.status = AISessionStatus.STOPPED
                    await self._flatten(session, reason="max_daily_loss breached")
                    break

                await self._run_one_cycle(session)

                # Cooperative sleep so pause/stop take effect promptly.
                try:
                    await asyncio.sleep(interval_s)
                except asyncio.CancelledError:
                    break

            if session.status not in (AISessionStatus.STOPPED, AISessionStatus.ERROR):
                session.status = AISessionStatus.STOPPED
        except asyncio.CancelledError:
            # Normal on stop(); status/flatten handled by stop().
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("AI session %s crashed", session.id)
            session.status = AISessionStatus.ERROR
            session.message = str(exc)
            session.add_trace(_trace_step("message", text=f"session error: {exc}"))
        finally:
            await self._emit_status(session)

    async def _run_one_cycle(self, session: _Session) -> None:
        """One think-act cycle of an autonomous session."""
        guard_text = self._guardrail_prompt(session.guardrails)
        prompt = (
            f"Autonomous trading strategy directive:\n{session.strategy_prompt}\n\n"
            f"{guard_text}\n\n"
            "Assess the current market and your portfolio using tools, then take "
            "at most a few well-justified actions this cycle (or none if nothing "
            "is worth doing). Explain briefly."
        )
        cycle_trace: list[dict] = []

        # The autonomous loop routes every tool call through _guarded_dispatch,
        # which enforces per-order guardrails (allowed_symbols, notional cap,
        # rate limit) before touching the engine's shared risk-check path.
        try:
            reply = await self._run_agent_loop_guarded(session, prompt, cycle_trace)
            if reply:
                cycle_trace.append(_trace_step("message", text=reply))
        except Exception as exc:  # noqa: BLE001
            cycle_trace.append(_trace_step("message", text=f"cycle error: {exc}"))

        # Persist + stream this cycle's trace.
        for step in cycle_trace:
            session.add_trace(step)
            await self._emit_trace(session, step)

    async def _run_agent_loop_guarded(
        self, session: _Session, prompt: str, trace: list[dict]
    ) -> str:
        """Agentic loop for a session with guardrail-enforced order placement."""
        client = self._get_client()
        specs = tools.tool_specs()
        messages: list[dict] = [{"role": "user", "content": prompt}]
        final_text = ""

        for _step in range(max(1, settings.ai_max_steps)):
            resp = await client.messages.create(
                model=settings.ai_model,
                max_tokens=2048,
                system=_SYSTEM_PROMPT,
                tools=specs,
                messages=messages,
            )
            assistant_blocks: list[dict] = []
            tool_uses: list[Any] = []
            for block in resp.content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    if block.text:
                        trace.append(_trace_step("thought", text=block.text))
                        final_text = block.text
                    assistant_blocks.append({"type": "text", "text": block.text})
                elif btype == "tool_use":
                    tool_uses.append(block)
                    assistant_blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
            messages.append({"role": "assistant", "content": assistant_blocks})

            if not tool_uses:
                break

            tool_results: list[dict] = []
            for tu in tool_uses:
                trace.append(_trace_step("tool_call", tool=tu.name, args=tu.input or {}))
                result = await self._guarded_dispatch(session, tu.name, tu.input or {})
                trace.append(_trace_step("tool_result", tool=tu.name, result=result))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(result),
                    }
                )
            messages.append({"role": "user", "content": tool_results})
        return final_text

    async def _guarded_dispatch(
        self, session: _Session, tool_name: str, tool_args: dict
    ) -> dict:
        """Dispatch a tool call, enforcing guardrails on ``place_order``."""
        if tool_name == "place_order":
            block = self._check_order_guardrails(session, tool_args)
            if block is not None:
                return {"error": f"blocked by guardrail: {block}", "guardrail": block}
            # Record the order timestamp for the rate limiter.
            session._order_times.append(time.monotonic())

        return await tools.dispatch(
            tool_name,
            tool_args,
            engine=self.engine,
            market_data=self.market_data,
            backtester=self.backtester,
            portfolio_id=session.portfolio_id,
        )

    def _check_order_guardrails(
        self, session: _Session, args: dict
    ) -> Optional[str]:
        """Return a rejection reason if an order violates guardrails, else None."""
        g = session.guardrails

        # allowed_symbols
        allowed = g.get("allowed_symbols")
        if allowed:
            sym = str(args.get("symbol", "")).upper()
            allowed_up = {str(s).upper() for s in allowed}
            if sym not in allowed_up:
                return f"symbol {sym} not in allowed_symbols"

        # max_order_notional (best-effort: qty * limit_price when available).
        max_notional = g.get("max_order_notional")
        if max_notional is not None:
            price = args.get("limit_price") or args.get("stop_price")
            if price is not None:
                try:
                    notional = float(args.get("quantity", 0)) * float(price)
                    if notional > float(max_notional):
                        return (
                            f"order notional {notional:.2f} exceeds "
                            f"max_order_notional {max_notional}"
                        )
                except (TypeError, ValueError):
                    pass  # can't compute; let the engine's risk path decide

        # max_orders_per_min (rate limit over a rolling 60s window).
        max_per_min = g.get("max_orders_per_min")
        if max_per_min is not None:
            now = time.monotonic()
            session._order_times = [t for t in session._order_times if now - t < 60.0]
            if len(session._order_times) >= int(max_per_min):
                return f"rate limit: >= {max_per_min} orders/min"

        return None

    async def _daily_loss_breached(self, session: _Session) -> bool:
        """True if realized+unrealized loss since start exceeds max_daily_loss."""
        max_loss = session.guardrails.get("max_daily_loss")
        if max_loss is None or session.starting_equity is None:
            return False
        try:
            p = await self.engine.get_portfolio(session.portfolio_id)
        except Exception:  # noqa: BLE001
            return False
        loss = session.starting_equity - p.equity
        return loss >= float(max_loss)

    async def _flatten(self, session: _Session, reason: str) -> None:
        """Kill-switch primitive: close every open position for the portfolio."""
        try:
            orders = await self.engine.flatten_all(session.portfolio_id)
            step = _trace_step(
                "message",
                text=f"{reason}: flattened {len(orders)} position(s).",
            )
            session.add_trace(step)
            await self._emit_trace(session, step)
        except Exception as exc:  # noqa: BLE001
            log.warning("flatten_all failed for session %s: %s", session.id, exc)

    # ------------------------------------------------------------------ #
    # Prompt / event helpers                                             #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _guardrail_prompt(guardrails: dict) -> str:
        """Render guardrails as a human/LLM-readable directive."""
        if not guardrails:
            return "Guardrails: none configured (still act conservatively)."
        parts = ["Guardrails you MUST respect:"]
        if guardrails.get("allowed_symbols"):
            parts.append(f"- only trade: {', '.join(guardrails['allowed_symbols'])}")
        if guardrails.get("max_order_notional") is not None:
            parts.append(
                f"- max notional per order: {guardrails['max_order_notional']}"
            )
        if guardrails.get("max_orders_per_min") is not None:
            parts.append(f"- max {guardrails['max_orders_per_min']} orders/minute")
        if guardrails.get("max_daily_loss") is not None:
            parts.append(
                f"- session halts if daily loss reaches {guardrails['max_daily_loss']}"
            )
        return "\n".join(parts)

    async def _emit_status(self, session: _Session) -> None:
        """Publish an ai_status event on the bus."""
        await bus.publish(
            Event(
                type=EventType.AI_STATUS,
                portfolio_id=session.portfolio_id,
                data={
                    "session_id": session.id,
                    "status": session.status.value,
                    "message": session.message,
                },
            )
        )

    async def _emit_trace(self, session: _Session, step: dict) -> None:
        """Publish a single ai_trace step on the bus."""
        await bus.publish(
            Event(
                type=EventType.AI_TRACE,
                portfolio_id=session.portfolio_id,
                data={"session_id": session.id, "step": step},
            )
        )
