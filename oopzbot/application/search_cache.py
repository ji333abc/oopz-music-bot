"""Bounded TTL/LRU search cache shared by command transports."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from threading import Event, RLock
from typing import Any

SearchKey = tuple[str, str, int, int]


def normalize_search_keyword(value: object) -> str:
    return " ".join(str(value or "").strip().split())


@dataclass(slots=True)
class _CacheEntry:
    value: list[dict[str, Any]]
    expires_at: float
    negative: bool


@dataclass(slots=True)
class _Flight:
    event: Event
    value: list[dict[str, Any]] | None = None
    error: BaseException | None = None


class SearchCache:
    def __init__(
        self,
        *,
        enabled: bool = True,
        ttl_seconds: int = 60,
        negative_ttl_seconds: int = 10,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if int(ttl_seconds) < 1 or int(negative_ttl_seconds) < 1:
            raise ValueError("search cache TTL must be positive")
        if int(max_entries) < 1:
            raise ValueError("search cache capacity must be positive")
        self.enabled = bool(enabled)
        self.ttl_seconds = int(ttl_seconds)
        self.negative_ttl_seconds = int(negative_ttl_seconds)
        self.max_entries = int(max_entries)
        self._clock = clock
        self._entries: OrderedDict[SearchKey, _CacheEntry] = OrderedDict()
        self._flights: dict[SearchKey, _Flight] = {}
        self._lock = RLock()
        self._stats = {
            "hit": 0,
            "miss": 0,
            "coalesced": 0,
            "negative_hit": 0,
            "eviction": 0,
            "upstream_error": 0,
        }

    @staticmethod
    def key(
        platform: object,
        keyword: object,
        *,
        limit: int,
        offset: int = 0,
    ) -> SearchKey:
        return (
            str(platform or "").strip().lower(),
            normalize_search_keyword(keyword),
            max(1, int(limit)),
            max(0, int(offset)),
        )

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _get_locked(self, key: SearchKey, now: float) -> list[dict[str, Any]] | None:
        entry = self._entries.pop(key, None)
        if entry is None:
            return None
        if entry.expires_at <= now:
            return None
        self._entries[key] = entry
        self._stats["negative_hit" if entry.negative else "hit"] += 1
        return deepcopy(entry.value)

    def get_or_load(
        self,
        key: SearchKey,
        loader: Callable[[], list[dict[str, Any]]],
        *,
        cacheable: Callable[[list[dict[str, Any]]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return deepcopy(list(loader()))

        with self._lock:
            cached = self._get_locked(key, float(self._clock()))
            if cached is not None:
                return cached
            flight = self._flights.get(key)
            if flight is None:
                flight = _Flight(Event())
                self._flights[key] = flight
                self._stats["miss"] += 1
                leader = True
            else:
                self._stats["coalesced"] += 1
                leader = False

        if not leader:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            return deepcopy(flight.value or [])

        try:
            value = deepcopy(list(loader()))
        except BaseException as exc:
            with self._lock:
                flight.error = exc
                self._stats["upstream_error"] += 1
                self._flights.pop(key, None)
                flight.event.set()
            raise

        with self._lock:
            if cacheable is not None and not cacheable(value):
                self._stats["upstream_error"] += 1
                flight.value = deepcopy(value)
                self._flights.pop(key, None)
                flight.event.set()
                return value
            negative = not value
            ttl = self.negative_ttl_seconds if negative else self.ttl_seconds
            self._entries[key] = _CacheEntry(
                value=deepcopy(value),
                expires_at=float(self._clock()) + ttl,
                negative=negative,
            )
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
                self._stats["eviction"] += 1
            flight.value = deepcopy(value)
            self._flights.pop(key, None)
            flight.event.set()
        return value

    def search(
        self,
        platform: object,
        keyword: object,
        *,
        limit: int,
        offset: int = 0,
        loader: Callable[[], list[dict[str, Any]]],
        cacheable: Callable[[list[dict[str, Any]]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        return self.get_or_load(
            self.key(platform, keyword, limit=limit, offset=offset),
            loader,
            cacheable=cacheable,
        )

    def snapshot(self) -> dict[str, int | bool]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "capacity": self.max_entries,
                "size": len(self._entries),
                "in_flight": len(self._flights),
                **self._stats,
            }
