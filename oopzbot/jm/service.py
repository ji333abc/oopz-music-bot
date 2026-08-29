"""JM task concurrency ownership independent from QQ events."""

from __future__ import annotations

import asyncio
from threading import Lock


class JMTaskCoordinator:
    """Own the one-job gate and retain background tasks until completion."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.tasks: set[asyncio.Task] = set()

    def acquire(self) -> bool:
        return self.lock.acquire(blocking=False)

    def release(self) -> None:
        if self.lock.locked():
            self.lock.release()

    def track(self, task: asyncio.Task) -> asyncio.Task:
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task
