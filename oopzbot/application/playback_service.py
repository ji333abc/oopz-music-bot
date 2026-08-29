"""Single command-facing playback orchestration boundary."""

from __future__ import annotations

from typing import Any, Protocol

from oopzbot.domain.contracts import CommandError, ErrorKind, OperationResult


class PlaybackBackendPort(Protocol):
    """Compatibility capability supplied by the current music implementation."""

    def play_song(
        self,
        keyword: str,
        platform: str,
        channel: str,
        area: str,
        user: str,
    ) -> dict: ...

    def play_song_choice(
        self,
        song: dict,
        channel: str,
        area: str,
        user: str,
    ) -> dict: ...

    def play_next(self, channel: str, area: str, user: str) -> Any: ...

    def stop_play(self, channel: str, area: str) -> Any: ...


class PlaybackService:
    """Own command playback outcomes while delegating stable media mechanics.

    The embedded implementation still performs the proven OOPZ/Agora calls,
    but transports no longer interpret its dictionary or exceptions.
    """

    def __init__(self, backend: PlaybackBackendPort) -> None:
        self._backend = backend

    def play_keyword(
        self,
        keyword: str,
        *,
        platform: str,
        channel: str,
        area: str,
        requester_id: str,
    ) -> OperationResult:
        try:
            raw = self._backend.play_song(
                keyword,
                platform,
                channel,
                area,
                requester_id,
            )
        except Exception as exc:
            return self._failure(str(exc) or "歌曲播放失败", stage="playing")
        return self._from_legacy(raw, f"已提交点歌：{keyword}")

    def play_choice(
        self,
        song: dict,
        *,
        channel: str,
        area: str,
        requester_id: str,
    ) -> OperationResult:
        try:
            raw = self._backend.play_song_choice(song, channel, area, requester_id)
        except Exception as exc:
            return self._failure(str(exc) or "歌曲播放失败", stage="playing")
        return self._from_legacy(raw, "歌曲未能开始播放或加入队列")

    def next(self, *, channel: str, area: str, requester_id: str) -> OperationResult:
        try:
            self._backend.play_next(channel, area, requester_id)
        except Exception as exc:
            return self._failure(str(exc) or "切歌失败", stage="switching")
        return OperationResult(ok=True, message="已执行切歌")

    def stop(self, *, channel: str, area: str) -> OperationResult:
        try:
            self._backend.stop_play(channel, area)
        except Exception as exc:
            return self._failure(str(exc) or "停止播放失败", stage="stopping")
        return OperationResult(ok=True, message="已停止播放")

    @classmethod
    def _from_legacy(cls, raw: Any, default: str) -> OperationResult:
        if not isinstance(raw, dict):
            return cls._failure(default, stage="playing")
        failed = raw.get("ok") is False or str(raw.get("code") or "").lower() in {
            "error",
            "failed",
            "failure",
        }
        message = str(raw.get("message") or raw.get("error") or default)
        if failed or raw.get("error"):
            return cls._failure(message, stage="playing")
        return OperationResult(ok=True, message=message)

    @staticmethod
    def _failure(message: str, *, stage: str) -> OperationResult:
        error = CommandError(ErrorKind.PLAYBACK, message, stage)
        return OperationResult(ok=False, message=message, error=error)
