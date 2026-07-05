"""A tiny async TTL cache used by the market-data layer.

Quotes, OHLCV responses, and symbol lists are cached here with a per-key
expiry so that repeated reads (a busy UI, a strategy loop, the AI tool loop)
do not hammer upstream providers and so that a transient provider hiccup can
be papered over by serving a slightly stale value.

The cache is intentionally minimal: an in-process dict guarded by an
``asyncio.Lock``. It stores arbitrary values (``Quote``, ``OHLCVResponse``,
``list[SymbolInfo]``, ...) keyed by a caller-supplied string. Expired entries
are evicted lazily on access (and opportunistically on set).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class _Entry:
    """A single cached value with its absolute expiry timestamp (monotonic)."""

    value: Any
    expires_at: float


class TTLCache:
    """An async, per-key TTL cache.

    All access is serialized through a single lock. That is more than fast
    enough for the platform's scale (a handful of symbols, sub-second reads)
    and keeps the semantics dead simple: no torn reads, no background sweeper.
    """

    def __init__(self, default_ttl: float = 2.0, max_entries: int = 4096) -> None:
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._store: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        """Return the cached value for ``key`` or ``None`` if missing/expired.

        An expired entry is intentionally *retained* (not evicted) so that
        :meth:`get_stale` can still serve it as an honest last-known fallback on
        provider failure / rate-limit. Expired entries are bounded by the
        capacity-based eviction in :meth:`set` and never grow without limit.
        """
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at <= time.monotonic():
                return None
            return entry.value

    async def get_stale(self, key: str) -> Optional[Any]:
        """Return the cached value ignoring expiry (best-effort fallback).

        Used on provider failure / rate-limit so we can honestly serve the
        last-known value rather than fabricating a fresh price. Callers should
        still label the data as potentially stale.
        """
        async with self._lock:
            entry = self._store.get(key)
            return entry.value if entry is not None else None

    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Store ``value`` under ``key`` for ``ttl`` seconds (default TTL if None)."""
        expiry = time.monotonic() + (self._default_ttl if ttl is None else ttl)
        async with self._lock:
            # Opportunistically bound memory: if we are at capacity, drop the
            # entry that expires soonest (cheap, good-enough eviction).
            if key not in self._store and len(self._store) >= self._max_entries:
                oldest = min(
                    self._store, key=lambda k: self._store[k].expires_at, default=None
                )
                if oldest is not None:
                    self._store.pop(oldest, None)
            self._store[key] = _Entry(value=value, expires_at=expiry)

    async def invalidate(self, key: str) -> None:
        """Remove a single key if present."""
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self) -> None:
        """Drop every cached entry."""
        async with self._lock:
            self._store.clear()
