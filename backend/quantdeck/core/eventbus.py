"""A tiny async pub/sub event bus.

The simulation engine, market-data streamer, strategy runtime, and AI loop all
publish `Event`s here; the WebSocket layer and in-process consumers subscribe.
Subscriptions can be filtered by portfolio and by event type. Backed by
per-subscriber asyncio.Queues so a slow consumer never blocks producers (the
queue simply drops the oldest event if it overflows).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Iterable, Optional

from .enums import EventType
from .models import Event

log = logging.getLogger("quantdeck.eventbus")


class _Subscription:
    def __init__(
        self,
        portfolio_id: Optional[str],
        types: Optional[set[EventType]],
        maxsize: int,
    ) -> None:
        self.portfolio_id = portfolio_id
        self.types = types
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)

    def matches(self, event: Event) -> bool:
        if self.portfolio_id is not None and event.portfolio_id is not None:
            if event.portfolio_id != self.portfolio_id:
                return False
        if self.types is not None and event.type not in self.types:
            return False
        return True


class EventBus:
    def __init__(self) -> None:
        self._subs: set[_Subscription] = set()
        self._lock = asyncio.Lock()

    async def publish(self, event: Event) -> None:
        # Snapshot subscribers under the lock, then deliver without holding it.
        subs = list(self._subs)
        for sub in subs:
            if not sub.matches(event):
                continue
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop oldest to make room — never block the producer.
                try:
                    sub.queue.get_nowait()
                    sub.queue.put_nowait(event)
                except asyncio.QueueEmpty:
                    pass

    def publish_soon(self, event: Event) -> None:
        """Fire-and-forget publish from sync code with a running loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish(event))

    async def subscribe(
        self,
        portfolio_id: Optional[str] = None,
        types: Optional[Iterable[EventType]] = None,
        maxsize: int = 1000,
    ) -> AsyncIterator[Event]:
        sub = _Subscription(
            portfolio_id=portfolio_id,
            types=set(types) if types else None,
            maxsize=maxsize,
        )
        async with self._lock:
            self._subs.add(sub)
        try:
            while True:
                event = await sub.queue.get()
                yield event
        finally:
            async with self._lock:
                self._subs.discard(sub)


# Process-wide singleton.
bus = EventBus()
