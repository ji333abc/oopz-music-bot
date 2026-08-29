"""Queue business rules shared by commands and the control panel."""

from __future__ import annotations

from collections.abc import Sequence

from oopzbot.domain.contracts import PlaybackState, QueueItem, QueuePort, QueueSnapshot


class QueuePositionError(ValueError):
    """Raised before mutation when a pending position is invalid."""

    def __init__(self, length: int) -> None:
        self.length = max(0, int(length))
        super().__init__("queue position out of range")


class QueueService:
    """Own one-based pending-queue semantics independently of persistence."""

    def __init__(self, queue: QueuePort) -> None:
        self._queue = queue

    def snapshot(self) -> QueueSnapshot:
        return self._queue.get_snapshot()

    def enqueue(self, item: QueueItem) -> int:
        return self._queue.enqueue(item)

    def next_item(self) -> QueueItem | None:
        return self._queue.next_item()

    def clear_pending(self) -> None:
        self._queue.clear()

    def current(self) -> QueueItem | None:
        return self._queue.get_current()

    def playback(self) -> PlaybackState | None:
        return self._queue.get_playback_state()

    def set_playback(self, state: PlaybackState) -> None:
        self._queue.set_playback_state(state)

    def remove(self, positions: Sequence[int]) -> tuple[QueueItem, ...]:
        normalized = tuple(sorted(set(int(value) for value in positions)))
        snapshot = self.snapshot()
        if (
            not normalized
            or normalized[0] < 1
            or normalized[-1] > snapshot.queue_length
        ):
            raise QueuePositionError(snapshot.queue_length)
        try:
            return tuple(self._queue.remove_positions(normalized))
        except IndexError as exc:
            raise QueuePositionError(self.snapshot().queue_length) from exc
