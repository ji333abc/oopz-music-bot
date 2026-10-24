"""Keep external Bilibili metadata out of QQ/OOPZ chat and media uploads."""

from __future__ import annotations

import ast
import copy
import json
import logging
import threading
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from test_bridge_queue import bridge
from test_qqbot_uploader_bridge import _FakeGroupAPI, _FakeGroupMessage, service

from oopzbot.commands.formatter import (
    bilibili_identity,
    format_bilibili_request,
    format_queue,
    format_search,
    public_song_attachments,
    public_song_label,
    public_song_metadata,
)
from oopzbot.controller import MusicQueue
from oopzbot.domain.contracts import QueueItem, QueueSnapshot, SongCandidate

ROOT = Path(__file__).resolve().parents[1]
BV = "BV13x41117TL"
PRIVATE = "PRIVATE_EXTERNAL_METADATA"
SONG = {
    "platform": "bilibili",
    "song_id": BV + "?p=2",
    "id": BV + "?p=2",
    "name": PRIVATE + "_title",
    "artists": PRIVATE + "_uploader",
    "album": PRIVATE + "_album",
    "cover": "https://image.invalid/" + PRIVATE,
    "duration": "03:25",
    "duration_ms": 205000,
    "attachments": [{"fileKey": PRIVATE, "width": 100, "height": 100}],
    "channel": "text",
    "area": "area",
    "url": "old-url",
}


