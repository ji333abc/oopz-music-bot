"""Small, dependency-free contracts for the P1 application boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class ComponentStatus(StrEnum):
    """Finite status values exposed by health and Panel adapters."""

    STARTING = "starting"
    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class ErrorKind(StrEnum):
    """Stable business error categories; messages remain user-facing text."""

    INVALID_INPUT = "invalid_input"
    NOT_INITIALIZED = "not_initialized"
    NOT_FOUND = "not_found"
    EXPIRED = "expired"
    DEPENDENCY = "dependency"
    PLAYBACK = "playback"
    OPERATION = "operation"
    UNKNOWN = "unknown"


class PlaybackPhase(StrEnum):
    IDLE = "idle"
    RESOLVING = "resolving"
    JOINING = "joining"
    PLAYING = "playing"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class CommandRequest:
    """Transport-neutral representation of one command invocation."""

    command: str
    requester_id: str = "anonymous"
    requester_name: str = ""
    group_openid: str = ""
    source: str = "unknown"
    command_id: str | None = None
    area_id: str = ""
    text_channel_id: str = ""
    voice_channel_id: str = ""
    bot_user_id: str = ""
    expected_version: int | None = None

    @property
    def requester_key(self) -> str:
        """Stable session key shared by QQ and Panel command transports."""

        if self.group_openid:
            return f"{self.group_openid}:{self.requester_id}"
        return self.requester_id


@dataclass(frozen=True, slots=True)
class SongCandidate:
    """The cross-module song fields needed for search, display, and playback."""

    song_id: str
    name: str = "未知歌曲"
    artists: str = "未知歌手"
    album: str = ""
    duration_ms: int = 0
    duration_text: str = ""
    cover: str = ""
    platform: str = "qq"
    url: str = ""
    extras: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QueueItem:
    """A persisted song request; ``position`` is one-based within pending items."""

    song: SongCandidate
    channel: str = ""
    area: str = ""
    requester_id: str = ""
    position: int | None = None
    attachments: tuple[Any, ...] = ()
    play_uuid: str | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlaybackState:
    """A serializable playback snapshot, including the current state phase."""

    phase: PlaybackPhase = PlaybackPhase.IDLE
    current_song_id: str | None = None
    paused: bool = False
    loading: bool = False
    progress_seconds: float = 0.0
    duration_seconds: float = 0.0
    start_time: float = 0.0
    pause_elapsed: float | None = None
    error_kind: ErrorKind | None = None


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """The complete queue view required by commands and the Panel."""

    current: QueueItem | None = None
    pending: tuple[QueueItem, ...] = ()
    playback: PlaybackState | None = None
    degraded: bool = False
    version: int = 0

    @property
    def queue_length(self) -> int:
        return len(self.pending)


@dataclass(frozen=True, slots=True)
class ComponentState:
    """Bounded component status shared by readiness and Panel responses."""

    name: str
    status: ComponentStatus
    reason: str = ""
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class CommandError:
    """Structured command failure; adapters must not classify by message text."""

    kind: ErrorKind
    message: str
    stage: str = ""


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Small result used by runtime and infrastructure ports."""

    ok: bool
    message: str = ""
    error: CommandError | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Transport-neutral command result with an explicit compatibility payload."""

    ok: bool
    message: str = ""
    reply_type: str | None = None
    command_id: str | None = None
    code: str | None = None
    error: CommandError | None = None
    songs: tuple[SongCandidate, ...] = ()
    song: SongCandidate | None = None
    queue: QueueSnapshot | None = None
    playback: PlaybackState | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class MusicProviderPort(Protocol):
    """Search and resolve playback URLs without exposing an upstream client."""

    def search(self, keyword: str, limit: int = 10) -> Sequence[SongCandidate]: ...

    def get_play_url(self, song_id: str) -> str | None: ...


@runtime_checkable
class QueuePort(Protocol):
    """Minimum queue ownership contract for the application service.

    The currently playing item is never part of pending positions.  All
    position arguments and return values are one-based.  Invalid multi-remove
    requests must fail before mutating any item.
    """

    def get_snapshot(self) -> QueueSnapshot: ...

    def enqueue(self, item: QueueItem) -> int:
        """Append and return the one-based pending position."""
        ...

    def enqueue_many(
        self,
        items: Sequence[QueueItem],
        expected_version: int | None = None,
    ) -> int:
        """Atomically append a batch and return the final pending length."""
        ...

    def next_item(self) -> QueueItem | None: ...

    def clear(self, expected_version: int | None = None) -> None:
        """Clear pending items only; current/playback have explicit methods."""
        ...

    def remove_positions(
        self, positions: Sequence[int], expected_version: int | None = None
    ) -> Sequence[QueueItem]:
        """Atomically remove unique one-based pending positions."""
        ...

    def move_position(
        self, source: int, target: int, expected_version: int | None = None
    ) -> None:
        """Atomically move one pending item to its final one-based position."""
        ...

    def get_current(self) -> QueueItem | None: ...

    def set_current(self, item: QueueItem) -> None: ...

    def clear_current(self) -> None: ...

    def get_playback_state(self) -> PlaybackState | None: ...

    def set_playback_state(self, state: PlaybackState) -> None: ...

    def clear_playback_state(self) -> None: ...


@runtime_checkable
class OopzRuntimePort(Protocol):
    """OOPZ and voice capability boundary; implementation details stay hidden."""

    def start(self) -> OperationResult: ...

    def close(self) -> None: ...

    def status(self) -> ComponentState: ...

    def send_text(self, text: str, *, channel: str, area: str) -> OperationResult: ...

    def enter_voice(self, *, area: str, channel: str) -> OperationResult: ...

    def play(self, url: str, *, area: str, channel: str) -> OperationResult: ...

    def pause(self) -> OperationResult: ...

    def resume(self) -> OperationResult: ...

    def stop(self) -> OperationResult: ...

    def current_state(self) -> PlaybackState: ...
