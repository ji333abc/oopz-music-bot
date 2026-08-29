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


class _CommandMusic:
    def __init__(self) -> None:
        self.queue = MusicQueue()
        self._voice_channel_id = "voice"
        self._voice_channel_area = "area"
        self.events: list[str] = []
        self.names = types.SimpleNamespace(
            user=lambda user_id: {"user-2": "Alice"}.get(user_id, user_id),
        )
        self.sender = types.SimpleNamespace(
            send_message=lambda **_kwargs: True,
            get_area_channels=lambda **_kwargs: [
                {
                    "name": "Music",
                    "channels": [
                        {"id": "voice", "name": "主语音", "type": "VOICE"},
                        {"id": "lobby", "name": "大厅", "type": "AUDIO"},
                    ],
                }
            ],
            get_voice_channel_members=lambda **_kwargs: {
                "voice": [
                    {"uid": "bot", "name": "机器人"},
                    {"uid": "user-2"},
                ],
                "lobby": [],
            },
        )
        self.platforms = {
            "qq": types.SimpleNamespace(
                get_song_url=lambda song_id, **_kwargs: f"https://audio.invalid/{song_id}.mp3",
            ),
        }
        self.voice = types.SimpleNamespace(
            pause_audio=lambda: self._record_voice("pause"),
            resume_audio=lambda: self._record_voice("resume"),
        )

    def _record_voice(self, event: str) -> bool:
        self.events.append(event)
        return True

    def _get_queue(self, _area: str) -> MusicQueue:
        return self.queue

    def play_next(self, *_args) -> dict:
        self.events.append("next")
        return {"code": "success", "message": "已切换到下一首"}

    def stop_play(self, *_args) -> dict:
        self.events.append("stop")
        return {"code": "success", "message": "已停止播放"}

    def search_candidates(self, keyword: str, _platform: str, limit: int = 10) -> list[dict]:
        title = keyword.split()[0]
        return [
            {
                "id": f"song-{index}",
                "name": title if index == 1 else f"{title}-{index}",
                "artists": "歌手",
                "album": "专辑",
                "duration": 180000,
                "durationText": "3:00",
                "cover": "https://img.invalid/cover.jpg",
            }
            for index in range(1, min(limit, 3) + 1)
        ]

    def enter_voice_channel(self, channel: str, area: str) -> dict:
        self.events.append("enter_voice")
        self._voice_channel_id = channel
        self._voice_channel_area = area
        return {"ok": True}

    def play_song(self, keyword: str, *_args) -> dict:
        self.events.append("play")
        return {"code": "success", "message": f"已提交点歌：{keyword}"}

    def play_song_choice(self, song: dict, channel: str, area: str, user: str) -> dict:
        self.events.append("play_choice")
        song_data = self._build_song_data_from_platform_data(
            song,
            "qq",
            str(song.get("id") or song.get("song_id")),
            channel,
            area,
            user,
        )
        return self._commit_song_request(song_data, prefix="已选择")

    def _build_song_data_from_platform_data(
        self,
        song: dict,
        platform: str,
        song_id: str,
        channel: str,
        area: str,
        user: str,
    ) -> dict:
        return {
            "song_id": song_id,
            "platform": platform,
            "name": song["name"],
            "artists": song["artists"],
            "album": song.get("album", ""),
            "duration": song.get("durationText", "3:00"),
            "duration_ms": song.get("duration", 180000),
            "cover": song.get("cover", ""),
            "url": song.get("url", ""),
            "channel": channel,
            "area": area,
            "user": user,
            "attachments": [],
        }

    def _commit_song_request(self, song_data: dict, prefix: str = "已点歌") -> dict:
        if self.queue.get_current():
            position = self.queue.add_to_queue(song_data)
            message = f"{prefix}：{song_data['name']} - {song_data['artists']}\n已加入队列，第 {position} 首"
        else:
            self.queue.set_current(song_data)
            self.queue.set_play_state(
                {"start_time": time.time(), "duration": 180, "loading": False}
            )
            message = f"{prefix}：{song_data['name']} - {song_data['artists']}"
        return {"code": "success", "message": message, "attachments": []}

    def _kickoff_cover_prefetch(self, _song_data: dict) -> None:
        self.events.append("cover_prefetch")

    def _preload_next_song_if_any(self) -> None:
        self.events.append("preload")


class CommandBehaviorSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.music = _CommandMusic()
        self.previous_dependency = bridge._music_dependency
        bridge._music_dependency = self.music
        self.config = patch.object(
            bridge,
            "_command_config",
            return_value=("area", "text", "voice", "bot"),
        )
        self.config.start()

    def tearDown(self) -> None:
        self.config.stop()
        bridge._music_dependency = self.previous_dependency

    def test_rank_catalog_detail_selection_and_batch_keep_existing_contracts(self) -> None:
        previous_sessions = bridge._rank_sessions.copy()
        bridge._rank_sessions.clear()
        rank_id, rank_title = bridge._QQ_RANKS[0]
        payload = {
            "data": [
                {
                    "rank": index,
                    "title": f"榜歌曲{index}",
                    "singerName": "歌手",
                    "albumMid": f"album-{index}",
                }
                for index in range(1, 4)
            ]
        }
        try:
            with patch.object(bridge, "_qq_music_api_get", return_value=payload):
                catalog = bridge._execute_command("排行榜", "group:user")
                detail = bridge._execute_command(
                    f"榜单 {rank_id}",
                    "group:user",
                )
                selected = bridge._execute_command("榜单点歌 1", "group:user")
                detail_again = bridge._execute_command(
                    f"榜单 {rank_title}",
                    "group:user",
                )
                batch = bridge._execute_command("榜单批量 2", "group:user")
        finally:
            bridge._rank_sessions.clear()
            bridge._rank_sessions.update(previous_sessions)

        self.assertEqual(catalog["reply_type"], "rank_catalog")
        self.assertTrue(any(item["id"] == rank_id for item in catalog["ranks"]))
        self.assertEqual(detail["reply_type"], "rank_results")
        self.assertEqual(len(detail["songs"]), 3)
        self.assertEqual(selected["reply_type"], "song_selected")
        self.assertIn("榜歌曲1", selected["message"])
        self.assertEqual(detail_again["reply_type"], "rank_results")
        self.assertEqual(batch["reply_type"], "rank_batch_queued")
        self.assertEqual(batch["added_count"], 2)
        self.assertEqual(self.music.queue.get_queue_length(), 2)

    def test_online_voice_members_and_help_keep_stable_semantics(self) -> None:
        online = bridge._execute_command("在线", "group:user")
        members = bridge._execute_command("语音成员", "group:user")
        help_result = bridge._execute_command("帮助", "group:user")

        self.assertTrue(online["ok"])
        self.assertIn("主语音", online["message"])
        self.assertIn("Alice", online["message"])
        self.assertTrue(members["ok"])
        self.assertIn("Alice", members["message"])
        self.assertTrue(help_result["ok"])
        self.assertIn("搜歌", help_result["message"])

    def test_next_and_stop_keep_the_existing_user_messages(self) -> None:
        next_result = bridge._execute_command("下一首", "group:user")
        stop_result = bridge._execute_command("停止", "group:user")

        self.assertEqual(next_result, {"ok": True, "message": "已执行切歌"})
        self.assertEqual(stop_result, {"ok": True, "message": "已停止播放"})
        self.assertEqual(self.music.events, ["next", "stop"])

    def test_pause_and_resume_keep_idempotent_state_messages(self) -> None:
        self.music.queue.set_current({"song_id": "song-1", "name": "歌曲"})
        self.music.queue.set_play_state(
            {"start_time": time.time() - 2, "duration": 180, "loading": False}
        )

        paused = bridge._execute_command("暂停", "group:user")
        repeated_pause = bridge._execute_command("暂停", "group:user")
        resumed = bridge._execute_command("继续", "group:user")

        self.assertEqual(paused["message"], "已暂停播放")
        self.assertEqual(repeated_pause["message"], "播放已经暂停")
        self.assertEqual(resumed["message"], "已继续播放")
        self.assertEqual(self.music.events, ["pause", "resume"])

    def test_unknown_command_keeps_the_stable_help_hint(self) -> None:
        result = bridge._execute_command("不是命令", "group:user")

        self.assertFalse(result["ok"])
        self.assertIn("面板、搜歌 <歌名>", result["message"])


class SearchSessionSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_sessions = bridge._search_sessions.copy()
        bridge._search_sessions.clear()

    def tearDown(self) -> None:
        bridge._search_sessions.clear()
        bridge._search_sessions.update(self.previous_sessions)

    def test_search_sessions_are_isolated_by_requester_and_expire(self) -> None:
        music = types.SimpleNamespace(
            search_candidates=lambda keyword, _platform, limit=10: [
                {"id": keyword, "name": keyword, "artists": "artist"}
            ][:limit]
        )

        first = bridge._search_songs(music, "first", "group:user-1")
        second = bridge._search_songs(music, "second", "group:user-2")

        self.assertEqual(first["songs"][0]["name"], "first")
        self.assertEqual(second["songs"][0]["name"], "second")
        self.assertEqual(set(bridge._search_sessions), {"group:user-1", "group:user-2"})

        bridge._search_sessions["group:user-1"]["expires_at"] = time.monotonic() - 1
        expired = bridge._select_song(
            music,
            index=1,
            requester_key="group:user-1",
            area="area",
            text_channel="text",
            voice_channel="voice",
            bot_user="bot",
        )
        self.assertFalse(expired["ok"])
        self.assertNotIn("group:user-1", bridge._search_sessions)


if __name__ == "__main__":
    unittest.main()
