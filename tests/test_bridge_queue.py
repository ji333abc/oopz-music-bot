from __future__ import annotations

import sys
import time
import types
import unittest
from unittest.mock import patch

try:
    import fastapi  # noqa: F401
except ImportError:
    fastapi_module = types.ModuleType("fastapi")
    responses_module = types.ModuleType("fastapi.responses")

    class _FakeRouter:
        def get(self, *_args, **_kwargs):
            return lambda function: function

        def post(self, *_args, **_kwargs):
            return lambda function: function

    fastapi_module.APIRouter = _FakeRouter
    fastapi_module.Request = object
    responses_module.JSONResponse = dict
    sys.modules["fastapi"] = fastapi_module
    sys.modules["fastapi.responses"] = responses_module

from oopzbot import bridge
from oopzbot.controller import MusicQueue


class _FakeMusic:
    def __init__(self) -> None:
        self.queue = MusicQueue()
        self.search_limit = 0

    def _get_queue(self, _area: str) -> MusicQueue:
        return self.queue

    def search_candidates(self, keyword: str, platform: str, limit: int = 5) -> list[dict]:
        del platform
        self.search_limit = limit
        return [
            {
                "id": f"song-{index}",
                "name": f"{keyword}-{index}",
                "artists": "artist",
                "durationText": "3:00",
            }
            for index in range(1, 13)
        ][:limit]


class _LegacyQueue:
    """Minimal shape exposed by the embedded legacy Redis QueueManager."""

    def __init__(self, items: list[dict]) -> None:
        self.items = [dict(item) for item in items]

    def get_current(self):
        return None

    def get_queue(self) -> list[dict]:
        return [dict(item) for item in self.items]

    def get_queue_length(self) -> int:
        return len(self.items)

    def remove_from_queue(self, index: int) -> bool:
        if index < 0 or index >= len(self.items):
            return False
        self.items.pop(index)
        return True


class _LegacyMusic:
    def __init__(self, items: list[dict]) -> None:
        self.queue = _LegacyQueue(items)

    def _get_queue(self, _area: str) -> _LegacyQueue:
        return self.queue


class _FakeSender:
    def get_area_channels(self, area: str, quiet: bool = True) -> list[dict]:
        del area, quiet
        return [
            {
                "channels": [
                    {"id": "text", "name": "聊天", "type": "TEXT"},
                    {"id": "music", "name": "Music", "type": "VOICE"},
                    {"id": "lobby", "name": "大厅", "type": "AUDIO"},
                ]
            }
        ]

    def get_voice_channel_members(self, area: str) -> dict:
        del area
        return {
            "music": [
                {"uid": "bot", "name": "OOPZ Bot"},
                {"uid": "user-1", "nickname": "Alice"},
            ],
            "lobby": [],
        }


class VoiceChannelPayloadTests(unittest.TestCase):
    def test_returns_structured_channels_members_and_configured_marker(self) -> None:
        music = _FakeMusic()
        music.sender = _FakeSender()
        music.names = types.SimpleNamespace(user=lambda uid: uid)

        channels = bridge._voice_channels_payload(music, "area", "music", "bot")

        self.assertEqual([channel["name"] for channel in channels], ["Music", "大厅"])
        self.assertTrue(channels[0]["configured"])
        self.assertEqual(channels[0]["member_count"], 1)
        self.assertEqual(channels[0]["members"][1]["name"], "Alice")
        self.assertTrue(channels[0]["members"][0]["is_bot"])


