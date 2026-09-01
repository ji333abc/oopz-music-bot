"""Explicit conversion functions at the legacy dictionary boundary.

These helpers are intentionally the only place in the new domain package that
knows the current flat song, queue, and bridge response shapes.  Business
services should exchange the dataclasses from :mod:`oopzbot.domain.contracts`
instead of guessing fields with scattered ``dict.get`` calls.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .contracts import (
    CommandError,
    CommandRequest,
    CommandResult,
    ErrorKind,
    PlaybackPhase,
    PlaybackState,
    QueueItem,
    QueueSnapshot,
    SongCandidate,
)


def _require_mapping(value: Mapping[str, Any] | Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    return value


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _first_text(data: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value) != "":
            return str(value)
    return default


def _integer(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_duration_ms(value: int) -> str:
    total_seconds = max(0, int(round(value / 1000)))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _duration_ms(data: Mapping[str, Any]) -> int:
    raw_duration_ms = data.get("duration_ms")
    duration_ms = _integer(raw_duration_ms)
    if duration_ms:
        return duration_ms
    raw_duration = data.get("duration")
    if isinstance(raw_duration, (int, float)):
        return _integer(raw_duration)
    if isinstance(raw_duration, str) and re.fullmatch(r"\d+", raw_duration.strip()):
        return _integer(raw_duration)
    return 0


def _duration_text(data: Mapping[str, Any], duration_ms: int) -> str:
    explicit = _first_text(data, "durationText")
    if explicit:
        return explicit
    raw_duration = data.get("duration")
    if isinstance(raw_duration, str):
        stripped = raw_duration.strip()
        if stripped and not re.fullmatch(r"\d+", stripped):
            return stripped
    return _format_duration_ms(duration_ms) if duration_ms else ""


def _song_candidate_from_legacy(
    value: Mapping[str, Any],
    *,
    default_platform: str = "qq",
    preserve_display_fields: bool = False,
) -> SongCandidate:
    """Read common song fields without retaining persistence-only metadata."""

    data = _require_mapping(value, "song")
    duration_ms = _duration_ms(data)
    extras: dict[str, Any] = {}
    if preserve_display_fields:
        known = {
            "song_id",
            "id",
            "mid",
            "name",
            "artists",
            "singerName",
            "album",
            "duration_ms",
            "duration",
            "durationText",
            "cover",
            "platform",
            "url",
            "index",
        }
        extras = {
            key: deepcopy(item)
            for key, item in data.items()
            if key not in known
        }
        # Ranking responses use ``title`` as the song name.  Preserve the
        # original alias because the existing QQ ranking renderer consumes it.
        if "title" in data:
            extras["title"] = deepcopy(data["title"])
    return SongCandidate(
        song_id=_first_text(data, "song_id", "id", "mid"),
        name=_first_text(data, "name", "title", default="未知歌曲"),
        artists=_first_text(data, "artists", "singerName", default="未知歌手"),
        album=_first_text(data, "album"),
        duration_ms=duration_ms,
        duration_text=_duration_text(data, duration_ms),
        cover=_first_text(data, "cover"),
        platform=_first_text(data, "platform", default=default_platform),
        url=_first_text(data, "url"),
        extras=extras,
    )


def display_song_from_legacy(
    value: Mapping[str, Any], *, default_platform: str = "qq"
) -> SongCandidate:
    """Convert a legacy song into the QQ/Panel display DTO."""

    return _song_candidate_from_legacy(
        value,
        default_platform=default_platform,
        preserve_display_fields=True,
    )


def song_candidate_from_legacy(
    value: Mapping[str, Any], *, default_platform: str = "qq"
) -> SongCandidate:
    """Backward-compatible name for the display-song conversion."""

    return display_song_from_legacy(value, default_platform=default_platform)


def display_song_to_legacy(value: SongCandidate, *, index: int | None = None) -> dict[str, Any]:
    """Serialize only fields safe for QQ/Panel display responses."""

    duration = value.duration_text or (
        _format_duration_ms(value.duration_ms) if value.duration_ms else ""
    )
    payload: dict[str, Any] = deepcopy(dict(value.extras))
    payload.update({
        "id": value.song_id,
        "name": value.name,
        "artists": value.artists,
        "album": value.album,
        "duration": duration,
        "durationText": duration,
        "cover": value.cover,
        "platform": value.platform,
    })
    if index is not None:
        payload["index"] = index
    return payload


def song_candidate_to_legacy(value: SongCandidate, *, index: int | None = None) -> dict[str, Any]:
    """Backward-compatible name for the display-song serialization."""

    return display_song_to_legacy(value, index=index)


_QUEUE_RECORD_FIELDS = {
    "song_id",
    "id",
    "mid",
    "name",
    "title",
    "artists",
    "singerName",
    "album",
    "duration_ms",
    "duration",
    "durationText",
    "cover",
    "platform",
    "url",
    "channel",
    "text_channel",
    "area",
    "user",
    "requester_id",
    "requester",
    "position",
    "index",
    "attachments",
    "play_uuid",
}


def _attachments_from_legacy(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(deepcopy(item) for item in value)
    return (deepcopy(value),)


def queue_item_from_legacy(value: Mapping[str, Any]) -> QueueItem:
    """Convert a persistent queue record, retaining non-display fields."""

    data = _require_mapping(value, "queue item")
    extras = {
        key: deepcopy(item)
        for key, item in data.items()
        if key not in _QUEUE_RECORD_FIELDS
    }
    return QueueItem(
        song=_song_candidate_from_legacy(data),
        channel=_first_text(data, "channel", "text_channel"),
        area=_first_text(data, "area"),
        requester_id=_first_text(data, "user", "requester_id", "requester"),
        position=_integer(data.get("position"), 0) or None,
        attachments=_attachments_from_legacy(data.get("attachments")),
        play_uuid=_first_text(data, "play_uuid") or None,
        extras=extras,
    )


def queue_item_to_legacy(value: QueueItem) -> dict[str, Any]:
    """Serialize a queue record; this is intentionally not a display payload."""

    payload = deepcopy(dict(value.extras))
    duration = value.song.duration_text or (
        _format_duration_ms(value.song.duration_ms) if value.song.duration_ms else ""
    )
    payload.update(
        {
            "id": value.song.song_id,
            "name": value.song.name,
            "artists": value.song.artists,
            "album": value.song.album,
            "duration": duration,
            "durationText": duration,
            "cover": value.song.cover,
            "platform": value.song.platform,
            "song_id": value.song.song_id,
            "duration_ms": value.song.duration_ms,
            "url": value.song.url,
            "channel": value.channel,
            "area": value.area,
            "user": value.requester_id,
            "attachments": deepcopy(list(value.attachments)),
        }
    )
    if value.play_uuid is not None:
        payload["play_uuid"] = value.play_uuid
    return payload


def queue_item_to_display(value: QueueItem) -> dict[str, Any]:
    """Serialize a queue item for QQ/Panel without persistence metadata."""

    return display_song_to_legacy(value.song, index=value.position)


def playback_state_from_legacy(
    value: Mapping[str, Any] | None,
    *,
    current_song_id: str | None = None,
) -> PlaybackState | None:
    if value is None:
        return None
    data = _require_mapping(value, "play state")
    paused = bool(data.get("paused"))
    loading = bool(data.get("loading"))
    if loading:
        phase = PlaybackPhase.RESOLVING
    elif current_song_id:
        phase = PlaybackPhase.PLAYING
    else:
        phase = PlaybackPhase.IDLE
    return PlaybackState(
        phase=phase,
        current_song_id=current_song_id,
        paused=paused,
        loading=loading,
        progress_seconds=_number(data.get("progress_seconds", data.get("progress"))),
        duration_seconds=_number(data.get("duration")),
        start_time=_number(data.get("start_time")),
        pause_elapsed=(
            _number(data.get("pause_elapsed"))
            if data.get("pause_elapsed") is not None
            else None
        ),
    )


def playback_state_to_legacy(value: PlaybackState) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "start_time": value.start_time,
        "duration": value.duration_seconds,
        "progress": value.progress_seconds,
        "paused": value.paused,
        "loading": value.loading,
    }
    if value.pause_elapsed is not None:
        payload["pause_elapsed"] = value.pause_elapsed
    return payload


def queue_snapshot_from_legacy(
    current: Mapping[str, Any] | None,
    pending: Sequence[Mapping[str, Any]],
    play_state: Mapping[str, Any] | None = None,
    *,
    degraded: bool = False,
    version: int = 0,
) -> QueueSnapshot:
    current_item = queue_item_from_legacy(current) if current is not None else None
    pending_items = tuple(
        queue_item_from_legacy(dict(item, position=index))
        for index, item in enumerate(pending, 1)
    )
    playback = playback_state_from_legacy(
        play_state,
        current_song_id=current_item.song.song_id if current_item else None,
    )
    return QueueSnapshot(
        current=current_item,
        pending=pending_items,
        playback=playback,
        degraded=degraded,
        version=max(0, int(version)),
    )


def command_request_from_legacy(value: Mapping[str, Any]) -> CommandRequest:
    data = _require_mapping(value, "command request")
    return CommandRequest(
        command=_text(data.get("command")).strip(),
        requester_id=_first_text(data, "requester_id", default="anonymous"),
        requester_name=_first_text(data, "requester_name"),
        group_openid=_first_text(data, "group_openid"),
        source=_first_text(data, "source", default="unknown"),
        command_id=_first_text(data, "command_id") or None,
        area_id=_first_text(data, "area_id"),
        text_channel_id=_first_text(data, "text_channel_id"),
        voice_channel_id=_first_text(data, "voice_channel_id"),
        bot_user_id=_first_text(data, "bot_user_id"),
        expected_version=(
            int(data["expected_version"])
            if data.get("expected_version") is not None
            else None
        ),
    )


def command_request_to_legacy(value: CommandRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "command": value.command,
        "requester_id": value.requester_id,
        "requester_name": value.requester_name,
        "group_openid": value.group_openid,
        "source": value.source,
        "command_id": value.command_id or "",
        "area_id": value.area_id,
        "text_channel_id": value.text_channel_id,
        "voice_channel_id": value.voice_channel_id,
        "bot_user_id": value.bot_user_id,
    }
    if value.expected_version is not None:
        payload["expected_version"] = value.expected_version
    return payload


def command_result_from_legacy(
    value: Mapping[str, Any], *, command_id: str | None = None
) -> CommandResult:
    """Convert a bridge dictionary without classifying errors from prose."""

    data = _require_mapping(value, "command result")
    ok = bool(data.get("ok"))
    message = _first_text(data, "message", "error")
    reply_type = _first_text(data, "reply_type") or None
    result_command_id = _first_text(data, "command_id") or command_id
    raw_error_kind = _first_text(data, "error_kind", "error_type")
    error = None
    if not ok:
        try:
            kind = ErrorKind(raw_error_kind) if raw_error_kind else ErrorKind.UNKNOWN
        except ValueError:
            kind = ErrorKind.UNKNOWN
        error = CommandError(
            kind=kind,
            message=message,
            stage=_first_text(data, "error_stage", "stage"),
        )

    raw_songs = data.get("songs") or ()
    songs = tuple(
        song_candidate_from_legacy(song)
        for song in raw_songs
        if isinstance(song, Mapping)
    )
    raw_song = data.get("song")
    song = song_candidate_from_legacy(raw_song) if isinstance(raw_song, Mapping) else None

    queue = None
    if any(key in data for key in ("queue_items", "queue_all", "queue_length")):
        pending = data.get("queue_all") or data.get("queue_items") or ()
        current = data.get("current")
        queue = queue_snapshot_from_legacy(
            current if isinstance(current, Mapping) else None,
            [item for item in pending if isinstance(item, Mapping)],
            version=_integer(data.get("queue_version")),
        )

    playback = None
    if any(key in data for key in ("playing", "paused", "loading", "progress", "duration")):
        current = data.get("current")
        current_id = (
            song_candidate_from_legacy(current).song_id
            if isinstance(current, Mapping)
            else None
        )
        playback = playback_state_from_legacy(
            data,
            current_song_id=current_id,
        )

    known = {
        "ok",
        "message",
        "error",
        "reply_type",
        "command_id",
        "code",
        "error_kind",
        "error_type",
        "error_stage",
        "stage",
        "songs",
        "song",
        "queue_items",
        "queue_all",
        "queue_length",
        "queue_version",
        "current",
        "playing",
        "paused",
        "loading",
        "progress",
        "duration",
    }
    extras = {key: item for key, item in data.items() if key not in known}
    return CommandResult(
        ok=ok,
        message=message,
        reply_type=reply_type,
        command_id=result_command_id,
        code=_first_text(data, "code") or None,
        error=error,
        songs=songs,
        song=song,
        queue=queue,
        playback=playback,
        extras=extras,
    )


def command_result_to_legacy(value: CommandResult) -> dict[str, Any]:
    """Serialize a domain result to the current bridge response envelope."""

    payload: dict[str, Any] = dict(value.extras)
    payload.update({"ok": value.ok, "message": value.message})
    if value.reply_type:
        payload["reply_type"] = value.reply_type
    if value.command_id:
        payload["command_id"] = value.command_id
    if value.code:
        payload["code"] = value.code
    if value.error is not None:
        payload.update(
            {
                "error_kind": value.error.kind.value,
                "error_stage": value.error.stage,
            }
        )
    if value.songs:
        payload["songs"] = [
            song_candidate_to_legacy(song, index=index)
            for index, song in enumerate(value.songs, 1)
        ]
    if value.song is not None:
        payload["song"] = song_candidate_to_legacy(value.song)
    if value.queue is not None:
        payload["current"] = (
            queue_item_to_display(value.queue.current) if value.queue.current else None
        )
        payload["queue_items"] = [
            queue_item_to_display(item) for item in value.queue.pending[:10]
        ]
        payload["queue_all"] = [
            queue_item_to_display(item) for item in value.queue.pending
        ]
        payload["queue_length"] = value.queue.queue_length
        payload["queue_version"] = value.queue.version
    if value.playback is not None:
        payload.update(
            {
                "playing": value.playback.current_song_id is not None,
                "paused": value.playback.paused,
                "loading": value.playback.loading,
                "duration": value.playback.duration_seconds,
                "progress": value.playback.progress_seconds,
            }
        )
    return payload
