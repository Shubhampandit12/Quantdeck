"""WebSocket endpoints: live quotes (`/ws/market`) and engine events
(`/ws/events`).

Both are read-mostly streams. `/ws/market` bridges the market-data service's
`subscribe()` async iterator to the socket and lets the client adjust its
symbol set on the fly. `/ws/events` bridges the process-wide event `bus` to the
socket, optionally filtered by portfolio.

Design notes:
- We run producer/consumer concurrently and cancel cleanly on disconnect.
- Frames are JSON. Quote frames are `{"type":"quote","data": Quote}`; event
  frames are the serialized `Event` (`{type, portfolio_id, timestamp, data}`).
- A slow/blocked client is dropped rather than allowed to back up the engine —
  the event bus already bounds its per-subscriber queue.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi import Query

from ..core.eventbus import bus
from ..core.symbols import normalize_symbol
from .deps import get_market_data

log = logging.getLogger("quantdeck.api.ws")


def _parse_symbols(raw: Optional[str]) -> list[str]:
    """Comma-separated ?symbols= param -> normalized symbol list."""
    if not raw:
        return []
    return [normalize_symbol(s) for s in raw.split(",") if s.strip()]


def register_ws(app: FastAPI) -> None:
    """Attach the two WebSocket routes to the app."""

    @app.websocket("/ws/market")
    async def ws_market(websocket: WebSocket) -> None:
        """Stream live quotes. Client may send
        `{"action":"subscribe"|"unsubscribe","symbols":[...]}` to change the set.
        """
        await websocket.accept()
        md = get_market_data()

        # Current subscription set (mutable via client control messages).
        symbols = set(_parse_symbols(websocket.query_params.get("symbols")))

        # A generation counter lets us restart the quote stream whenever the
        # subscription set changes without racing the old iterator.
        producer_task: Optional[asyncio.Task] = None

        async def _stream_quotes(active: set[str]) -> None:
            """Forward quotes for `active` until cancelled."""
            if not active:
                # Nothing to stream; idle until the set changes.
                while True:
                    await asyncio.sleep(3600)
            try:
                async for quote in md.subscribe(sorted(active)):
                    await websocket.send_json(
                        {"type": "quote", "data": quote.model_dump(mode="json")}
                    )
            except asyncio.CancelledError:  # resubscription / shutdown
                raise
            except Exception:  # pragma: no cover - provider hiccup, don't crash socket
                log.exception("quote stream error")

        def _restart_producer() -> None:
            nonlocal producer_task
            if producer_task is not None:
                producer_task.cancel()
            producer_task = asyncio.create_task(_stream_quotes(set(symbols)))

        _restart_producer()
        try:
            # Consume client control messages; each change restarts the stream.
            while True:
                msg = await websocket.receive_json()
                action = msg.get("action")
                changed = _parse_symbols(",".join(msg.get("symbols", [])))
                if action == "subscribe":
                    symbols.update(changed)
                    _restart_producer()
                elif action == "unsubscribe":
                    symbols.difference_update(changed)
                    _restart_producer()
                elif action == "set":
                    symbols = set(changed)
                    _restart_producer()
        except WebSocketDisconnect:
            pass
        except Exception:  # pragma: no cover - malformed frame etc.
            log.debug("ws_market closing", exc_info=True)
        finally:
            if producer_task is not None:
                producer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await producer_task

    @app.websocket("/ws/events")
    async def ws_events(
        websocket: WebSocket,
        portfolio_id: Optional[str] = Query(default=None),
    ) -> None:
        """Stream engine/strategy/AI events, optionally filtered by portfolio."""
        await websocket.accept()

        async def _forward() -> None:
            """Pump bus events to the socket until cancelled/disconnected."""
            async for event in bus.subscribe(portfolio_id=portfolio_id):
                await websocket.send_json(event.model_dump(mode="json"))

        forward_task = asyncio.create_task(_forward())
        try:
            # We don't require client messages, but reading lets us detect
            # disconnects promptly. Any received frame is ignored.
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # pragma: no cover
            log.debug("ws_events closing", exc_info=True)
        finally:
            forward_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await forward_task
