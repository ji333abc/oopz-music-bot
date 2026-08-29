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

    def play_liked(self, channel: str, area: str, user: str, count: int) -> Any: ...

    def play_liked_by_index(
        self,
        index: int,
        channel: str,
        area: str,
        user: str,
    ) -> Any: ...

    def show_liked_list(self, channel: str, area: str, page: int) -> Any: ...


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

    def play_liked(
        self,
        *,
        channel: str,
        area: str,
        requester_id: str,
        count: int = 1,
    ) -> OperationResult:
        try:
            self._backend.play_liked(channel, area, requester_id, count)
        except Exception as exc:
            return self._failure(str(exc) or "随机播放喜欢歌曲失败", stage="playing")
        return OperationResult(ok=True, message="已执行随机播放")

    def play_liked_by_index(
        self,
        index: int,
        *,
        channel: str,
        area: str,
        requester_id: str,
    ) -> OperationResult:
        try:
            self._backend.play_liked_by_index(index, channel, area, requester_id)
        except Exception as exc:
            return self._failure(str(exc) or "播放喜欢歌曲失败", stage="playing")
        return OperationResult(ok=True, message="已执行喜欢歌曲点播")

    def show_liked(self, page: int, *, channel: str, area: str) -> OperationResult:
        try:
            self._backend.show_liked_list(channel, area, page)
        except Exception as exc:
            return self._failure(str(exc) or "读取喜欢列表失败", stage="resolving")
        return OperationResult(ok=True, message="已显示喜欢列表")

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
