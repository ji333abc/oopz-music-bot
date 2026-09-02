"""Bounded revision publisher used by the Panel SSE transport."""

from __future__ import annotations

import asyncio
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
        self._async_waiters: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()

    @staticmethod
    def _wake_async_waiters(
        waiters: tuple[tuple[asyncio.AbstractEventLoop, asyncio.Event], ...],
    ) -> None:
        for loop, event in waiters:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                # A disconnected client may close its loop before cleanup.
                continue

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
            revision = self._revision
            waiters = tuple(self._async_waiters)
        self._wake_async_waiters(waiters)
        return revision

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

    async def wait_for_change_async(
        self,
        after_revision: int,
        timeout: float = 20.0,
        coalesce_seconds: float = 0.05,
    ) -> int | None:
        """Wait without occupying the event loop's shared worker pool."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(timeout))

        async def wait_for_signal(
            waiter: tuple[asyncio.AbstractEventLoop, asyncio.Event],
            wait_timeout: float,
        ) -> bool:
            event = waiter[1]
            try:
                await asyncio.wait_for(event.wait(), timeout=wait_timeout)
                return True
            except TimeoutError:
                return False
            finally:
                with self._condition:
                    self._async_waiters.discard(waiter)

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            event = asyncio.Event()
            waiter = (loop, event)
            with self._condition:
                if self._closed:
                    return -1
                if self._revision > after_revision:
                    observed = self._revision
                    break
                self._async_waiters.add(waiter)
            if not await wait_for_signal(waiter, remaining):
                return None

        quiet = min(0.25, max(0.0, float(coalesce_seconds)))
        if not quiet:
            return observed
        quiet_deadline = min(deadline, loop.time() + quiet)
        while True:
            remaining = quiet_deadline - loop.time()
            if remaining <= 0:
                with self._condition:
                    return -1 if self._closed else self._revision
            event = asyncio.Event()
            waiter = (loop, event)
            with self._condition:
                if self._closed:
                    return -1
                current = self._revision
                if current != observed:
                    observed = current
                    quiet_deadline = min(deadline, loop.time() + quiet)
                    continue
                self._async_waiters.add(waiter)
            signaled = await wait_for_signal(waiter, remaining)
            with self._condition:
                current = self._revision
            if not signaled:
                return current

    def close(self) -> None:
        """Wake every waiter so process shutdown never waits for a heartbeat."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            waiters = tuple(self._async_waiters)
        self._wake_async_waiters(waiters)


state_publisher = StatePublisher()
