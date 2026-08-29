"""Lifecycle owner for the compatibility auto-play monitor."""

from __future__ import annotations

import threading
from typing import Protocol


class AutoPlayMonitorPort(Protocol):
    def auto_play_monitor(self, stop_event: threading.Event | None = None) -> None: ...


class PlaybackMonitorService:
    """Start and stop exactly one automatic-next monitor thread."""

    def __init__(self, backend: AutoPlayMonitorPort) -> None:
        self._backend = backend
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._backend.auto_play_monitor,
            args=(self._stop,),
            name="oopz-playback-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))
