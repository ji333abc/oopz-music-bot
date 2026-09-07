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
from oopzbot.commands.parser import parse_platform_keyword
from oopzbot.controller import MusicQueue


class _FakeMusic:
    def __init__(self) -> None:
        self.queue = MusicQueue()
        self.search_limit = 0
        self.search_platform = ""

    def _get_queue(self, _area: str) -> MusicQueue:
        return self.queue

    def search_candidates(self, keyword: str, platform: str, limit: int = 5) -> list[dict]:
        self.search_platform = platform
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


class _FailedPlaybackMusic(_FakeMusic):
    def __init__(self) -> None:
        super().__init__()
        self._voice_channel_id = "voice"
        self._voice_channel_area = "area"

    def play_song(self, *_args) -> dict:
        return {"code": "error", "message": "暂无播放链接"}

    def play_song_choice(self, *_args) -> dict:
        return {"code": "error", "message": "歌曲不可播放"}


class _RecordingPlaybackMusic(_FakeMusic):
    def __init__(self) -> None:
        super().__init__()
        self._voice_channel_id = "voice"
        self._voice_channel_area = "area"
        self.play_args = None

    def play_song(self, *args) -> dict:
        self.play_args = args
        return {"code": "success", "message": "已点歌"}


class _AlbumProvider:
    def search_albums(self, keyword: str, limit: int = 5) -> list[dict]:
        return [
            {"id": "album-1", "name": keyword, "artists": "artist", "cover": ""}
        ][:limit]

    def get_album(self, album_id: str) -> dict:
        return {
            "id": album_id,
            "name": "album",
            "artists": "artist",
            "track_count": 3,
            "tracks": [
                {
                    "id": f"track-{index}",
                    "name": f"song-{index}",
                    "artists": "artist",
                    "album": "album",
                    "duration": 180_000,
                    "durationText": "3:00",
                }
                for index in range(1, 4)
            ],
        }


class _AlbumMusic(_FakeMusic):
    def __init__(self) -> None:
        super().__init__()
        self._voice_channel_id = "voice"
        self._voice_channel_area = "area"

    def play_next(self, *_args) -> dict:
        song = self.queue.play_next()
        if song:
            self.queue.set_current(song)
        return {"code": "success", "message": "next"}

    def play_song_choice(self, *_args) -> dict:
        return {"code": "success", "message": "selected"}


