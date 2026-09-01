"""Standalone music queue and playback controller."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from .application.search_cache import SearchCache
from .config import Settings
from .music import QQMusic
from .runtime import NameFacade, OopzRuntime, SenderFacade, VoiceFacade

logger = logging.getLogger(__name__)


class MusicQueue:
    def __init__(self):
        self._items: deque[dict] = deque()
        self._current: dict | None = None
        self._play_state: dict = {}
        self._version = 0
        self._lock = threading.RLock()

    def add_to_queue(self, item: dict) -> int:
        with self._lock:
            self._items.append(dict(item))
            self._version += 1
            return len(self._items)

    def get_queue(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self._items]

    def get_queue_length(self) -> int:
        with self._lock:
            return len(self._items)

    def peek_next(self) -> dict | None:
        with self._lock:
            return dict(self._items[0]) if self._items else None

    def play_next(self) -> dict | None:
        with self._lock:
            if not self._items:
                return None
            self._version += 1
            return dict(self._items.popleft())

    def remove_positions(
        self, positions: list[int], expected_version: int | None = None
    ) -> list[dict]:
        """Remove one-based pending queue positions atomically.

        The currently playing song is intentionally not part of the numbered
        pending queue and is never removed by this operation.
        """
        with self._lock:
            if expected_version is not None and expected_version != self._version:
                raise RuntimeError("queue version conflict")
            normalized = sorted(set(positions))
            if not normalized:
                return []
            if normalized[0] < 1 or normalized[-1] > len(self._items):
                raise IndexError("queue position out of range")

            items = list(self._items)
            selected = set(normalized)
            removed = [dict(items[index - 1]) for index in normalized]
            self._items = deque(
                item
                for index, item in enumerate(items, 1)
                if index not in selected
            )
            self._version += 1
            return removed

    def move_position(
        self, source: int, target: int, expected_version: int | None = None
    ) -> None:
        with self._lock:
            if expected_version is not None and expected_version != self._version:
                raise RuntimeError("queue version conflict")
            length = len(self._items)
            if source < 1 or target < 1 or source > length or target > length:
                raise IndexError("queue position out of range")
            if source == target:
                return
            items = list(self._items)
            item = items.pop(source - 1)
            items.insert(target - 1, item)
            self._items = deque(items)
            self._version += 1

    def get_version(self) -> int:
        with self._lock:
            return self._version

    def get_current(self) -> dict | None:
        with self._lock:
            return dict(self._current) if self._current else None

    def set_current(self, item: dict) -> None:
        with self._lock:
            self._current = dict(item)

    def clear_current(self) -> None:
        with self._lock:
            self._current = None

    def get_play_state(self) -> dict:
        with self._lock:
            return dict(self._play_state)

    def set_play_state(self, state: dict) -> None:
        with self._lock:
            self._play_state = dict(state)

    def clear_play_state(self) -> None:
        with self._lock:
            self._play_state = {}

    def clear(self, expected_version: int | None = None) -> None:
        with self._lock:
            if expected_version is not None and expected_version != self._version:
                raise RuntimeError("queue version conflict")
            changed = bool(self._items)
            self._items.clear()
            self._current = None
            self._play_state = {}
            if changed:
                self._version += 1


class MusicController:
    def __init__(self, settings: Settings, runtime: OopzRuntime):
        self.settings = settings
        self.runtime = runtime
        self.platforms = {"qq": QQMusic(settings)}
        self.search_cache = SearchCache(
            enabled=settings.search_cache_enabled,
            ttl_seconds=settings.search_cache_ttl_seconds,
            negative_ttl_seconds=settings.search_negative_cache_ttl_seconds,
            max_entries=settings.search_cache_max_entries,
        )
        self.sender = SenderFacade(runtime)
        self.names = NameFacade(runtime)
        self.voice = VoiceFacade(runtime)
        self.queue = MusicQueue()
        self._queues: dict[str, MusicQueue] = {settings.oopz_area_id: self.queue}
        self._queues_lock = threading.Lock()
        self._playback_lock = threading.RLock()
        self._voice_operation_lock = threading.Lock()
        self._voice_channel_id: str | None = None
        self._voice_channel_area: str | None = None
        self._play_start_time = 0.0
        self._play_duration = 0.0
        self._play_generation = 0
        self._closed = threading.Event()
        self._monitor = threading.Thread(target=self._monitor_playback, daemon=True)
        self._monitor.start()

    def _get_queue(self, area: str | None = None) -> MusicQueue:
        key = area or self.settings.oopz_area_id
        with self._queues_lock:
            return self._queues.setdefault(key, MusicQueue())

    def enter_voice_channel(self, channel: str, area: str) -> dict:
        try:
            if self._voice_channel_id and (
                self._voice_channel_id != channel or self._voice_channel_area != area
            ):
                self.runtime.leave_voice()
            self.runtime.join_voice(area, channel)
            self._voice_channel_id = channel
            self._voice_channel_area = area
            self.queue = self._get_queue(area)
            return {"ok": True}
        except Exception as exc:
            logger.exception("进入 OOPZ 语音频道失败")
            return {"error": str(exc)}

    def notify_message(self, *, text: str, channel: str, area: str, **kwargs) -> bool:
        """Send a best-effort OOPZ text notification.

        Playback state is committed before this notification is sent. A text
        channel outage must therefore not turn a successful voice playback
        command into an HTTP 500 response to the QQ bridge.
        """
        try:
            self.sender.send_message(
                text=text,
                channel=channel,
                area=area,
                **kwargs,
            )
        except Exception as exc:
            logger.warning("OOPZ 文字消息发送失败，继续播放: %s", exc)
            return False
        return True

    def search_candidates(self, keyword: str, platform: str = "qq", limit: int = 5) -> list[dict]:
        adapter = self.platforms.get(platform)
        if adapter is None:
            return []
        normalized_limit = max(1, min(int(limit), 10))
        load_failed = False

        def load() -> list[dict]:
            nonlocal load_failed
            value = adapter.search_many(keyword, limit=normalized_limit)
            load_failed = bool(getattr(adapter, "last_error", None))
            return value

        return self.search_cache.search(
            platform,
            keyword,
            limit=normalized_limit,
            loader=load,
            cacheable=lambda _value: not load_failed,
        )

    def _build_song_data_from_platform_data(
        self,
        song: dict,
        platform: str,
        song_id: str,
        channel: str,
        area: str,
        user: str,
    ) -> dict:
        duration_ms = int(song.get("duration_ms") or song.get("duration") or 0)
        return {
            "song_id": str(song_id),
            "platform": platform,
            "name": str(song.get("name") or "未知歌曲"),
            "artists": str(song.get("artists") or "未知歌手"),
            "album": str(song.get("album") or ""),
            "duration": str(song.get("durationText") or ""),
            "duration_ms": duration_ms,
            "cover": str(song.get("cover") or ""),
            "url": str(song.get("url") or ""),
            "channel": channel,
            "area": area,
            "user": user,
            "attachments": [],
        }

    def _resolve_playable(self, song: dict, platform_name: str, channel: str, area: str, user: str) -> dict:
        platform = self.platforms[platform_name]
        song_id = song.get("id") or song.get("mid") or song.get("song_id")
        if not song_id:
            raise RuntimeError("歌曲缺少 ID")
        url = platform.get_song_url(str(song_id))
        if not url:
            failure = getattr(platform, "last_error", None)
            message = (
                str(failure.get("message"))
                if isinstance(failure, dict) and failure.get("message")
                else "无法获取歌曲播放地址"
            )
            raise RuntimeError(message[:240])
        playable = dict(song, url=url)
        return self._build_song_data_from_platform_data(
            playable, platform_name, str(song_id), channel, area, user
        )

    def play_song(self, keyword: str, platform: str, channel: str, area: str, user: str) -> dict:
        results = self.search_candidates(keyword, platform, limit=1)
        if not results:
            failure = getattr(self.platforms.get(platform), "last_error", None)
            if isinstance(failure, dict) and failure.get("message"):
                return {"code": "error", "message": str(failure["message"])[:240]}
            return {"code": "error", "message": f"未找到歌曲：{keyword}"}
        return self.play_song_choice(results[0], channel, area, user)

    def play_song_choice(self, song: dict, channel: str, area: str, user: str) -> dict:
        platform = str(song.get("platform") or "qq")
        try:
            data = self._resolve_playable(song, platform, channel, area, user)
        except (KeyError, RuntimeError) as exc:
            message = str(exc) or "无法获取歌曲播放地址"
            self.notify_message(
                text=f"错误: {message}",
                channel=channel,
                area=area,
            )
            return {"code": "error", "message": message}
        result = self._commit_song_request(data, prefix="已点歌")
        self.notify_message(
            text=result["message"],
            channel=channel,
            area=area,
        )
        return result

    def _commit_song_request(self, song_data: dict, prefix: str = "已点歌") -> dict:
        queue = self._get_queue(song_data.get("area"))
        with self._playback_lock:
            if queue.get_current():
                position = queue.add_to_queue(song_data)
                message = (
                    f"{prefix}：{song_data['name']} - {song_data['artists']}\n"
                    f"已加入队列，第 {position} 首"
                )
            else:
                self._start_song(song_data, queue)
                message = f"{prefix}：{song_data['name']} - {song_data['artists']}"
        return {"code": "success", "message": message, "attachments": []}

    def _start_song(self, song: dict, queue: MusicQueue) -> None:
        if not song.get("url"):
            raise RuntimeError("歌曲没有播放地址")
        queue.set_current(song)
        self._play_generation += 1
        generation = self._play_generation
        self._play_start_time = 0.0
        self._play_duration = max(0.0, float(song.get("duration_ms") or 0) / 1000)
        queue.set_play_state(
            {
                "start_time": 0.0,
                "duration": self._play_duration,
                "loading": True,
            }
        )
        threading.Thread(
            target=self._start_song_audio,
            args=(song, queue, generation),
            name=f"oopz-play-{generation}",
            daemon=True,
        ).start()

    def _start_song_audio(
        self,
        song: dict,
        queue: MusicQueue,
        generation: int,
    ) -> None:
        try:
            with self._voice_operation_lock:
                self.voice.play_audio(song["url"])
        except Exception:
            logger.exception("OOPZ 音频启动失败: %s", song.get("name") or "未知歌曲")
            with self._playback_lock:
                current = queue.get_current() or {}
                if generation == self._play_generation and current.get("url") == song.get("url"):
                    queue.clear_current()
                    queue.clear_play_state()
            return

        with self._playback_lock:
            current = queue.get_current() or {}
            if generation != self._play_generation or current.get("url") != song.get("url"):
                return
            self._play_start_time = time.time()
            queue.set_play_state(
                {
                    "start_time": self._play_start_time,
                    "duration": self._play_duration,
                    "loading": False,
                }
            )

    def play_next(self, channel: str, area: str, user: str = "") -> dict:
        del user
        queue = self._get_queue(area)
        with self._playback_lock:
            self._play_generation += 1
            try:
                with self._voice_operation_lock:
                    self.voice.stop_audio()
            except Exception:
                logger.debug("停止当前音频失败", exc_info=True)
            queue.clear_current()
            queue.clear_play_state()
            next_song = queue.play_next()
            if not next_song:
                return {"code": "success", "message": "队列已空"}
            self._start_song(next_song, queue)
            self.notify_message(
                text=f"正在播放：{next_song['name']} - {next_song['artists']}",
                channel=channel,
                area=area,
            )
            return {"code": "success", "message": "已切换到下一首"}

    def stop_play(self, channel: str, area: str) -> dict:
        del channel
        with self._playback_lock:
            self._play_generation += 1
            try:
                with self._voice_operation_lock:
                    self.voice.stop_audio()
            finally:
                self._get_queue(area).clear()
                self._play_start_time = 0
                self._play_duration = 0
        return {"code": "success", "message": "已停止播放"}

    def _monitor_playback(self) -> None:
        while not self._closed.wait(3):
            area = self._voice_channel_area
            if not area:
                continue
            queue = self._get_queue(area)
            current = queue.get_current()
            state = queue.get_play_state()
            if not current or state.get("paused") or state.get("loading"):
                continue
            finished = False
            try:
                finished = self.voice.get_state() == "finished"
            except Exception:
                duration = float(state.get("duration") or 0)
                started = float(state.get("start_time") or 0)
                finished = bool(duration and started and time.time() - started >= duration + 5)
            if finished:
                try:
                    self.play_next(
                        current.get("channel") or self.settings.oopz_text_channel_id,
                        area,
                    )
                except Exception:
                    logger.exception("自动播放下一首失败")

    def _kickoff_cover_prefetch(self, _song: dict) -> None:
        return None

    def _preload_next_song_if_any(self) -> None:
        return None

    def close(self) -> None:
        self._closed.set()
        try:
            self.stop_play("", self.settings.oopz_area_id)
            self.runtime.leave_voice()
        except Exception:
            logger.debug("关闭音乐控制器失败", exc_info=True)
