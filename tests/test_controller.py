from __future__ import annotations

import threading
import time
import unittest

from oopzbot.config import Settings
from oopzbot.controller import MusicController, MusicQueue
from oopzbot.infrastructure.queue_adapter import LegacyQueueAdapter


class _FakeRuntime:
    ready = True
    bot = None

    def __init__(self) -> None:
        self.played: list[str] = []
        self.messages: list[tuple[str, str, str]] = []
        self.fail_messages = False
        self.play_gate: threading.Event | None = None

    def join_voice(self, area: str, channel: str) -> None:
        self.joined = (area, channel)

    def leave_voice(self) -> None:
        pass

    def call(self, operation, timeout=None):
        del timeout

        class Voice:
            async def play_url(inner_self, url):
                if self.play_gate is not None:
                    self.play_gate.wait(1)
                self.played.append(url)
                return {"ok": True}

            async def stop(inner_self):
                return None

            async def get_state(inner_self):
                return "playing"

        class Bot:
            voice = Voice()

        import asyncio

        return asyncio.run(operation(Bot()))

    def send_message(self, text: str, area: str, channel: str):
        if self.fail_messages:
            raise RuntimeError("text channel unavailable")
        self.messages.append((text, area, channel))

    def user_name(self, uid: str) -> str:
        return uid


class _FakeMusic:
    def __init__(self) -> None:
        self.search_calls = 0

    def search_many(self, keyword: str, limit: int = 5):
        self.search_calls += 1
        return [
            {
                "id": keyword,
                "name": keyword,
                "artists": "artist",
                "duration": 120_000,
                "durationText": "2:00",
            }
        ][:limit]

    def get_song_url(self, song_id: str):
        return f"https://audio.invalid/{song_id}.mp3"


class _UnplayableMusic(_FakeMusic):
    def get_song_url(self, song_id: str):
        del song_id
        return None


class _FailedSearchMusic(_FakeMusic):
    last_error = {"type": "timeout"}

    def search_many(self, keyword: str, limit: int = 5):
        del keyword, limit
        self.search_calls += 1
        return []


def _settings() -> Settings:
    return Settings(
        qqbot_app_id="app",
        qqbot_app_secret="secret",
        bridge_token="bridge",
        bridge_host="127.0.0.1",
        bridge_port=18080,
        oopz_area_id="area",
        oopz_text_channel_id="text",
        oopz_voice_channel_id="voice",
        oopz_person_uid="bot",
        qq_music_enabled=True,
        qq_music_managed=False,
        qq_music_base_url="http://music.invalid",
        qq_music_service_dir=".services/qqmusic-api",
        qq_music_cookie="",
        qq_music_quality="320",
        qq_music_fallback_quality="128",
        log_level="INFO",
    )


class MusicQueueTests(unittest.TestCase):
    def test_queue_returns_copies(self) -> None:
        queue = MusicQueue()
        song = {"name": "one"}
        queue.add_to_queue(song)
        song["name"] = "changed"
        self.assertEqual(queue.peek_next()["name"], "one")

    def test_batch_append_is_atomic_and_advances_version_once(self) -> None:
        queue = MusicQueue()
        version = queue.get_version()

        length = queue.add_many_to_queue(
            [{"name": "one"}, {"name": "two"}],
            expected_version=version,
        )

        self.assertEqual(length, 2)
        self.assertEqual(queue.get_version(), version + 1)
        with self.assertRaisesRegex(RuntimeError, "version conflict"):
            queue.add_many_to_queue([{"name": "three"}], expected_version=version)
        self.assertEqual([item["name"] for item in queue.get_queue()], ["one", "two"])

    def test_adapter_uses_atomic_queue_snapshot(self) -> None:
        queue = MusicQueue()
        queue.add_to_queue({"name": "one", "song_id": "1"})
        version = queue.get_version()
        adapter = LegacyQueueAdapter(queue)

        # Sequential reads would allow a mutation between list and version.
        # Built-in queues must instead expose the one-lock snapshot path.
        original_get_queue = queue.get_queue
        original_get_version = queue.get_version
        queue.get_queue = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("sequential queue read used")
        )
        queue.get_version = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            AssertionError("sequential version read used")
        )
        try:
            snapshot = adapter.get_snapshot()
        finally:
            queue.get_queue = original_get_queue  # type: ignore[method-assign]
            queue.get_version = original_get_version  # type: ignore[method-assign]

        self.assertEqual(snapshot.version, version)
        self.assertEqual([item.song.name for item in snapshot.pending], ["one"])

    def test_remove_positions_uses_one_based_pending_indexes(self) -> None:
        queue = MusicQueue()
        for name in ("one", "two", "three", "four"):
            queue.add_to_queue({"name": name})

        removed = queue.remove_positions([4, 2, 2])

        self.assertEqual([song["name"] for song in removed], ["two", "four"])
        self.assertEqual(
            [song["name"] for song in queue.get_queue()],
            ["one", "three"],
        )

    def test_remove_positions_is_atomic_when_an_index_is_invalid(self) -> None:
        queue = MusicQueue()
        queue.add_to_queue({"name": "one"})

        with self.assertRaises(IndexError):
            queue.remove_positions([1, 2])

        self.assertEqual([song["name"] for song in queue.get_queue()], ["one"])

    def test_move_position_updates_version_and_preserves_duplicates(self) -> None:
        queue = MusicQueue()
        for name in ("same", "two", "same"):
            queue.add_to_queue({"name": name})
        version = queue.get_version()

        queue.move_position(1, 3, version)

        self.assertEqual([song["name"] for song in queue.get_queue()], ["two", "same", "same"])
        self.assertEqual(queue.get_version(), version + 1)
        with self.assertRaisesRegex(RuntimeError, "version conflict"):
            queue.move_position(1, 2, version)


class MusicControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = _FakeRuntime()
        self.controller = MusicController(_settings(), self.runtime)
        self.controller.platforms["qq"] = _FakeMusic()
        self.controller.enter_voice_channel("voice", "area")

    def tearDown(self) -> None:
        self.controller._closed.set()

    def _wait_for_play_count(self, count: int) -> None:
        deadline = time.monotonic() + 1
        while len(self.runtime.played) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreaterEqual(len(self.runtime.played), count)

    def test_first_song_plays_and_second_song_queues(self) -> None:
        first = self.controller.play_song("first", "qq", "text", "area", "user")
        second = self.controller.play_song("second", "qq", "text", "area", "user")
        self._wait_for_play_count(1)

        self.assertEqual(first["code"], "success")
        self.assertEqual(second["code"], "success")
        self.assertEqual(self.runtime.played, ["https://audio.invalid/first.mp3"])
        self.assertEqual(self.controller._get_queue("area").get_queue_length(), 1)

    def test_search_cache_is_shared_and_does_not_cache_play_urls(self) -> None:
        music = self.controller.platforms["qq"]
        first = self.controller.search_candidates("  same   song ", "qq", limit=5)
        second = self.controller.search_candidates("same song", "qq", limit=5)

        self.assertEqual(music.search_calls, 1)
        self.assertEqual(first, second)
        self.assertNotIn("url", first[0])

    def test_search_adapter_failure_is_not_negative_cached(self) -> None:
        music = _FailedSearchMusic()
        self.controller.platforms["qq"] = music

        self.controller.search_candidates("same", "qq", limit=5)
        self.controller.search_candidates("same", "qq", limit=5)

        self.assertEqual(music.search_calls, 2)

    def test_next_song_advances_queue(self) -> None:
        self.controller.play_song("first", "qq", "text", "area", "user")
        self.controller.play_song("second", "qq", "text", "area", "user")
        self.controller.play_next("text", "area")
        self._wait_for_play_count(2)
        self.assertEqual(self.runtime.played[-1], "https://audio.invalid/second.mp3")
        self.assertEqual(self.controller._get_queue("area").get_current()["name"], "second")

    def test_next_song_resolves_fresh_url_for_album_queue_item(self) -> None:
        queue = self.controller._get_queue("area")
        queue.add_to_queue(
            {
                "song_id": "album-track",
                "platform": "qq",
                "name": "album song",
                "artists": "artist",
                "duration_ms": 120_000,
                "url": "",
            }
        )

        result = self.controller.play_next("text", "area", "user")
        self._wait_for_play_count(1)

        self.assertEqual(result["code"], "success")
        self.assertEqual(
            self.runtime.played[-1],
            "https://audio.invalid/album-track.mp3",
        )

    def test_playback_succeeds_when_text_notification_fails(self) -> None:
        self.runtime.fail_messages = True

        result = self.controller.play_song("first", "qq", "text", "area", "user")
        self._wait_for_play_count(1)

        self.assertEqual(result["code"], "success")
        self.assertEqual(self.runtime.played, ["https://audio.invalid/first.mp3"])

    def test_unplayable_song_returns_structured_error(self) -> None:
        self.controller.platforms["qq"] = _UnplayableMusic()

        result = self.controller.play_song("first", "qq", "text", "area", "user")

        self.assertEqual(result["code"], "error")
        self.assertEqual(result["message"], "无法获取歌曲播放地址")
        self.assertEqual(self.runtime.played, [])

    def test_play_request_does_not_wait_for_voice_startup(self) -> None:
        self.runtime.play_gate = threading.Event()

        started = time.monotonic()
        result = self.controller.play_song("first", "qq", "text", "area", "user")
        elapsed = time.monotonic() - started

        self.assertEqual(result["code"], "success")
        self.assertLess(elapsed, 0.2)
        state = self.controller._get_queue("area").get_play_state()
        self.assertTrue(state["loading"])
        self.runtime.play_gate.set()
        self._wait_for_play_count(1)


if __name__ == "__main__":
    unittest.main()
