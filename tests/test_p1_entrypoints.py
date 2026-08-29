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

        def schedule(coroutine):
            coroutine.close()
            task = _ScheduledTask()
            scheduled.append(task)
            return task

        with (
            patch.object(self.service, "JM_ENABLED", True),
            patch.object(self.service, "JM_ALLOWED_USERS", set()),
            patch.object(self.service, "_inspect_jm_album", return_value=12),
            patch.object(
                self.service.operations,
                "begin_jm_job",
                return_value="job-1",
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
        )
        self.assertIn("已开始下载 JM123", reply.await_args.args[1])

    async def test_batch_jm_entry_creates_ordered_jobs_and_one_background_run(self) -> None:
        message = self._message("JM 123 456")
        scheduled: list[_ScheduledTask] = []

        def schedule(coroutine):
            coroutine.close()
            task = _ScheduledTask()
            scheduled.append(task)
            return task

        with (
            patch.object(self.service, "JM_ENABLED", True),
            patch.object(self.service, "JM_ALLOWED_USERS", set()),
            patch.object(self.service, "_inspect_jm_album", side_effect=[12, 24]),
            patch.object(
                self.service.operations,
                "begin_jm_job",
                side_effect=["job-1", "job-2"],
            ) as begin,
            patch.object(self.service.operations, "update_jm_job"),
            patch.object(self.client, "_reply", new=AsyncMock()) as reply,
            patch.object(asyncio, "create_task", side_effect=schedule),
        ):
            await self.client._start_jm_batch(message, ["123", "456"], "user-1")

        self.assertEqual(len(scheduled), 1)
        self.assertEqual(begin.call_count, 2)
        self.assertIn("已开始 JM 批量任务，共 2 个", reply.await_args.args[1])


if __name__ == "__main__":
    unittest.main()