class _FailedAlbumStartMusic(_AlbumMusic):
    def play_next(self, *_args) -> dict:
        return {
            "code": "error",
            "message": "连续 3 首歌曲暂不可播放，已停止自动跳过",
        }


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

    def test_album_batch_enqueue_is_one_versioned_write_without_urls(self) -> None:
        music = _AlbumMusic()
        music.queue.set_current({"song_id": "current", "name": "current"})
        requester = "group:user"
        provider = _AlbumProvider()
        album = provider.get_album("album-1")
        bridge._album_sessions.put(
            requester,
            albums=[],
            album=album,
            tracks=album["tracks"],
        )
        settings = types.SimpleNamespace(
            album_request_max_tracks=30,
            album_request_session_ttl_seconds=300,
        )
        before = music.queue.get_version()

        with patch.object(bridge, "get_settings", return_value=settings):
            result = bridge._queue_album_tracks(
                music,
                "全部",
                requester,
                "area",
                "text",
                "voice",
                "bot",
                expected_version=before,
            )

        queued = music.queue.get_queue()
        self.assertTrue(result["ok"])
        self.assertEqual(len(queued), 3)
        self.assertEqual(music.queue.get_version(), before + 1)
        self.assertTrue(all(not song["url"] for song in queued))
        self.assertEqual(len({song["batch_id"] for song in queued}), 1)

    def test_album_batch_reports_when_automatic_playback_cannot_start(self) -> None:
        music = _FailedAlbumStartMusic()
        requester = "group:failed-album-start"
        album = _AlbumProvider().get_album("album-1")
        bridge._album_sessions.put(
            requester,
            albums=[],
            album=album,
            tracks=album["tracks"],
        )
        settings = types.SimpleNamespace(
            album_request_max_tracks=30,
            album_request_session_ttl_seconds=300,
        )

        with patch.object(bridge, "get_settings", return_value=settings):
            result = bridge._queue_album_tracks(
                music,
                "全部",
                requester,
                "area",
                "text",
                "voice",
                "bot",
                expected_version=music.queue.get_version(),
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["playback_started"])
        self.assertIn("未能开始播放", result["message"])
        self.assertIn("连续 3 首歌曲暂不可播放", result["message"])
        self.assertEqual(music.queue.get_queue_length(), 3)

    def test_album_track_slice_supports_non_contiguous_positions(self) -> None:
        tracks = [{"id": f"track-{index}"} for index in range(1, 11)]

        selected = bridge._album_track_slice(tracks, "1 3 5 7 9")
        reordered = bridge._album_track_slice(tracks, "9，5,1 5")

        self.assertEqual(
            [track["id"] for track in selected or []],
            ["track-1", "track-3", "track-5", "track-7", "track-9"],
        )
        self.assertEqual(
            [track["id"] for track in reordered or []],
            ["track-9", "track-5", "track-1"],
        )
        self.assertIsNone(bridge._album_track_slice(tracks, "1 11"))
        self.assertIsNone(bridge._album_track_slice(tracks, "1 three 5"))

    def test_album_search_and_selection_create_requester_session(self) -> None:
        music = _AlbumMusic()
        provider = _AlbumProvider()
        requester = "group:album-user"
        settings = types.SimpleNamespace(album_request_session_ttl_seconds=300)

        with (
            patch.object(bridge, "get_settings", return_value=settings),
            patch.object(bridge, "_album_provider", return_value=provider),
        ):
            searched = bridge._search_albums(music, "album", requester)
            selected = bridge._select_album(music, 1, requester)

        self.assertEqual(searched["reply_type"], "album_search_results")
        self.assertEqual(selected["reply_type"], "album_detail")
        self.assertEqual(len(selected["tracks"]), 3)

    def test_album_command_is_feature_gated_and_routes_when_enabled(self) -> None:
        music = _AlbumMusic()
        provider = _AlbumProvider()
        request = bridge.CommandRequest(
            command="专辑 album",
            requester_id="user",
            group_openid="group",
            source="panel",
        )
        disabled = types.SimpleNamespace(album_request_enabled=False)
        enabled = types.SimpleNamespace(
            album_request_enabled=True,
            album_request_session_ttl_seconds=300,
        )

        with (
            patch.object(bridge, "_command_config", return_value=("area", "text", "voice", "bot")),
            patch.object(bridge, "_music_handler", return_value=music),
            patch.object(bridge, "_album_provider", return_value=provider),
            patch.object(bridge, "get_settings", return_value=disabled),
        ):
            blocked = bridge._execute_command_impl(request)
        with (
            patch.object(bridge, "_command_config", return_value=("area", "text", "voice", "bot")),
            patch.object(bridge, "_music_handler", return_value=music),
            patch.object(bridge, "_album_provider", return_value=provider),
            patch.object(bridge, "get_settings", return_value=enabled),
        ):
            routed = bridge._execute_command_impl(request)

        self.assertFalse(blocked["ok"])
        self.assertIn("尚未启用", blocked["message"])
        self.assertEqual(routed["reply_type"], "album_search_results")

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

    def test_move_returns_versioned_queue_and_rejects_stale_version(self) -> None:
        music = _FakeMusic()
        for name in ("one", "two", "three"):
            music.queue.add_to_queue({"name": name, "artists": "artist"})
        version = music.queue.get_version()

        moved = bridge._move_queue_item(music, "area", 1, 3, version)
        conflict = bridge._move_queue_item(music, "area", 1, 2, version)

        self.assertEqual([item["name"] for item in moved["queue_all"]], ["two", "three", "one"])
        self.assertEqual(moved["queue_version"], version + 1)
        self.assertEqual(conflict["code"], "queue_conflict")
        self.assertEqual(conflict["actual_version"], version + 1)
        self.assertEqual(conflict["queue"], conflict["queue_all"])

    def test_stale_delete_and_clear_do_not_mutate_newer_queue(self) -> None:
        music = _FakeMusic()
        for name in ("one", "two", "three"):
            music.queue.add_to_queue({"name": name, "artists": "artist"})
        stale = music.queue.get_version()
        music.queue.add_to_queue({"name": "four", "artists": "artist"})

        removed = bridge._remove_queue_items(music, "area", [2], stale)
        cleared = bridge._clear_queue_items(music, "area", stale)

        self.assertEqual(removed["code"], "queue_conflict")
        self.assertEqual(cleared["code"], "queue_conflict")
        self.assertEqual(
            [item["name"] for item in music.queue.get_queue()],
            ["one", "two", "three", "four"],
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

    def test_diagnostic_write_failure_does_not_change_command_result(self) -> None:
        music = _FakeMusic()
        previous_dependency = bridge._music_dependency
        bridge._music_dependency = music
        try:
            with patch.object(
                bridge,
                "_command_config",
                return_value=("area", "text", "voice", "bot"),
            ), patch.object(
                bridge.operations,
                "record_command_timing",
                side_effect=OSError("read-only diagnostics"),
            ):
                result = bridge._execute_command("面板", "group:user")
        finally:
            bridge._music_dependency = previous_dependency

        self.assertTrue(result["ok"])


class SearchResultTests(unittest.TestCase):
    def test_platform_prefix_parser_preserves_legacy_aliases(self) -> None:
        self.assertEqual(parse_platform_keyword("QQ: 周杰伦"), ("qq", "周杰伦"))
        self.assertEqual(parse_platform_keyword("b站：稻香"), ("bilibili", "稻香"))
        self.assertEqual(parse_platform_keyword("网易: 搁浅"), ("netease", "搁浅"))

    def test_search_returns_first_ten_candidates(self) -> None:
        music = _FakeMusic()

        result = bridge._search_songs(music, "hello", "group:user")

        self.assertEqual(music.search_limit, 10)
        self.assertEqual(len(result["songs"]), 10)
        self.assertEqual(result["songs"][-1]["index"], 10)

    def test_search_uses_selected_platform(self) -> None:
        music = _FakeMusic()

        bridge._search_songs(
            music,
            "hello",
            "group:user",
            platform="bilibili",
        )

        self.assertEqual(music.search_platform, "bilibili")

    def test_oopz_play_uses_oopz_requester_and_platform(self) -> None:
        from oopzbot.domain.contracts import CommandRequest

        music = _RecordingPlaybackMusic()
        previous_dependency = bridge._music_dependency
        bridge._music_dependency = music
        try:
            with patch.object(
                bridge,
                "_command_config",
                return_value=("area", "text", "voice", "bot"),
            ):
                result = bridge._execute_request(
                    CommandRequest(
                        command="播放 b站：稻香",
                        requester_id="oopz-user",
                        group_openid="area",
                        source="oopz",
                    )
                )
        finally:
            bridge._music_dependency = previous_dependency

        self.assertTrue(result.ok)
        self.assertEqual(
            music.play_args,
            ("稻香", "bilibili", "text", "area", "oopz-user"),
        )

    def test_failed_song_selection_is_reported_and_session_is_retained(self) -> None:
        music = _FailedPlaybackMusic()
        requester = "group:failed-selection"
        bridge._search_songs(music, "hello", requester)

        result = bridge._select_song(
            music,
            index=1,
            requester_key=requester,
            area="area",
            text_channel="text",
            voice_channel="voice",
            bot_user="bot",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "歌曲不可播放")
        self.assertIn(requester, bridge._search_sessions)

    def test_failed_direct_play_is_not_reported_as_submitted(self) -> None:
        music = _FailedPlaybackMusic()
        previous_dependency = bridge._music_dependency
        bridge._music_dependency = music
        try:
            with patch.object(
                bridge,
                "_command_config",
                return_value=("area", "text", "voice", "bot"),
            ):
                result = bridge._execute_command("播放 hello", "group:user")
        finally:
            bridge._music_dependency = previous_dependency

        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "暂无播放链接")


if __name__ == "__main__":
    unittest.main()
