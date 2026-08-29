from __future__ import annotations

import ast
import json
import unittest
from enum import StrEnum
from pathlib import Path

from oopzbot.domain.compat import (
    command_request_from_legacy,
    command_request_to_legacy,
    command_result_from_legacy,
    command_result_to_legacy,
    display_song_to_legacy,
    playback_state_from_legacy,
    playback_state_to_legacy,
    queue_item_from_legacy,
    queue_item_to_display,
    queue_item_to_legacy,
    queue_snapshot_from_legacy,
)
from oopzbot.domain.contracts import (
    CommandRequest,
    ComponentStatus,
    ErrorKind,
    MusicProviderPort,
    OopzRuntimePort,
    PlaybackPhase,
    QueuePort,
)


class DomainImportTests(unittest.TestCase):
    def test_contract_module_has_no_runtime_framework_dependencies(self) -> None:
        source = Path(__file__).parents[1] / "oopzbot" / "domain" / "contracts.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        forbidden = {"fastapi", "botpy", "redis", "legacy_oopzbot"}
        imports = {
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        imports.update(
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertTrue(forbidden.isdisjoint(imports))

    def test_contracts_expose_only_small_ports_and_finite_statuses(self) -> None:
        self.assertEqual(ComponentStatus.OK.value, "ok")
        self.assertEqual(PlaybackPhase.RESOLVING.value, "resolving")
        self.assertEqual(ErrorKind.DEPENDENCY.value, "dependency")
        self.assertTrue(issubclass(ComponentStatus, StrEnum))
        self.assertTrue(issubclass(ErrorKind, StrEnum))
        self.assertTrue(issubclass(PlaybackPhase, StrEnum))
        self.assertTrue(hasattr(MusicProviderPort, "search"))
        self.assertTrue(hasattr(QueuePort, "remove_positions"))
        self.assertTrue(hasattr(OopzRuntimePort, "current_state"))


class CompatibilityConversionTests(unittest.TestCase):
    def test_queue_item_round_trip_preserves_current_flat_fields(self) -> None:
        legacy = json.loads(
            """
            {
              "song_id": "song-1",
              "name": "歌曲",
              "artists": "歌手",
              "album": "专辑",
              "duration": "3:00",
              "duration_ms": 180000,
              "durationText": "3:00",
              "cover": "https://img.invalid/cover.jpg",
              "platform": "qq",
              "url": "https://audio.invalid/song.mp3",
              "channel": "text-1",
              "area": "area-1",
              "user": "user-1",
              "attachments": [
                {"fileKey": "cover-key", "width": 300, "height": 300}
              ],
              "play_uuid": "play-uuid-1",
              "image_cache_id": "cache-42",
              "legacy_extension": {"source": "old-core"}
            }
            """
        )

        item = queue_item_from_legacy(legacy)
        restored = queue_item_to_legacy(item)

        self.assertEqual(item.song.song_id, "song-1")
        self.assertEqual(item.channel, "text-1")
        self.assertEqual(restored["duration_ms"], 180000)
        self.assertEqual(restored["durationText"], "3:00")
        self.assertEqual(restored["url"], legacy["url"])
        self.assertEqual(restored["user"], "user-1")
        self.assertEqual(restored["attachments"], legacy["attachments"])
        self.assertEqual(restored["play_uuid"], "play-uuid-1")
        self.assertEqual(restored["image_cache_id"], "cache-42")
        self.assertEqual(restored["legacy_extension"], {"source": "old-core"})
        self.assertEqual(json.loads(json.dumps(restored)), restored)

        display = queue_item_to_display(item)
        self.assertNotIn("attachments", display)
        self.assertNotIn("play_uuid", display)
        self.assertNotIn("image_cache_id", display)
        self.assertNotIn("url", display)

    def test_missing_duration_text_formats_numeric_milliseconds_for_display(self) -> None:
        item = queue_item_from_legacy(
            {
                "id": "song-2",
                "name": "三分钟",
                "artists": "歌手",
                "duration": 180000,
            }
        )

        self.assertEqual(item.song.duration_ms, 180000)
        self.assertEqual(item.song.duration_text, "3:00")
        self.assertEqual(display_song_to_legacy(item.song)["duration"], "3:00")

    def test_playback_state_round_trip_preserves_playing_paused_and_loading(self) -> None:
        cases = (
            (
                {"progress": 12.5, "duration": 180, "loading": False, "paused": False},
                PlaybackPhase.PLAYING,
            ),
            (
                {
                    "progress": 45.25,
                    "duration": 180,
                    "loading": False,
                    "paused": True,
                    "pause_elapsed": 45.25,
                },
                PlaybackPhase.PLAYING,
            ),
            (
                {"progress": 0, "duration": 180, "loading": True, "paused": False},
                PlaybackPhase.RESOLVING,
            ),
        )

        for raw, phase in cases:
            with self.subTest(raw=raw):
                state = playback_state_from_legacy(raw, current_song_id="song-1")
                self.assertEqual(state.phase, phase)
                restored = playback_state_to_legacy(state)
                self.assertEqual(restored["progress"], raw["progress"])
                self.assertEqual(restored["duration"], raw["duration"])
                self.assertEqual(restored["paused"], raw["paused"])
                self.assertEqual(restored["loading"], raw["loading"])

    def test_command_result_round_trip_preserves_playback_progress(self) -> None:
        result = command_result_from_legacy(
            {
                "ok": True,
                "reply_type": "playback_status",
                "current": {"id": "song-1", "name": "歌曲"},
                "playing": True,
                "paused": True,
                "loading": False,
                "progress": 37.5,
                "duration": 180,
            }
        )

        self.assertEqual(result.playback.progress_seconds, 37.5)
        self.assertEqual(command_result_to_legacy(result)["progress"], 37.5)

    def test_queue_snapshot_assigns_one_based_pending_positions(self) -> None:
        snapshot = queue_snapshot_from_legacy(
            {"song_id": "current", "name": "当前"},
            [
                {"song_id": "one", "name": "第一"},
                {"song_id": "two", "name": "第二"},
            ],
            {"start_time": 1.0, "duration": 180, "loading": False},
        )

        self.assertEqual(snapshot.queue_length, 2)
        self.assertEqual(snapshot.pending[0].position, 1)
        self.assertEqual(snapshot.pending[1].position, 2)
        self.assertEqual(snapshot.playback.phase, PlaybackPhase.PLAYING)

    def test_command_request_round_trip_is_explicit(self) -> None:
        request = CommandRequest(
            command="搜歌 测试",
            requester_id="user-1",
            requester_name="测试用户",
            group_openid="group-1",
            source="qq",
            command_id="command-1234",
        )

        restored = command_request_from_legacy(command_request_to_legacy(request))

        self.assertEqual(restored, request)

    def test_command_result_round_trip_keeps_structured_error_without_message_matching(self) -> None:
        raw = {
            "ok": False,
            "message": "进入 OOPZ 语音频道失败: timeout",
            "reply_type": "error",
            "error_kind": "dependency",
            "error_stage": "joining",
            "command_id": "command-1234",
            "vendor_field": {"kept": True},
        }

        result = command_result_from_legacy(raw)
        restored = command_result_to_legacy(result)

        self.assertEqual(result.error.kind, ErrorKind.DEPENDENCY)
        self.assertEqual(result.error.stage, "joining")
        self.assertEqual(restored["error_kind"], "dependency")
        self.assertEqual(restored["vendor_field"], {"kept": True})

    def test_unclassified_legacy_error_does_not_guess_from_chinese_text(self) -> None:
        result = command_result_from_legacy(
            {"ok": False, "message": "Redis 不可用，请稍后重试"}
        )

        self.assertEqual(result.error.kind, ErrorKind.UNKNOWN)

    def test_search_result_keeps_ten_candidate_indexes(self) -> None:
        result = command_result_from_legacy(
            {
                "ok": True,
                "reply_type": "search_results",
                "songs": [
                    {"id": f"song-{index}", "name": f"歌{index}", "artists": "歌手"}
                    for index in range(1, 11)
                ],
            },
            command_id="command-1",
        )
        restored = command_result_to_legacy(result)

        self.assertEqual(len(result.songs), 10)
        self.assertEqual(restored["songs"][0]["index"], 1)
        self.assertEqual(restored["songs"][-1]["index"], 10)

    def test_rank_result_round_trip_preserves_rank_renderer_fields(self) -> None:
        raw = {
            "ok": True,
            "reply_type": "rank_results",
            "rank_id": 26,
            "title": "热歌榜",
            "songs": [
                {
                    "rank": 1,
                    "title": "榜单歌曲",
                    "artists": "榜单歌手",
                    "album_mid": "album-1",
                    "cover": "https://img.invalid/rank.jpg",
                }
            ],
        }

        restored = command_result_to_legacy(command_result_from_legacy(raw))

        self.assertEqual(restored["rank_id"], 26)
        self.assertEqual(restored["title"], "热歌榜")
        self.assertEqual(restored["songs"][0]["rank"], 1)
        self.assertEqual(restored["songs"][0]["title"], "榜单歌曲")
        self.assertEqual(restored["songs"][0]["album_mid"], "album-1")

    def test_qq_and_panel_results_have_same_semantics_for_one_bridge_response(self) -> None:
        raw = {
            "ok": True,
            "reply_type": "search_results",
            "message": "找到候选",
            "songs": [
                {"id": "song-1", "name": "歌曲", "artists": "歌手", "duration": 180000},
            ],
        }

        qq_result = command_result_from_legacy(raw, command_id="qq-command")
        panel_result = command_result_from_legacy(raw, command_id="panel-command")

        self.assertEqual(
            (
                qq_result.ok,
                qq_result.message,
                qq_result.reply_type,
                tuple(song.song_id for song in qq_result.songs),
                tuple(song.duration_text for song in qq_result.songs),
            ),
            (
                panel_result.ok,
                panel_result.message,
                panel_result.reply_type,
                tuple(song.song_id for song in panel_result.songs),
                tuple(song.duration_text for song in panel_result.songs),
            ),
        )
        self.assertNotEqual(qq_result.command_id, panel_result.command_id)


if __name__ == "__main__":
    unittest.main()
