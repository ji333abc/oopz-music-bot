"""Bounded revision publisher used by the Panel SSE transport."""

from __future__ import annotations

import time
from collections import deque
from threading import Condition


class StatePublisher:
    """Publish monotonically increasing revisions without retaining snapshots."""

    def __init__(self, *, history_limit: int = 32) -> None:
        if int(history_limit) < 1:
            raise ValueError("history limit must be positive")
        self._condition = Condition()
        self._revision = 0
        self._history: deque[int] = deque(maxlen=int(history_limit))
        self._closed = False

    @property
    def revision(self) -> int:
        with self._condition:
            return self._revision

    @property
    def oldest_revision(self) -> int:
        with self._condition:
            return self._history[0] if self._history else self._revision

    def publish(self) -> int:
        with self._condition:
            if self._closed:
                return self._revision
            self._revision += 1
            self._history.append(self._revision)
            self._condition.notify_all()
            return self._revision

    def wait_for_change(
        self,
        after_revision: int,
        timeout: float = 20.0,
        coalesce_seconds: float = 0.05,
    ) -> int | None:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while self._revision <= after_revision and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            if self._closed:
                return -1
            # A command can update several bounded diagnostic records. Allow a
            # tiny quiet window and expose only the latest revision to clients.
            quiet = min(0.25, max(0.0, float(coalesce_seconds)))
            observed = self._revision
            while quiet and time.monotonic() < deadline:
                self._condition.wait(min(quiet, deadline - time.monotonic()))
                if self._closed:
                    return -1
                if self._revision == observed:
                    break
                observed = self._revision
            return self._revision

    def close(self) -> None:
        """Wake every waiter so process shutdown never waits for a heartbeat."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()


state_publisher = StatePublisher()
