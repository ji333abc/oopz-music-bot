"""Exercise the shipped legacy queue methods without booting OOPZ/Redis."""

from __future__ import annotations

import ast
import copy
import logging
import threading
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from oopzbot.controller import MusicQueue

ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT / "legacy_oopzbot/src/music/music.py").read_text(encoding="utf-8"))
handler_class = next(
    node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MusicHandler"
)
names = {
    "_commit_song_request",
    "_resolve_queued_song",
    "_dequeue_next_song",
    "_build_song_data_from_platform_data",
}
methods = [
    node for node in handler_class.body if isinstance(node, ast.FunctionDef) and node.name in names
]
namespace = dict(
    copy=copy,
    logging=logging,
    threading=threading,
    time=time,
    uuid=uuid,
    logger=logging.getLogger(__name__),
    _PLATFORM_NETEASE="netease",
    PLAY_MODE_SINGLE="single",
    PLAY_MODE_SHUFFLE="shuffle",
    _MAX_CONSECUTIVE_QUEUE_RESOLVE_FAILURES=3,
    SongCache=types.SimpleNamespace(record_play=Mock()),
    Statistics=types.SimpleNamespace(update_today=Mock()),
)
isolated = ast.Module(
    body=[ast.ClassDef(name="Handler", bases=[], keywords=[], body=methods, decorator_list=[])],
    type_ignores=[],
)
exec(compile(ast.fix_missing_locations(isolated), "legacy-bilibili-playback", "exec"), namespace)


class LegacyBilibiliPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.music = namespace["Handler"]()
        self.queue = MusicQueue()
        self.provider = types.SimpleNamespace(
            get_song_url=Mock(return_value="http://127.0.0.1:1234/fresh"), last_song_url_error=""
        )
        self.music.platforms = {"bilibili": self.provider}
        self.music._playback_lock = threading.RLock()
        self.music._get_queue = Mock(return_value=self.queue)
        self.music._is_playing = lambda: bool(self.queue.get_current())
        self.music._mark_web_active_area = Mock()
        self.music._start_playing = Mock()
        self.music._preload_next_song_if_any = Mock()
        self.music._consume_cover_prefetch = Mock(return_value=None)
        self.music._resolve_song_attachments = Mock(return_value=([], None, False))
        self.music._build_song_request_text = lambda song, **_kwargs: song["name"]
        self.music._stream_to_voice_channel = Mock()
        self.song = {
            "song_id": "BV13x41117TL?p=2",
            "platform": "bilibili",
            "name": "视频第二P",
            "artists": "UP主",
            "duration_ms": 60000,
            "channel": "text",
            "area": "area",
            "user": "user",
            "url": "https://old.bilivideo.com/expired",
        }

    def test_first_song_resolves_but_pending_song_does_not_and_is_restart_safe(self):
        with patch.object(threading, "Thread"):
            first = self.music._commit_song_request(self.song)
            queued = self.music._commit_song_request(self.song)
        self.assertEqual(first["code"], "success")
        self.assertEqual(queued["code"], "success")
        self.assertEqual(self.provider.get_song_url.call_count, 1)
        self.assertEqual(self.queue.get_current()["url"], "http://127.0.0.1:1234/fresh")
        pending = self.queue.get_queue()[0]
        self.assertEqual(pending["url"], "")
        self.assertEqual(pending["song_id"], "BV13x41117TL?p=2")
        self.assertEqual(self.song["url"], "https://old.bilivideo.com/expired")

    def test_old_saved_url_is_replaced_and_page_context_is_preserved(self):
        resolved = self.music._resolve_queued_song(self.song)
        self.assertEqual(resolved["url"], "http://127.0.0.1:1234/fresh")
        self.assertEqual(resolved["channel"], "text")
        self.provider.get_song_url.assert_called_once()
        self.assertEqual(self.provider.get_song_url.call_args.args, ("BV13x41117TL?p=2",))

    def test_resolution_failure_does_not_start_or_save_current_song(self):
        self.provider.get_song_url.return_value = None
        self.provider.last_song_url_error = "B站访问受限"
        with patch.object(threading, "Thread") as thread:
            result = self.music._commit_song_request(self.song)
        self.assertEqual(result["code"], "error")
        self.assertIn("访问受限", result["message"])
        self.assertIsNone(self.queue.get_current())
        thread.assert_not_called()

    def test_single_repeat_re_resolves_audio_instead_of_reusing_expired_url(self):
        self.music.get_play_mode = lambda: "single"
        resolved, mode = self.music._dequeue_next_song(True, self.song)
        self.assertEqual(mode, "single")
        self.assertEqual(resolved["url"], "http://127.0.0.1:1234/fresh")
