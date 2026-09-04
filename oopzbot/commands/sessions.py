"""Thread-safe requester-scoped expiring command sessions."""

from __future__ import annotations

import time
from collections.abc import Iterator, MutableMapping
from threading import RLock
from typing import Any


class ExpiringSessionStore(MutableMapping[str, dict[str, Any]]):
    """Mapping-compatible store used by legacy tests and new handlers."""

    def __init__(self, ttl_seconds: float, max_entries: int = 256) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self.max_entries = max(1, int(max_entries))
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def _purge_expired(self, now: float) -> None:
        expired = [
            key
            for key, session in self._items.items()
            if now > float(session.get("expires_at") or 0)
        ]
        for key in expired:
            self._items.pop(key, None)

    def _store(self, requester_key: str, session: dict[str, Any]) -> None:
        # Reinsert existing keys so eviction follows least-recently-written order.
        self._items.pop(requester_key, None)
        self._items[requester_key] = session
        while len(self._items) > self.max_entries:
            self._items.pop(next(iter(self._items)))

    def put(self, requester_key: str, **values: Any) -> dict[str, Any]:
        now = time.monotonic()
        session = {"expires_at": now + self.ttl_seconds, **values}
        with self._lock:
            self._purge_expired(now)
            self._store(requester_key, session)
        return session

    def get_active(self, requester_key: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._items.get(requester_key)
            if session is None:
                return None
            if time.monotonic() > float(session.get("expires_at") or 0):
                self._items.pop(requester_key, None)
                return None
            return session

    def copy(self) -> dict[str, dict[str, Any]]:
        """Compatibility snapshot for diagnostics and existing tests."""

        with self._lock:
            self._purge_expired(time.monotonic())
            return dict(self._items)

    def __getitem__(self, key: str) -> dict[str, Any]:
        with self._lock:
            self._purge_expired(time.monotonic())
            return self._items[key]

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._purge_expired(time.monotonic())
            self._store(key, value)

    def __delitem__(self, key: str) -> None:
        with self._lock:
            del self._items[key]

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            self._purge_expired(time.monotonic())
            return iter(tuple(self._items))

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired(time.monotonic())
            return len(self._items)
