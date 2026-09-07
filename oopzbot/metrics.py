"""Framework-independent P2 metric contracts and bounded aggregators."""

from __future__ import annotations

import math
import os
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Generic, TypeVar

DEFAULT_METRICS_WINDOW_SIZE = 200
DEFAULT_METRICS_SERIES_LIMIT = 64
METRIC_NAME_LIMIT = 80
TIMESTAMP_LIMIT = 64


def utc_now() -> str:
    """Return a stable UTC timestamp for display and persistence only."""

    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _duration(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) and result >= 0 else 0.0


@dataclass(frozen=True, slots=True)
class LatencySummary:
    count: int = 0
    success: int = 0
    failure: int = 0
    last_ms: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    success_rate: float | None = None
    result_counts: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExternalCallResult:
    service: str
    operation: str
    result_kind: str
    duration_ms: float
    ok: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class CommandTiming:
    command_id: str
    source: str
    kind: str
    ok: bool
    error_kind: str = ""
    duration_ms: float = 0.0
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class PlaybackHistoryItem:
    song_id: str
    name: str
    artists: str
    platform: str
    source: str
    result: str
    started_at: str
    ended_at: str = ""
    error_kind: str = ""


@dataclass(frozen=True, slots=True)
class FailureRecord:
    component: str
    error_kind: str
    message: str
    created_at: str
    command_id: str = ""


@dataclass(frozen=True, slots=True)
class PanelStateRevision:
    schema_version: int
    revision: int
    generated_at: str


class MonotonicTimer:
    """Measure elapsed time without depending on wall-clock changes."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started_at = float(clock())

    def elapsed_ms(self) -> float:
        return _duration((float(self._clock()) - self._started_at) * 1000.0)


T = TypeVar("T")


class BoundedWindow(Generic[T]):
    """Thread-safe fixed-capacity window that never grows past ``limit``."""

    def __init__(self, limit: int) -> None:
        normalized = int(limit)
        if normalized < 1:
            raise ValueError("window limit must be positive")
        self.limit = normalized
        self._items: deque[T] = deque(maxlen=normalized)
        self._lock = RLock()

    def append(self, item: T) -> None:
        with self._lock:
            self._items.append(item)

    def snapshot(self) -> tuple[T, ...]:
        with self._lock:
            return tuple(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def _nearest_rank(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    rank = max(1, math.ceil(float(percentile) * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


class LatencyWindow(BoundedWindow[ExternalCallResult]):
    def summary(self) -> LatencySummary:
        items = self.snapshot()
        if not items:
            return LatencySummary()
        durations = [_duration(item.duration_ms) for item in items]
        success = sum(1 for item in items if item.ok)
        result_counts: dict[str, int] = {}
        for item in items:
            result_counts[item.result_kind] = result_counts.get(item.result_kind, 0) + 1
        return LatencySummary(
            count=len(items),
            success=success,
            failure=len(items) - success,
            last_ms=durations[-1],
            p50_ms=_nearest_rank(durations, 0.50),
            p95_ms=_nearest_rank(durations, 0.95),
            success_rate=success / len(items),
            result_counts=result_counts,
        )


class MetricsRegistry:
    """Bound endpoint cardinality and per-endpoint latency samples."""

    def __init__(
        self,
        *,
        window_size: int = DEFAULT_METRICS_WINDOW_SIZE,
        series_limit: int = DEFAULT_METRICS_SERIES_LIMIT,
        monotonic_clock: Callable[[], float] = time.monotonic,
        timestamp_clock: Callable[[], str] = utc_now,
    ) -> None:
        if int(window_size) < 1:
            raise ValueError("metrics window size must be positive")
        if int(series_limit) < 1:
            raise ValueError("metrics series limit must be positive")
        self.window_size = int(window_size)
        self.series_limit = int(series_limit)
        self._monotonic_clock = monotonic_clock
        self._timestamp_clock = timestamp_clock
        self._series: OrderedDict[tuple[str, str], LatencyWindow] = OrderedDict()
        self._lock = RLock()
        self.evictions = 0

    def timer(self) -> MonotonicTimer:
        return MonotonicTimer(self._monotonic_clock)

    def record_external(
        self,
        *,
        service: str,
        operation: str,
        result_kind: str,
        ok: bool,
        duration_ms: float,
        created_at: str | None = None,
    ) -> ExternalCallResult:
        result = ExternalCallResult(
            service=str(service)[:METRIC_NAME_LIMIT],
            operation=str(operation)[:METRIC_NAME_LIMIT],
            result_kind=str(result_kind)[:METRIC_NAME_LIMIT],
            duration_ms=_duration(duration_ms),
            ok=bool(ok),
            created_at=str(created_at or self._timestamp_clock())[:TIMESTAMP_LIMIT],
        )
        key = (result.service, result.operation)
        with self._lock:
            window = self._series.pop(key, None)
            if window is None:
                if len(self._series) >= self.series_limit:
                    self._series.popitem(last=False)
                    self.evictions += 1
                window = LatencyWindow(self.window_size)
            window.append(result)
            self._series[key] = window
        return result

    def summaries(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            items = tuple(self._series.items())
        return {
            f"{service}:{operation}": window.summary().to_dict()
            for (service, operation), window in items
        }

    @property
    def series_count(self) -> int:
        with self._lock:
            return len(self._series)


def _environment_window_size() -> int:
    try:
        value = int(os.getenv("OOPZ_METRICS_WINDOW_SIZE", "200"))
    except ValueError:
        return DEFAULT_METRICS_WINDOW_SIZE
    return value if 10 <= value <= 2000 else DEFAULT_METRICS_WINDOW_SIZE


metrics = MetricsRegistry(window_size=_environment_window_size())
