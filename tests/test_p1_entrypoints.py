from __future__ import annotations

import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


class OopzMessageCallbackTests(unittest.TestCase):
    def test_music_mention_callback_routes_stop_and_search_commands(self) -> None:
        from oopzbot.legacy_runtime import _install_legacy_import_paths

        _install_legacy_import_paths()
        from app.services.routing.mention_command_router import MentionCommandRouter

        music_service = types.SimpleNamespace(
            handle_mention=Mock(side_effect=[True, True]),
        )
        plugins = types.SimpleNamespace(try_dispatch_mention=Mock(return_value=False))
        runtime = types.SimpleNamespace(
            services=types.SimpleNamespace(
                interaction=types.SimpleNamespace(music=music_service),
            ),
            plugin_host=object(),
        )
        router = object.__new__(MentionCommandRouter)
        router._runtime = runtime
        router._services = runtime.services
        router._plugins = plugins

        self.assertFalse(router.dispatch("停止", "channel", "area", "user"))
        self.assertFalse(router.dispatch("搜歌 测试", "channel", "area", "user"))
        self.assertEqual(
            music_service.handle_mention.call_args_list,
            [
                unittest.mock.call("停止", "channel", "area", "user"),
                unittest.mock.call("搜歌 测试", "channel", "area", "user"),
            ],
        )

    def test_modern_music_boundary_preempts_legacy_music_parser(self) -> None:
        from oopzbot.legacy_runtime import _install_legacy_import_paths

        _install_legacy_import_paths()
        from app.services.routing.mention_command_router import MentionCommandRouter

        modern = Mock(return_value=True)
        legacy = Mock(return_value=True)
        plugins = types.SimpleNamespace(try_dispatch_mention=Mock(return_value=False))
        runtime = types.SimpleNamespace(
            dispatch_external_music_command=modern,
            services=types.SimpleNamespace(
                interaction=types.SimpleNamespace(music=types.SimpleNamespace(handle_mention=legacy)),
            ),
            plugin_host=object(),
        )
        router = object.__new__(MentionCommandRouter)
        router._runtime = runtime
        router._services = runtime.services
        router._plugins = plugins

        self.assertFalse(router.dispatch("停止", "channel", "area", "user"))
        modern.assert_called_once_with("停止", "channel", "area", "user")
        legacy.assert_not_called()

    def test_modern_music_boundary_preempts_legacy_slash_parser(self) -> None:
        from oopzbot.legacy_runtime import _install_legacy_import_paths

        _install_legacy_import_paths()
        from app.services.routing.slash_command_router import SlashCommandRouter

        modern = Mock(return_value=True)
        legacy = Mock(return_value=True)
        plugins = types.SimpleNamespace(try_dispatch_slash=Mock(return_value=False))
        access = types.SimpleNamespace(is_admin=Mock(return_value=False))
        runtime = types.SimpleNamespace(
            dispatch_external_music_command=modern,
            services=types.SimpleNamespace(
                routing=types.SimpleNamespace(access=access),
                interaction=types.SimpleNamespace(music=types.SimpleNamespace(handle_slash=legacy)),
            ),
            plugin_host=object(),
        )
        router = object.__new__(SlashCommandRouter)
        router._runtime = runtime
        router._services = runtime.services
        router._plugins = plugins
        router._current_user = ""

        router.dispatch("/bf bili 稻香", "channel", "area", "user")

        modern.assert_called_once_with("/bf bili 稻香", "channel", "area", "user")
        legacy.assert_not_called()

    def test_oopz_slash_commands_normalize_into_shared_parser(self) -> None:
        from oopzbot import bridge

        self.assertEqual(bridge._modern_oopz_music_command("/queue"), "队列")
        self.assertEqual(
            bridge._modern_oopz_music_command("/bf bili 稻香"),
            "播放 bilibili:稻香",
        )
        self.assertEqual(bridge._modern_oopz_music_command("/pick 2"), "选歌 2")
        self.assertEqual(bridge._modern_oopz_music_command("/like 3"), "喜欢 3")
        self.assertEqual(
            bridge._modern_oopz_music_command("/like list 2"),
            "喜欢列表 2",
        )
        self.assertEqual(bridge._modern_oopz_music_command("播放"), "播放")

    def test_oopz_album_commands_normalize_into_shared_parser(self) -> None:
        from oopzbot import bridge

        commands = (
            "专辑 叶惠美",
            "专辑选择 1",
            "选专辑 1",
            "专辑曲目 2",
            "专辑点歌 3",
            "专辑加入 全部",
            "专辑加入 前5首",
            "专辑加入 3-8",
            "专辑加入 1 3 5 7 9",
            "取消专辑",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(bridge._modern_oopz_music_command(command), command)

    def test_oopz_dispatch_uses_transport_target_and_avoids_duplicate_play_reply(self) -> None:
        from oopzbot import bridge
        from oopzbot.domain.contracts import CommandResult

        with (
            patch.object(
                bridge,
                "_execute_request",
                return_value=CommandResult(ok=True, message="已点歌"),
            ) as execute,
            patch.object(bridge, "_notify_music") as notify,
        ):
            handled = bridge.dispatch_oopz_music_command(
                "播放 测试",
                "text-1",
                "area-1",
                "user-1",
            )

        self.assertTrue(handled)
        request = execute.call_args.args[0]
        self.assertEqual(request.source, "oopz")
        self.assertEqual(request.area_id, "area-1")
        self.assertEqual(request.text_channel_id, "text-1")
        self.assertEqual(request.bot_user_id, "")
        notify.assert_not_called()

    def test_oopz_album_search_sends_result_through_text_channel(self) -> None:
        from oopzbot import bridge
        from oopzbot.domain.contracts import CommandResult

        with (
            patch.object(
                bridge,
                "_execute_request",
                return_value=CommandResult(ok=True, message="找到专辑：叶惠美"),
            ),
            patch.object(bridge, "_music_handler", return_value=object()),
            patch.object(bridge, "_notify_music") as notify,
        ):
            handled = bridge.dispatch_oopz_music_command(
                "专辑 叶惠美",
                "text-1",
                "area-1",
                "user-1",
            )

        self.assertTrue(handled)
        notify.assert_called_once_with(
            unittest.mock.ANY,
            text="找到专辑：叶惠美",
            channel="text-1",
            area="area-1",
        )

    def test_oopz_album_song_avoids_duplicate_backend_reply(self) -> None:
        from oopzbot import bridge
        from oopzbot.domain.contracts import CommandResult

        with (
            patch.object(
                bridge,
                "_execute_request",
                return_value=CommandResult(
                    ok=True,
                    message="已选择歌曲",
                    extras={"backend_notified": True},
                ),
            ),
            patch.object(bridge, "_notify_music") as notify,
        ):
            handled = bridge.dispatch_oopz_music_command(
                "专辑点歌 3",
                "text-1",
                "area-1",
                "user-1",
            )

        self.assertTrue(handled)
        notify.assert_not_called()

    def test_oopz_album_song_validation_error_is_sent_to_text_channel(self) -> None:
        from oopzbot import bridge
        from oopzbot.domain.contracts import CommandResult

        with (
            patch.object(
                bridge,
                "_execute_request",
                return_value=CommandResult(ok=False, message="专辑搜索结果已失效"),
            ),
            patch.object(bridge, "_music_handler", return_value=object()),
            patch.object(bridge, "_notify_music") as notify,
        ):
            handled = bridge.dispatch_oopz_music_command(
                "专辑点歌 3",
                "text-1",
                "area-1",
                "user-1",
            )

        self.assertTrue(handled)
        notify.assert_called_once_with(
            unittest.mock.ANY,
            text="专辑搜索结果已失效",
            channel="text-1",
            area="area-1",
        )

    def test_oopz_dispatch_reports_modern_failure_without_legacy_fallback(self) -> None:
        from oopzbot import bridge

        with (
            patch.object(bridge, "_execute_request", side_effect=RuntimeError("boom")),
            patch.object(bridge, "_music_handler", return_value=object()),
            patch.object(bridge, "_notify_music") as notify,
        ):
            handled = bridge.dispatch_oopz_music_command(
                "停止",
                "text-1",
                "area-1",
                "user-1",
            )

        self.assertTrue(handled)
        notify.assert_called_once_with(
            unittest.mock.ANY,
            text="音乐命令执行失败，请稍后重试",
            channel="text-1",
            area="area-1",
        )

    def test_oopz_read_command_sends_one_result_through_legacy_sender(self) -> None:
        from oopzbot import bridge
        from oopzbot.domain.contracts import CommandResult

        with (
            patch.object(
                bridge,
                "_execute_request",
                return_value=CommandResult(ok=True, message="队列为空"),
            ),
            patch.object(bridge, "_music_handler", return_value=object()),
            patch.object(bridge, "_notify_music") as notify,
        ):
            handled = bridge.dispatch_oopz_music_command(
                "列表",
                "text-1",
                "area-1",
                "user-1",
            )

        self.assertTrue(handled)
        notify.assert_called_once_with(
            unittest.mock.ANY,
            text="队列为空",
            channel="text-1",
            area="area-1",
        )


class _ScheduledTask:
    def add_done_callback(self, callback) -> None:
        self.callback = callback


class QQCommandResultTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_rank_result_reaches_the_existing_qq_renderer_unchanged(self) -> None:
        import importlib

        service = importlib.import_module("oopzbot.qqbot")
        client = object.__new__(service.OopzQQClient)
        renderer = AsyncMock()
        result = {
            "ok": True,
            "reply_type": "rank_results",
            "title": "热歌榜",
            "songs": [
                {
                    "rank": 1,
                    "title": "榜单歌曲",
                    "artists": "歌手",
                    "album_mid": "album-1",
                }
            ],
        }

        with patch.object(client, "_reply_rank_results", new=renderer):
            await client._reply_result(
                types.SimpleNamespace(),
                result,
                "user-1",
            )

        renderer.assert_awaited_once()
        self.assertIs(renderer.await_args.args[1], result)

    async def test_album_detail_reaches_album_renderer_unchanged(self) -> None:
        import importlib

        service = importlib.import_module("oopzbot.qqbot")
        client = object.__new__(service.OopzQQClient)
        renderer = AsyncMock()
        result = {
            "ok": True,
            "reply_type": "album_detail",
            "album": {"id": "album-1", "name": "专辑"},
            "tracks": [{"id": "track-1", "name": "歌曲", "index": 1}],
        }

        with patch.object(client, "_reply_album_detail", new=renderer):
            await client._reply_result(types.SimpleNamespace(), result, "user-1")

        renderer.assert_awaited_once()
        self.assertIs(renderer.await_args.args[1], result)

    async def test_album_detail_keyboard_stays_within_five_rows(self) -> None:
        import importlib

        service = importlib.import_module("oopzbot.qqbot")
        client = object.__new__(service.OopzQQClient)
        client._post_group_message = AsyncMock()
        client._reply_identity = Mock(return_value={})
        result = {
            "ok": True,
            "reply_type": "album_detail",
            "album": {"id": "album-1", "name": "专辑", "track_count": 30},
            "tracks": [
                {"id": f"track-{index}", "name": f"歌曲 {index}", "index": index}
                for index in range(11, 21)
            ],
            "page": 2,
            "total_pages": 3,
        }

        await client._reply_album_detail(
            types.SimpleNamespace(), result, "user-1"
        )

        payload = client._post_group_message.await_args.args[1]
        self.assertLessEqual(len(payload["keyboard"]["content"]["rows"]), 5)


class JMEntryContractTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _message(content: str = "") -> types.SimpleNamespace:
        return types.SimpleNamespace(
            group_openid="group-1",
            id=f"message-{id(content)}",
            content=content,
            author=types.SimpleNamespace(
                member_openid="user-1",
                id="user-1",
                username="tester",
            ),
        )

    async def asyncSetUp(self) -> None:
        import importlib

        try:
            import botpy  # noqa: F401
        except ImportError:
            botpy_module = types.ModuleType("botpy")
            botpy_module.Client = object
            botpy_module.Intents = object
            message_module = types.ModuleType("botpy.message")
            message_module.GroupMessage = object
            sys.modules["botpy"] = botpy_module
            sys.modules["botpy.message"] = message_module
        self.service = importlib.import_module("oopzbot.qqbot")
        self.client = object.__new__(self.service.OopzQQClient)
        self.original_tasks = self.service._jm_tasks.copy()
        self.service._jm_tasks.clear()
        if self.service._jm_job_lock.locked():
            self.service._jm_job_lock.release()

    async def asyncTearDown(self) -> None:
        self.service._jm_tasks.clear()
        if self.service._jm_job_lock.locked():
            self.service._jm_job_lock.release()
        self.service._jm_tasks.update(self.original_tasks)

    async def test_group_message_entry_forwards_command_and_replies_once(self) -> None:
        message = self._message("帮助")
        reply_result = AsyncMock()
        with (
            patch.object(
                self.service,
                "_forward_command",
                return_value={"ok": True, "message": "帮助"},
            ),
            patch.object(self.client, "_reply_result", new=reply_result),
        ):
            await self.client._handle_group_at_message_create(message, "command-1")

        reply_result.assert_awaited_once()
        self.assertEqual(reply_result.await_args.args[1]["message"], "帮助")

    async def test_group_message_entry_routes_single_and_batch_jm_commands(self) -> None:
        start_batch = AsyncMock()
        with (
            patch.object(self.service, "ALLOWED_GROUPS", set()),
            patch.object(self.service, "_is_duplicate", return_value=False),
            patch.object(self.client, "_start_jm_batch", new=start_batch),
        ):
            await self.client._handle_group_at_message_create(
                self._message("JM 123"),
                "command-single",
            )
            await self.client._handle_group_at_message_create(
                self._message("JM 123 456"),
                "command-batch",
            )

        self.assertEqual(start_batch.await_count, 2)
        self.assertEqual(start_batch.await_args_list[0].args[1:], (["123"], "user-1"))
        self.assertEqual(
            start_batch.await_args_list[1].args[1:],
            (["123", "456"], "user-1"),
        )

    async def test_single_jm_entry_creates_job_and_schedules_background_run(self) -> None:
        message = self._message("JM 123")
        scheduled: list[_ScheduledTask] = []
        queue = types.SimpleNamespace(
            available=Mock(return_value=True),
            submit_many=Mock(),
        )

        def schedule(coroutine):
            coroutine.close()
            task = _ScheduledTask()
            scheduled.append(task)
            return task

        with (
            patch.object(self.service, "JM_ENABLED", True),
            patch.object(self.service, "JM_ALLOWED_USERS", set()),
            patch.object(self.service, "RedisJMQueue", return_value=queue),
            patch.object(
                self.service.operations,
                "begin_jm_job",
                return_value="a" * 32,
            ) as begin,
            patch.object(self.service.operations, "update_jm_job"),
            patch.object(self.client, "_reply", new=AsyncMock()) as reply,
            patch.object(asyncio, "create_task", side_effect=schedule),
        ):
            await self.client._start_jm_job(message, "123", "user-1")

        self.assertEqual(len(scheduled), 1)
        begin.assert_called_once_with(
            "123",
            requester="QQ 群用户",
            batch_index=1,
            batch_total=1,
        )
        queue.submit_many.assert_called_once()
        submitted = queue.submit_many.call_args.args[0]
        self.assertEqual([job.album_id for job in submitted], ["123"])
        self.assertIn("由独立 worker 顺序处理", reply.await_args.args[1])

    async def test_batch_jm_entry_creates_ordered_jobs_and_one_background_run(self) -> None:
        message = self._message("JM 123 456")
        scheduled: list[_ScheduledTask] = []
        queue = types.SimpleNamespace(
            available=Mock(return_value=True),
            submit_many=Mock(),
        )

        def schedule(coroutine):
            coroutine.close()
            task = _ScheduledTask()
            scheduled.append(task)
            return task

        with (
            patch.object(self.service, "JM_ENABLED", True),
            patch.object(self.service, "JM_ALLOWED_USERS", set()),
            patch.object(self.service, "RedisJMQueue", return_value=queue),
            patch.object(
                self.service.operations,
                "begin_jm_job",
                side_effect=["a" * 32, "b" * 32],
            ) as begin,
            patch.object(self.service.operations, "update_jm_job"),
            patch.object(self.client, "_reply", new=AsyncMock()) as reply,
            patch.object(asyncio, "create_task", side_effect=schedule),
        ):
            await self.client._start_jm_batch(message, ["123", "456"], "user-1")

        self.assertEqual(len(scheduled), 1)
        self.assertEqual(begin.call_count, 2)
        queue.submit_many.assert_called_once()
        submitted = queue.submit_many.call_args.args[0]
        self.assertEqual([job.album_id for job in submitted], ["123", "456"])
        self.assertIn("已提交 JM 任务，共 2 个", reply.await_args.args[1])

    async def test_enabled_jm_without_worker_stays_available_for_music(self) -> None:
        message = self._message("JM 123")
        queue = types.SimpleNamespace(available=Mock(return_value=False))

        with (
            patch.object(self.service, "JM_ENABLED", True),
            patch.object(self.service, "JM_ALLOWED_USERS", set()),
            patch.object(self.service, "RedisJMQueue", return_value=queue),
            patch.object(self.client, "_reply", new=AsyncMock()) as reply,
        ):
            await self.client._start_jm_job(message, "123", "user-1")

        self.assertIn("JM 服务未启用或当前不可用", reply.await_args.args[1])
        self.assertTrue(self.service._jm_job_lock.acquire(blocking=False))
        self.service._jm_job_lock.release()


if __name__ == "__main__":
    unittest.main()