def legacy_class(path, class_name, method_names):
    """Load actual legacy methods without starting the network/Redis runtime."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    cls = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    methods = [
        node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in method_names
    ]
    isolated = ast.Module(
        body=[ast.ClassDef(name="Handler", bases=[], keywords=[], body=methods, decorator_list=[])],
        type_ignores=[],
    )
    namespace = dict(
        json=json,
        logger=logging.getLogger(__name__),
        time=time,
        threading=threading,
        uuid=uuid,
        format_bilibili_request=format_bilibili_request,
        public_song_label=public_song_label,
        public_song_attachments=public_song_attachments,
        public_song_metadata=public_song_metadata,
        WEB_PLAYER_CONFIG={},
        _PLATFORM_NETEASE="netease",
        _MAX_CONSECUTIVE_QUEUE_RESOLVE_FAILURES=3,
        ImageCache=Mock(),
        SongCache=Mock(),
        Statistics=Mock(),
    )
    exec(compile(ast.fix_missing_locations(isolated), path, "exec"), namespace)
    return namespace["Handler"], namespace


class PublicBilibiliTests(unittest.TestCase):
    def assert_public(self, payload):
        self.assertNotIn(PRIVATE, str(payload))
        self.assertNotIn("![IMAGE", str(payload))

    def test_templates_queue_and_search_exclude_external_metadata(self):
        song = SongCandidate(
            song_id=SONG["song_id"],
            name=SONG["name"],
            artists=SONG["artists"],
            album=SONG["album"],
            cover=SONG["cover"],
            platform="bilibili",
            duration_ms=205000,
            duration_text=PRIVATE,
        )
        item = QueueItem(song=song)
        for text in (
            format_bilibili_request(SONG),
            format_bilibili_request(SONG, queue_position=3),
            format_queue(QueueSnapshot(current=item, pending=(item,))),
            format_search(BV, (song,)),
        ):
            self.assert_public(text)
            self.assertIn(BV, text)
        self.assertIn("队列位置：3", format_bilibili_request(SONG, queue_position=3))
        self.assertIn("03:25", format_bilibili_request(SONG))

    def test_display_copy_preserves_original_and_music_metadata(self):
        original = copy.deepcopy(SONG)
        public = public_song_metadata(SONG)
        self.assert_public(
            {key: public[key] for key in ("name", "artists", "album", "cover", "attachments")}
        )
        self.assertEqual(public["cover"], "")
        self.assertEqual(public_song_attachments(SONG), [])
        self.assertEqual(SONG, original)
        music = dict(SONG, platform="qq")
        self.assertEqual(public_song_metadata(music), music)
        self.assertEqual(public_song_attachments(music), music["attachments"])
        self.assertEqual(public_song_label(music), f"{music['name']} - {music['artists']}")

    def test_malformed_saved_identity_and_duration_cannot_inject_text(self):
        for identifier in (PRIVATE, BV + "?p=" + PRIVATE, BV + "?p=10001", BV + "\n" + PRIVATE):
            with self.subTest(identifier=identifier):
                text = format_bilibili_request(
                    dict(SONG, song_id=identifier, duration=PRIVATE, duration_ms=0)
                )
                self.assert_public(text)
                self.assertEqual(bilibili_identity({"id": identifier}), "音源")

    def test_bridge_queue_status_and_removal_keep_panel_metadata_private(self):
        queue = MusicQueue()
        queue.set_current(SONG)
        queue.add_to_queue(SONG)
        music = types.SimpleNamespace(queue=queue, _get_queue=lambda _area: queue)
        panel = bridge._queue_panel(music, "area")
        self.assert_public(panel["message"])
        self.assertEqual(panel["current"]["name"], SONG["name"])
        status = bridge._status_summary(
            music,
            "area",
            "voice",
            "bot",
            playback={
                "current": panel["current"],
                "paused": False,
                "progress": 0,
                "duration": 205,
                "queue_length": 1,
                "online_count": 1,
            },
        )
        self.assert_public(status)
        removed = bridge._remove_queue_items(music, "area", [1])
        self.assert_public(removed["message"])
        self.assert_public(removed["notice"])
        self.assertIn(BV, removed["notice"])

    def test_qq_markdown_and_buttons_do_not_render_private_queue_fields(self):
        client = object.__new__(service.OopzQQClient)
        result = {"current": SONG, "queue_items": [dict(SONG, index=1)], "queue_length": 1}
        for output in (
            client._queue_markdown(result),
            client._queue_keyboard(result, "requester"),
            client._search_markdown({"songs": [dict(SONG, index=1)]}),
            client._search_keyboard({"songs": [dict(SONG, index=1)]}, "requester"),
        ):
            self.assert_public(output)

    def test_legacy_requests_switches_and_cover_uploads_ignore_stored_attachments(self):
        handler, namespace = legacy_class(
            "legacy_oopzbot/src/music/music.py",
            "MusicHandler",
            {
                "_build_song_request_text",
                "_cover_prefetch_key",
                "_resolve_song_attachments",
                "show_queue",
            },
        )
        music = handler()
        music.sender = Mock()
        music._get_web_link = Mock(return_value=PRIVATE)
        self.assert_public(music._build_song_request_text(SONG, prefix=PRIVATE))
        music._get_web_link.assert_not_called()
        self.assertIsNone(music._cover_prefetch_key(SONG))
        self.assertEqual(music._resolve_song_attachments(SONG), ([], None, False))
        namespace["ImageCache"].get_by_source.assert_not_called()
        music.sender.upload_file_from_url.assert_not_called()
        queue = MusicQueue()
        queue.add_to_queue(SONG)
        music._get_queue = lambda _area: types.SimpleNamespace(
            get_queue=lambda *_bounds: queue.get_queue(),
            get_queue_length=queue.get_queue_length,
        )
        music.show_queue("text", "area")
        self.assert_public(music.sender.send_message.call_args.args[0])

        playback, _ = legacy_class(
            "legacy_oopzbot/src/music/music_playback.py",
            "PlaybackMixin",
            {"_build_now_playing_text"},
        )
        player = playback()
        for prefix in ("自动播放", "切换到下一首"):
            self.assert_public(player._build_now_playing_text(prefix, SONG))

    def test_legacy_manual_next_filters_pre_upgrade_queue_attachments(self):
        handler, _ = legacy_class(
            "legacy_oopzbot/src/music/music.py", "MusicHandler", {"play_next"}
        )
        playback, _ = legacy_class(
            "legacy_oopzbot/src/music/music_playback.py",
            "PlaybackMixin",
            {"_build_now_playing_text"},
        )
        music = handler()
        queue = MusicQueue()
        queue.add_to_queue(SONG)
        music._voice_channel_id = "voice"
        music._playback_lock = threading.Lock()
        music._get_queue = lambda _area: queue
        music._resolve_queued_song = lambda song, *_args: song
        music.voice = None
        music._mark_web_active_area = Mock()
        music._start_playing = Mock()
        music._stream_to_voice_channel = Mock()
        music._preload_next_song_if_any = Mock()
        music._build_now_playing_text = types.MethodType(playback._build_now_playing_text, music)
        music.sender = Mock()
        with patch.object(threading, "Thread"):
            result = music.play_next("text", "area", "user")
        self.assertEqual(result["code"], "success")
        self.assert_public(music.sender.send_message.call_args.kwargs)
        self.assertEqual(music.sender.send_message.call_args.kwargs["attachments"], [])
        self.assertEqual(queue.get_current()["name"], SONG["name"])

    def test_web_notifications_handle_bilibili_and_untyped_legacy_messages(self):
        handler, _ = legacy_class(
            "legacy_oopzbot/src/music/music_web_control.py",
            "WebControlExecutor",
            {"_handle_notify"},
        )
        executor = handler()
        executor.h = types.SimpleNamespace(
            _voice_channel_id="voice",
            _voice_channel_area="area",
            queue=MusicQueue(),
            sender=Mock(),
        )
        for info in (dict(SONG, position=2), {"name": PRIVATE, "artists": PRIVATE, "position": 2}):
            executor._handle_notify("notify:" + json.dumps(info))
            self.assert_public(executor.h.sender.send_message.call_args.args[0])


class QQBilibiliReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_song_sends_only_the_minimal_reply(self):
        api = _FakeGroupAPI()
        client = object.__new__(service.OopzQQClient)
        await client._reply_song_selected(_FakeGroupMessage(api), {"song": SONG})
        self.assertEqual(len(api.calls), 1)
        self.assertNotIn(PRIVATE, str(api.calls))
        self.assertIn(BV, str(api.calls))
        self.assertEqual(api.file_calls, [])
