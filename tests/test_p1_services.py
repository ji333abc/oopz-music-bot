from __future__ import annotations

import asyncio
import threading
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from oopzbot.application.playback_monitor_service import PlaybackMonitorService
from oopzbot.application.playback_service import PlaybackService
from oopzbot.application.queue_service import QueuePositionError, QueueService
from oopzbot.domain.compat import queue_item_from_legacy, queue_item_to_legacy
from oopzbot.domain.contracts import ComponentStatus
from oopzbot.infrastructure.queue_adapter import LegacyQueueAdapter
from oopzbot.jm.service import JMTaskCoordinator
from oopzbot.jm.uploader import upload_archive
from oopzbot.legacy_runtime import LegacyOopzRuntimeAdapter
from oopzbot.qq.reply_policy import ReplyErrorKind, ReplyPolicy, classify_reply_error


class _LegacyQueue:
    def __init__(self, items: list[dict] | None = None) -> None:
        self.items = list(items or [])
        self.current = None
        self.play_state = None

    def get_queue(self):
        return [dict(item) for item in self.items]

    def get_queue_length(self):
        return len(self.items)

    def add_to_queue(self, item):
        self.items.append(dict(item))
        return len(self.items) - 1

    def play_next(self):
        return self.items.pop(0) if self.items else None

    def remove_from_queue(self, index):
        self.items.pop(index)
        return True

    def clear_queue(self):
        self.items.clear()

    def get_current(self):
        return self.current

    def set_current(self, item):
        self.current = dict(item)

    def clear_current(self):
        self.current = None

    def get_play_state(self):
        return self.play_state

    def set_play_state(self, state):
        self.play_state = dict(state)

    def clear_play_state(self):
        self.play_state = None


class QueueServiceTests(unittest.TestCase):
    def test_invalid_multi_remove_is_atomic_and_one_based(self) -> None:
        raw = [{"song_id": str(index), "name": f"song-{index}"} for index in range(1, 4)]
        legacy = _LegacyQueue(raw)
        service = QueueService(LegacyQueueAdapter(legacy))

        with self.assertRaises(QueuePositionError):
            service.remove([1, 9])
        self.assertEqual([item["song_id"] for item in legacy.items], ["1", "2", "3"])

        removed = service.remove([3, 1, 1])
        self.assertEqual([item.song.song_id for item in removed], ["1", "3"])
        self.assertEqual([item["song_id"] for item in legacy.items], ["2"])

    def test_adapter_keeps_old_flat_json_readable_in_both_directions(self) -> None:
        old = {
            "song_id": "mid-1",
            "name": "name",
            "artists": "artist",
            "channel": "text",
            "area": "area",
            "user": "user",
            "play_uuid": "uuid",
            "future_field": {"kept": True},
        }
        self.assertEqual(queue_item_to_legacy(queue_item_from_legacy(old))["future_field"], {"kept": True})
        legacy = _LegacyQueue([old])
        snapshot = QueueService(LegacyQueueAdapter(legacy)).snapshot()
        self.assertEqual(snapshot.pending[0].position, 1)
        self.assertEqual(snapshot.pending[0].play_uuid, "uuid")


class PlaybackServiceTests(unittest.TestCase):
    def test_playback_errors_are_structured_and_notification_success_is_preserved(self) -> None:
        backend = SimpleNamespace(
            play_song=lambda *_args: {"code": "success", "message": "播放成功"},
            play_song_choice=lambda *_args: {"code": "error", "message": "暂无播放链接"},
            play_next=lambda *_args: None,
            stop_play=lambda *_args: None,
        )
        service = PlaybackService(backend)
        success = service.play_keyword(
            "song", platform="qq", channel="text", area="area", requester_id="user"
        )
        failure = service.play_choice(
            {"id": "1"}, channel="text", area="area", requester_id="user"
        )
        self.assertTrue(success.ok)
        self.assertFalse(failure.ok)
        self.assertEqual(failure.error.stage, "playing")

    def test_liked_commands_are_owned_by_playback_service(self) -> None:
        calls = []
        backend = SimpleNamespace(
            play_liked=lambda channel, area, user, count: calls.append(
                ("random", channel, area, user, count)
            ),
            play_liked_by_index=lambda index, channel, area, user: calls.append(
                ("pick", index, channel, area, user)
            ),
            show_liked_list=lambda channel, area, page: calls.append(
                ("list", channel, area, page)
            ),
        )
        service = PlaybackService(backend)

        self.assertTrue(
            service.play_liked(
                channel="text",
                area="area",
                requester_id="user",
                count=3,
            ).ok
        )
        self.assertTrue(
            service.play_liked_by_index(
                2,
                channel="text",
                area="area",
                requester_id="user",
            ).ok
        )
        self.assertTrue(service.show_liked(4, channel="text", area="area").ok)
        self.assertEqual(
            calls,
            [
                ("random", "text", "area", "user", 3),
                ("pick", 2, "text", "area", "user"),
                ("list", "text", "area", 4),
            ],
        )


class PlaybackMonitorServiceTests(unittest.TestCase):
    def test_monitor_has_one_owner_and_stops_cooperatively(self) -> None:
        class Backend:
            def __init__(self) -> None:
                self.calls = 0
                self.started = threading.Event()

            def auto_play_monitor(self, stop_event=None) -> None:
                self.calls += 1
                self.started.set()
                stop_event.wait(2)

        backend = Backend()
        service = PlaybackMonitorService(backend)

        service.start()
        self.assertTrue(backend.started.wait(1))
        service.start()
        self.assertEqual(backend.calls, 1)
        self.assertTrue(service.running)

        service.stop(timeout=1)
        self.assertFalse(service.running)


class ReplyPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_policy_bounds_proactive_permission_failure(self) -> None:
        calls = []
        failures = deque([RuntimeError("msgid 已失效"), RuntimeError("40034105 主动消息失败, 无权限")])

        async def post(**payload):
            calls.append(payload)
            raise failures.popleft()

        sequence = iter((10, 11, 12)).__next__
        policy = ReplyPolicy(sequence)
        with self.assertRaisesRegex(RuntimeError, "40034105"):
            await policy.send(
                post,
                group_openid="group",
                payload={"msg_id": "message", "msg_seq": 9, "content": "x"},
            )
        self.assertEqual(len(calls), 2)
        self.assertNotIn("msg_id", calls[-1])
        self.assertEqual(
            classify_reply_error(RuntimeError("40034105 主动消息失败, 无权限")),
            ReplyErrorKind.PROACTIVE_FORBIDDEN,
        )


class _FakeMusic:
    def __init__(self) -> None:
        self._voice_channel_id = None
        self._voice_channel_area = None
        self.voice = SimpleNamespace(
            available=True,
            play_audio=lambda _url: None,
            pause_audio=lambda: True,
            resume_audio=lambda: True,
            stop_audio=lambda: None,
        )

    def notify_message(self, **_kwargs):
        return True

    def enter_voice_channel(self, channel, area):
        self._voice_channel_id = channel
        self._voice_channel_area = area
        return {"ok": True}

    def auto_play_monitor(self, stop_event=None):
        return None


class _FakeLegacyCore:
    def __init__(self, *, authenticated=True) -> None:
        self.music = _FakeMusic()
        self.bot = SimpleNamespace(
            config=SimpleNamespace(person_uid="legacy-person-uid")
        )
        self._closed = SimpleNamespace(is_set=lambda: False)
        self.context = SimpleNamespace(
            client=SimpleNamespace(authenticated=authenticated, connected=authenticated)
        )

    @property
    def ready(self):
        return self.context.client.authenticated

    def start(self):
        return self.music

    def close(self):
        return None


class RuntimeAdapterTests(unittest.TestCase):
    def test_legacy_adapter_has_bounded_status_and_idempotent_start(self) -> None:
        adapter = LegacyOopzRuntimeAdapter(_FakeLegacyCore())
        self.assertTrue(adapter.start().ok)
        self.assertTrue(adapter.start().ok)
        self.assertEqual(adapter.status().status, ComponentStatus.OK)
        self.assertEqual(
            adapter.component_status("websocket").status,
            ComponentStatus.OK,
        )
        self.assertTrue(adapter.enter_voice(area="area", channel="voice").ok)
        self.assertTrue(adapter.play("https://audio", area="area", channel="voice").ok)

    def test_adapter_preserves_password_login_person_uid_for_commands(self) -> None:
        adapter = LegacyOopzRuntimeAdapter(_FakeLegacyCore())
        self.assertTrue(adapter.start().ok)
        self.assertEqual(adapter.bot.config.person_uid, "legacy-person-uid")

    def test_adapter_exposes_explicit_application_owners(self) -> None:
        core = _FakeLegacyCore()
        bind = Mock()
        core.context.handler = SimpleNamespace(bind_external_music_command=bind)
        adapter = LegacyOopzRuntimeAdapter(core)
        self.assertEqual(adapter.command_implementation, "legacy-music-command-fallback")
        self.assertEqual(adapter.playback_monitor_implementation, "playback-monitor-service")
        self.assertTrue(adapter.start().ok)

        def handler(*_args):
            return True

        adapter.bind_music_command_handler(handler)
        bind.assert_called_once_with(handler)
        self.assertEqual(adapter.command_implementation, "shared-command-service")

    def test_unstarted_adapter_returns_structured_operation_failure(self) -> None:
        adapter = LegacyOopzRuntimeAdapter(_FakeLegacyCore())
        result = adapter.send_text("text", area="area", channel="channel")
        self.assertFalse(result.ok)
        self.assertEqual(result.error.stage, "notification")


class JMCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_is_retained_then_removed_and_lock_is_reusable(self) -> None:
        coordinator = JMTaskCoordinator()
        self.assertTrue(coordinator.acquire())
        self.assertFalse(coordinator.acquire())
        coordinator.release()
        self.assertTrue(coordinator.acquire())
        coordinator.release()

        async def complete():
            await asyncio.sleep(0)

        task = coordinator.track(asyncio.create_task(complete()))
        self.assertIn(task, coordinator.tasks)
        await task
        await asyncio.sleep(0)
        self.assertNotIn(task, coordinator.tasks)

    async def test_cancelling_upload_kills_the_child_process(self) -> None:
        class EmptyStream:
            async def read(self):
                return b""

            async def readline(self):
                return b""

        class Process:
            def __init__(self):
                self.stdout = EmptyStream()
                self.stderr = EmptyStream()
                self.returncode = None
                self.killed = False

            async def wait(self):
                if self.killed:
                    self.returncode = -9
                    return -9
                await asyncio.Future()

            def kill(self):
                self.killed = True

        process = Process()
        with patch("asyncio.create_subprocess_exec", return_value=process):
            task = asyncio.create_task(
                upload_archive(
                    node="node",
                    uploader="uploader",
                    archive=Path("archive.zip"),
                    group_openid="group",
                    message_id="message",
                    display_name="archive.zip",
                    environment={},
                    timeout_seconds=30,
                    logger=SimpleNamespace(info=lambda *_args: None),
                )
            )
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(process.killed)


if __name__ == "__main__":
    unittest.main()
