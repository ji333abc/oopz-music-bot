"""Compatibility QueuePort around the current in-memory or Redis queue."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

from oopzbot.domain.compat import (
    playback_state_to_legacy,
    queue_item_from_legacy,
    queue_item_to_legacy,
    queue_snapshot_from_legacy,
)
from oopzbot.domain.contracts import PlaybackState, QueueItem, QueueSnapshot


class LegacyQueueAdapter:
    """Expose existing queue implementations without changing stored JSON.

    The adapter deliberately refreshes the wrapped manager through its public
    properties on every call.  Legacy ``QueueManager`` can therefore recover
    from Redis degradation instead of pinning a dead client.
    """

    def __init__(self, queue: Any) -> None:
        self._queue = queue
        self._lock = threading.RLock()

    @property
    def degraded(self) -> bool:
        client = getattr(self._queue, "redis", None)
        return client is not None and type(client).__name__ == "_InMemoryRedis"

    def get_snapshot(self) -> QueueSnapshot:
        with self._lock:
            get_current = getattr(self._queue, "get_current", None)
            current = get_current() if callable(get_current) else None
            pending = self._queue.get_queue()
            get_play_state = getattr(self._queue, "get_play_state", None)
            play_state = get_play_state() if callable(get_play_state) else None
            return queue_snapshot_from_legacy(
                current,
                pending,
                play_state,
                degraded=self.degraded,
            )

    def enqueue(self, item: QueueItem) -> int:
        with self._lock:
            self._queue.add_to_queue(queue_item_to_legacy(item))
            # QueueManager returns zero-based while MusicQueue returns length.
            # Reading length gives this port one stable one-based contract.
            return int(self._queue.get_queue_length())

    def next_item(self) -> QueueItem | None:
        with self._lock:
            raw = self._queue.play_next()
            return queue_item_from_legacy(raw) if raw is not None else None

    def clear(self) -> None:
        with self._lock:
            clear_queue = getattr(self._queue, "clear_queue", None)
            if callable(clear_queue):
                clear_queue()
                return
            pending = self._queue.get_queue()
            if pending:
                self.remove_positions(range(1, len(pending) + 1))

    def remove_positions(self, positions: Sequence[int]) -> Sequence[QueueItem]:
        with self._lock:
            normalized = sorted(set(int(value) for value in positions))
            pending = self._queue.get_queue()
            if (
                not normalized
                or normalized[0] < 1
                or normalized[-1] > len(pending)
            ):
                raise IndexError("queue position out of range")

            remove_many = getattr(self._queue, "remove_positions", None)
            if callable(remove_many):
                removed = remove_many(normalized)
            else:
                removed = [pending[position - 1] for position in normalized]
                remove_one = getattr(self._queue, "remove_from_queue", None)
                if not callable(remove_one):
                    raise TypeError("queue does not support pending item removal")
                for position in reversed(normalized):
                    if remove_one(position - 1) is False:
                        raise RuntimeError(f"failed to remove queue position {position}")
            return tuple(queue_item_from_legacy(item) for item in removed)

    def get_current(self) -> QueueItem | None:
        get_current = getattr(self._queue, "get_current", None)
        raw = get_current() if callable(get_current) else None
        return queue_item_from_legacy(raw) if raw is not None else None

    def set_current(self, item: QueueItem) -> None:
        self._queue.set_current(queue_item_to_legacy(item))

    def clear_current(self) -> None:
        self._queue.clear_current()

    def get_playback_state(self) -> PlaybackState | None:
        return self.get_snapshot().playback

    def set_playback_state(self, state: PlaybackState) -> None:
        self._queue.set_play_state(playback_state_to_legacy(state))

    def clear_playback_state(self) -> None:
        self._queue.clear_play_state()
