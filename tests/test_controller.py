from __future__ import annotations

import unittest

from oopzbot.config import Settings
from oopzbot.controller import MusicController, MusicQueue


class _FakeRuntime:
    ready = True
    bot = None

    def __init__(self) -> None:
        self.played: list[str] = []
        self.messages: list[tuple[str, str, str]] = []

    def join_voice(self, area: str, channel: str) -> None:
        self.joined = (area, channel)

    def leave_voice(self) -> None:
        pass

    def call(self, operation, timeout=None):
        del timeout

        class Voice:
            async def play_url(inner_self, url):
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
        self.messages.append((text, area, channel))

    def user_name(self, uid: str) -> str:
        return uid


class _FakeMusic:
    def search_many(self, keyword: str, limit: int = 5):
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


class MusicControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = _FakeRuntime()
        self.controller = MusicController(_settings(), self.runtime)
        self.controller.platforms["qq"] = _FakeMusic()
        self.controller.enter_voice_channel("voice", "area")

    def tearDown(self) -> None:
        self.controller._closed.set()

    def test_first_song_plays_and_second_song_queues(self) -> None:
        first = self.controller.play_song("first", "qq", "text", "area", "user")
        second = self.controller.play_song("second", "qq", "text", "area", "user")

        self.assertEqual(first["code"], "success")
        self.assertEqual(second["code"], "success")
        self.assertEqual(self.runtime.played, ["https://audio.invalid/first.mp3"])
        self.assertEqual(self.controller._get_queue("area").get_queue_length(), 1)

    def test_next_song_advances_queue(self) -> None:
        self.controller.play_song("first", "qq", "text", "area", "user")
        self.controller.play_song("second", "qq", "text", "area", "user")
        self.controller.play_next("text", "area")
        self.assertEqual(self.runtime.played[-1], "https://audio.invalid/second.mp3")
        self.assertEqual(self.controller._get_queue("area").get_current()["name"], "second")


if __name__ == "__main__":
    unittest.main()