class QueuePanelTests(unittest.TestCase):
    def test_queue_panel_contains_ten_actionable_items(self) -> None:
        music = _FakeMusic()
        for index in range(1, 13):
            music.queue.add_to_queue({"name": f"song-{index}", "artists": "artist"})

        result = bridge._queue_panel(music, "area")

        self.assertEqual(result["reply_type"], "queue_panel")
        self.assertEqual(result["queue_length"], 12)
        self.assertEqual(len(result["queue_items"]), 10)
        self.assertEqual(len(result["queue_all"]), 12)
        self.assertEqual(result["queue_items"][0]["index"], 1)

    def test_remove_multiple_positions_returns_refreshed_panel(self) -> None:
        music = _FakeMusic()
        for name in ("one", "two", "three", "four"):
            music.queue.add_to_queue({"name": name, "artists": "artist"})

        result = bridge._remove_queue_items(music, "area", [2, 4])

        self.assertTrue(result["ok"])
        self.assertIn("two、four", result["message"])
        self.assertEqual(
            [item["name"] for item in result["queue_items"]],
            ["one", "three"],
        )

    def test_remove_positions_supports_legacy_redis_queue(self) -> None:
        music = _LegacyMusic(
            [
                {"name": name, "artists": "artist"}
                for name in ("one", "two", "three", "four")
            ]
        )

        result = bridge._remove_queue_items(music, "area", [2, 4])

        self.assertTrue(result["ok"])
        self.assertIn("two、four", result["message"])
        self.assertEqual(
            [item["name"] for item in result["queue_items"]],
            ["one", "three"],
        )

    def test_invalid_legacy_position_does_not_partially_delete(self) -> None:
        music = _LegacyMusic(
            [{"name": "one", "artists": "artist"}]
        )

        result = bridge._remove_queue_items(music, "area", [1, 2])

        self.assertFalse(result["ok"])
        self.assertEqual([item["name"] for item in music.queue.items], ["one"])

    def test_queue_position_parser_accepts_spaces_and_commas(self) -> None:
        self.assertEqual(bridge._parse_queue_positions("2 5,7，9"), [2, 5, 7, 9])
        self.assertIsNone(bridge._parse_queue_positions("2 x"))

    def test_panel_and_delete_commands_route_through_bridge(self) -> None:
        music = _FakeMusic()
        for name in ("one", "two", "three"):
            music.queue.add_to_queue({"name": name, "artists": "artist"})
        previous_dependency = bridge._music_dependency
        bridge._music_dependency = music
        try:
            with patch.object(
                bridge,
                "_command_config",
                return_value=("area", "text", "voice", "bot"),
            ):
                panel = bridge._execute_command("面板", "group:user")
                updated = bridge._execute_command("删除 2", "group:user")
        finally:
            bridge._music_dependency = previous_dependency

        self.assertEqual(panel["reply_type"], "queue_panel")
        self.assertEqual(
            [item["name"] for item in updated["queue_items"]],
            ["one", "three"],
        )

    def test_status_command_returns_structured_playback_data(self) -> None:
        music = _FakeMusic()
        music.queue.set_current(
            {
                "song_id": "current-1",
                "name": "playing",
                "artists": "artist",
                "duration_ms": 180_000,
            }
        )
        music.queue.set_play_state(
            {"start_time": time.time() - 12, "duration": 180, "loading": False}
        )
        previous_dependency = bridge._music_dependency
        bridge._music_dependency = music
        try:
            with patch.object(
                bridge,
                "_command_config",
                return_value=("area", "text", "voice", "bot"),
            ):
                result = bridge._execute_command("状态", "group:user")
        finally:
            bridge._music_dependency = previous_dependency

        self.assertEqual(result["reply_type"], "playback_status")
        self.assertEqual(result["current"]["id"], "current-1")
        self.assertTrue(result["playing"])
        self.assertGreaterEqual(result["progress"], 11)
        self.assertEqual(result["duration"], 180)


class SearchResultTests(unittest.TestCase):
    def test_search_returns_first_ten_candidates(self) -> None:
        music = _FakeMusic()

        result = bridge._search_songs(music, "hello", "group:user")

        self.assertEqual(music.search_limit, 10)
        self.assertEqual(len(result["songs"]), 10)
        self.assertEqual(result["songs"][-1]["index"], 10)


if __name__ == "__main__":
    unittest.main()
