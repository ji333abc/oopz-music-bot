"""User blocks must be enforced before music side effects on every chat path."""

from __future__ import annotations

import ast
import os
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_bridge_queue import _RecordingPlaybackMusic, bridge
from test_qqbot_uploader_bridge import _FakeGroupAPI, _FakeGroupMessage, service

from oopzbot.commands import access
from oopzbot.commands.access import MUSIC_ACCESS_DENIED, music_request_blocked, music_user_blocked
from oopzbot.commands.oopz import is_oopz_music_command, normalize_oopz_music_command
from oopzbot.config import Settings
from oopzbot.domain.compat import command_result_to_legacy
from oopzbot.domain.contracts import CommandRequest, ErrorKind

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    "播放 歌曲",
    "b站 BV13x41117TL",
    "搜歌 歌曲",
    "选歌 1",
    "选择1",
    "榜单",
    "榜单点歌 1",
    "榜单批量 10",
    "专辑 名称",
    "专辑选择 1",
    "专辑加入 全部",
    "喜欢",
    "喜欢点歌 1",
    "喜欢列表",
    "状态",
    "队列",
    "暂停",
    "继续",
    "切歌",
    "停止",
    "音量 30",
    "删除 1",
    "清空队列",
    "移动 1 2",
)


class MusicUserAccessTests(unittest.TestCase):
    def setUp(self):
        self.settings = types.SimpleNamespace(
            qqbot_music_blocked_user_openids=frozenset({"qq-blocked"}),
            oopz_music_blocked_user_uids=frozenset({"oopz-blocked"}),
        )
        self.policy = patch.object(access, "get_settings", return_value=self.settings)
        self.policy.start()
        self.addCleanup(self.policy.stop)

    def test_blacklists_parse_separators_and_preserve_exact_ids(self):
        with patch.dict(
            os.environ,
            {
                "QQBOT_MUSIC_BLOCKED_USER_OPENIDS": " A, B，A\nCaseSensitive ",
                "OOPZ_MUSIC_BLOCKED_USER_UIDS": " oopz-1, oopz-2 ",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.qqbot_music_blocked_user_openids, {"A", "B", "CaseSensitive"})
        self.assertEqual(settings.oopz_music_blocked_user_uids, {"oopz-1", "oopz-2"})
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(Settings.from_env().qqbot_music_blocked_user_openids)
            self.assertFalse(Settings.from_env().oopz_music_blocked_user_uids)

    def test_platform_namespaces_exact_match_and_unknown_identity(self):
        self.assertTrue(music_user_blocked("qq", "qq-blocked"))
        self.assertTrue(music_user_blocked("http", "qq-blocked"))
        self.assertTrue(music_user_blocked("oopz", "oopz-blocked"))
        self.assertFalse(music_user_blocked("qq", "oopz-blocked"))
        self.assertFalse(music_user_blocked("oopz", "qq-blocked"))
        self.assertFalse(music_user_blocked("qq", "qq-blocked-extra"))
        for source in ("qq", "http", "oopz"):
            self.assertTrue(music_user_blocked(source, "anonymous"))
            self.assertTrue(music_user_blocked(source, ""))
        self.settings.qqbot_music_blocked_user_openids = frozenset()
        self.assertFalse(music_user_blocked("qq", "anonymous"))
        self.assertFalse(music_user_blocked("qq", "qq-blocked"))

    def test_every_blocked_command_is_rejected_before_runtime_and_config_resolution(self):
        for source, user in (
            ("http", "qq-blocked"),
            ("oopz", "oopz-blocked"),
            ("compat", "qq-blocked"),
        ):
            for command in COMMANDS:
                with (
                    self.subTest(source=source, command=command),
                    patch.object(bridge, "_music_handler") as handler,
                ):
                    result = bridge._execute_command_impl(
                        CommandRequest(command=command, source=source, requester_id=user)
                    )
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["code"], "forbidden")
                    self.assertEqual(result["message"], MUSIC_ACCESS_DENIED)
                    handler.assert_not_called()

    def test_allowed_user_keeps_playback_and_bot_account_is_not_the_requester(self):
        music = _RecordingPlaybackMusic()
        self.settings.oopz_music_blocked_user_uids = frozenset({"bot"})
        with (
            patch.object(bridge, "_music_handler", return_value=music),
            patch.object(
                bridge,
                "_command_config",
                return_value=("area", "text", "voice", "bot"),
            ),
        ):
            result = bridge._execute_command_impl(
                CommandRequest(command="播放 歌曲", source="http", requester_id="qq-allowed")
            )
        self.assertTrue(result["ok"])
        self.assertIsNotNone(music.play_args)

    def test_self_id_remains_available_without_music_or_voice(self):
        for source, user in (("http", "qq-blocked"), ("oopz", "oopz-blocked")):
            with self.subTest(source=source), patch.object(bridge, "_music_handler") as handler:
                result = bridge._execute_command_impl(
                    CommandRequest(command="我的ID", source=source, requester_id=user)
                )
                self.assertTrue(result["ok"])
                self.assertIn(user, result["message"])
                handler.assert_not_called()
        self.assertEqual(normalize_oopz_music_command("/myid"), "/myid")

    def test_authenticated_panel_session_has_independent_permissions(self):
        self.settings.qqbot_music_blocked_user_openids = frozenset({"panel-admin", "qq-blocked"})
        self.assertFalse(
            music_request_blocked(
                CommandRequest(
                    command="停止",
                    source="http",
                    requester_id="panel-admin",
                    group_openid="server-panel",
                )
            )
        )
        self.assertTrue(
            music_request_blocked(
                CommandRequest(
                    command="停止",
                    source="http",
                    requester_id="qq-blocked",
                    group_openid="server-panel",
                )
            )
        )
        self.assertTrue(
            music_request_blocked(
                CommandRequest(
                    command="停止",
                    source="http",
                    requester_id="panel-admin",
                    group_openid="real-qq-group",
                )
            )
        )

    def test_oopz_denial_is_replied_even_when_backend_normally_notifies(self):
        music = types.SimpleNamespace(notify_message=Mock(return_value=True))
        for command in ("播放 歌曲", "b站 BV13x41117TL", "下一首", "停止", "选歌 1", "喜欢"):
            with (
                self.subTest(command=command),
                patch.object(bridge, "_music_handler", return_value=music),
            ):
                music.notify_message.reset_mock()
                self.assertTrue(
                    bridge.dispatch_oopz_music_command(command, "text", "area", "oopz-blocked")
                )
                self.assertEqual(music.notify_message.call_count, 1)
                self.assertEqual(music.notify_message.call_args.kwargs["text"], MUSIC_ACCESS_DENIED)

    def test_denied_stop_does_not_read_or_modify_queue_and_has_typed_error(self):
        with patch.object(bridge, "_music_handler") as handler:
            result = bridge._execute_request(
                CommandRequest(command="停止", source="http", requester_id="qq-blocked")
            )
            handler.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.error.kind, ErrorKind.FORBIDDEN)
        self.assertEqual(command_result_to_legacy(result)["code"], "forbidden")

    def test_compatibility_entry_does_not_confuse_group_key_with_user_id(self):
        with patch.object(bridge, "_music_handler") as handler:
            result = bridge._execute_command("播放 歌曲", "group:qq-blocked")
            self.assertEqual(result["code"], "forbidden")
            handler.assert_not_called()
        path = ROOT / "legacy_oopzbot/src/web/qqbot_bridge_api.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        method = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_execute_command"
        )
        namespace = dict(
            CommandKind=bridge.CommandKind,
            exact_command_kind=bridge.exact_command_kind,
            music_user_blocked=music_user_blocked,
            MUSIC_ACCESS_DENIED=MUSIC_ACCESS_DENIED,
            _music_handler=Mock(),
        )
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                str(path),
                "exec",
            ),
            namespace,
        )
        result = namespace["_execute_command"]("停止", "group:qq-blocked")
        self.assertEqual(result["code"], "forbidden")
        namespace["_music_handler"].assert_not_called()

    def test_legacy_routers_reject_aliases_before_plugins_or_fallback_handlers(self):
        aliases = (
            "/next ignored",
            "/bf qq 歌曲",
            "/like list ignored",
            "/queue ignored",
            "/enter voice",
            "音量 30",
            "清空队列",
            "最近播放",
        )
        for alias in aliases:
            self.assertTrue(is_oopz_music_command(alias), alias)
        self.assertFalse(is_oopz_music_command("我的ID"))
        self.assertFalse(is_oopz_music_command("每日一句"))
        for filename, class_name in (
            ("mention_command_router.py", "MentionCommandRouter"),
            ("slash_command_router.py", "SlashCommandRouter"),
        ):
            path = ROOT / "legacy_oopzbot/src/app/services/routing" / filename
            tree = ast.parse(path.read_text(encoding="utf-8"))
            cls = next(
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            )
            method = next(
                node
                for node in cls.body
                if isinstance(node, ast.FunctionDef) and node.name == "dispatch"
            )
            namespace = dict(
                is_oopz_music_command=is_oopz_music_command,
                music_user_blocked=music_user_blocked,
                MUSIC_ACCESS_DENIED=MUSIC_ACCESS_DENIED,
            )
            exec(
                compile(
                    ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                    str(path),
                    "exec",
                ),
                namespace,
            )
            router = types.SimpleNamespace(_sender=Mock(), _plugins=Mock(), _runtime=Mock())
            for alias in aliases:
                with self.subTest(router=filename, alias=alias):
                    router._sender.reset_mock()
                    namespace["dispatch"](router, alias, "text", "area", "oopz-blocked")
                    router._plugins.try_dispatch_mention.assert_not_called()
                    router._plugins.try_dispatch_slash.assert_not_called()
                    router._sender.send_message.assert_called_once_with(
                        MUSIC_ACCESS_DENIED, channel="text", area="area"
                    )


class QQUserAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_whitelist_is_checked_before_user_access_and_identity_queries(self):
        client = object.__new__(service.OopzQQClient)
        api = _FakeGroupAPI()
        message = _FakeGroupMessage(
            api, message_id=f"wrong-group-{time.monotonic_ns()}", content="我的ID"
        )
        with (
            patch.object(service, "ALLOWED_GROUPS", {"another-group"}),
            patch.object(service, "_forward_command") as forward,
        ):
            await client.on_group_at_message_create(message)
            forward.assert_not_called()
            self.assertFalse(api.calls)

    async def test_blocked_sender_never_forwards_command_or_joins_voice(self):
        client = object.__new__(service.OopzQQClient)
        for index, command in enumerate(("播放 歌曲", "选歌 1", "停止", "删除 1", "音量 90")):
            api = _FakeGroupAPI()
            message = _FakeGroupMessage(
                api, message_id=f"blocked-{index}-{time.monotonic_ns()}", content=command
            )
            with (
                patch.object(service, "music_user_blocked", return_value=True),
                patch.object(service, "_forward_command") as forward,
            ):
                await client.on_group_at_message_create(message)
                forward.assert_not_called()
                self.assertEqual(api.calls[0]["content"], MUSIC_ACCESS_DENIED)

    async def test_self_id_forwards_the_actual_qq_sender_even_if_blocked(self):
        client = object.__new__(service.OopzQQClient)
        api = _FakeGroupAPI()
        message = _FakeGroupMessage(
            api, message_id=f"self-id-{time.monotonic_ns()}", content="我的ID"
        )
        with (
            patch.object(service, "music_user_blocked", return_value=True),
            patch.object(
                service,
                "_forward_command",
                return_value={"ok": True, "message": "你的QQ 用户 OpenID：user-1"},
            ) as forward,
        ):
            await client.on_group_at_message_create(message)
            self.assertEqual(forward.call_args.args[1], "user-1")
            self.assertIn("user-1", api.calls[0]["content"])

    async def test_jm_keeps_its_existing_permission_path(self):
        client = object.__new__(service.OopzQQClient)
        api = _FakeGroupAPI()
        message = _FakeGroupMessage(
            api, message_id=f"jm-{time.monotonic_ns()}", content="JM 123456"
        )
        from unittest.mock import AsyncMock

        with (
            patch.object(service, "music_user_blocked", return_value=True),
            patch.object(client, "_start_jm_batch", new_callable=AsyncMock) as start,
        ):
            await client.on_group_at_message_create(message)
            start.assert_awaited_once()
