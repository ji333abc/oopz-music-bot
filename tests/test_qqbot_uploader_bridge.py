from __future__ import annotations

import importlib
import sys
import time
import types
import unittest
from unittest.mock import patch


def _load_service_module():
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
    return importlib.import_module("oopzbot.qqbot")


service = _load_service_module()


class _FakeGroupAPI:
    def __init__(self, first_error: Exception | None = None) -> None:
        self.first_error = first_error
        self.calls: list[dict] = []
        self.file_calls: list[dict] = []

    async def post_group_message(self, **payload) -> None:
        self.calls.append(payload)
        if self.first_error is not None:
            error = self.first_error
            self.first_error = None
            raise error

    async def post_group_file(self, **payload) -> dict:
        self.file_calls.append(payload)
        return {"file_info": "unused"}


class _FakeGroupMessage:
    def __init__(
        self,
        api: _FakeGroupAPI,
        *,
        message_id: str = "message-1",
        content: str = "",
    ) -> None:
        self._api = api
        self.group_openid = "group-1"
        self.id = message_id
        self.content = content
        self.author = types.SimpleNamespace(
            member_openid="user-1",
            id="user-1",
            username="tester",
        )


class GroupReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_oopz_inline_cover_marker_is_removed_from_qq_text(self) -> None:
        api = _FakeGroupAPI()
        message = _FakeGroupMessage(api)
        client = object.__new__(service.OopzQQClient)

        await client._reply(
            message,
            "![IMAGEw300h300](/im/cover.jpeg)\nMusic-bot 点播了:\n歌曲: 半壶纱",
        )

        self.assertEqual(
            api.calls[0]["content"],
            "Music-bot 点播了:\n歌曲: 半壶纱",
        )

    async def test_selected_song_reply_does_not_upload_cover_to_qq(self) -> None:
        api = _FakeGroupAPI()
        message = _FakeGroupMessage(api)
        client = object.__new__(service.OopzQQClient)

        await client._reply_song_selected(
            message,
            {
                "song": {
                    "name": "半壶纱",
                    "artists": "刘珂矣",
                    "album": "半壶纱",
                    "duration": "3:41",
                    "cover": "https://example.invalid/cover.jpg",
                }
            },
        )

        self.assertEqual(api.file_calls, [])
        self.assertEqual(api.calls[0]["msg_type"], 2)
        self.assertNotIn("media", api.calls[0])

    async def test_expired_reply_falls_back_to_active_group_message(self) -> None:
        api = _FakeGroupAPI(RuntimeError("msgid已经过期,不能回复"))
        message = _FakeGroupMessage(api)
        client = object.__new__(service.OopzQQClient)

        await client._reply(message, "done", msg_seq=3)

        self.assertEqual(len(api.calls), 2)
        self.assertEqual(api.calls[0]["msg_id"], "message-1")
        self.assertNotIn("msg_id", api.calls[1])
        self.assertIsInstance(api.calls[1]["msg_seq"], int)
        self.assertNotEqual(api.calls[0]["msg_seq"], api.calls[1]["msg_seq"])
        self.assertEqual(api.calls[1]["content"], "done")

    async def test_other_reply_errors_are_not_retried(self) -> None:
        api = _FakeGroupAPI(RuntimeError("permission denied"))
        message = _FakeGroupMessage(api)
        client = object.__new__(service.OopzQQClient)

        with self.assertRaisesRegex(RuntimeError, "permission denied"):
            await client._reply(message, "done")

        self.assertEqual(len(api.calls), 1)

    async def test_reply_limit_falls_back_to_active_group_message(self) -> None:
        api = _FakeGroupAPI(RuntimeError("回复次数已达上限"))
        message = _FakeGroupMessage(api)
        client = object.__new__(service.OopzQQClient)

        await client._reply(message, "done", msg_seq=5)

        self.assertEqual(len(api.calls), 2)
        self.assertNotIn("msg_id", api.calls[1])
        self.assertIsInstance(api.calls[1]["msg_seq"], int)

    async def test_timeout_retries_passive_reply_with_new_sequence(self) -> None:
        api = _FakeGroupAPI(TimeoutError("请求超时"))
        message = _FakeGroupMessage(api)
        client = object.__new__(service.OopzQQClient)

        await client._reply(message, "done")

        self.assertEqual(len(api.calls), 2)
        self.assertEqual(api.calls[0]["msg_id"], "message-1")
        self.assertEqual(api.calls[1]["msg_id"], "message-1")
        self.assertNotEqual(api.calls[0]["msg_seq"], api.calls[1]["msg_seq"])

    async def test_deduplication_retries_passive_reply_with_new_sequence(self) -> None:
        api = _FakeGroupAPI(RuntimeError("40054005 消息被去重，请检查请求msgseq"))
        message = _FakeGroupMessage(api)
        client = object.__new__(service.OopzQQClient)

        await client._reply(message, "done")

        self.assertEqual(len(api.calls), 2)
        self.assertEqual(api.calls[0]["msg_id"], "message-1")
        self.assertEqual(api.calls[1]["msg_id"], "message-1")
        self.assertNotEqual(api.calls[0]["msg_seq"], api.calls[1]["msg_seq"])

    async def test_proactive_reply_uses_unique_sequence_without_msg_id(self) -> None:
        api = _FakeGroupAPI()
        message = _FakeGroupMessage(api)
        client = object.__new__(service.OopzQQClient)

        await client._reply(message, "one", proactive=True)
        await client._reply(message, "two", proactive=True)

        self.assertNotIn("msg_id", api.calls[0])
        self.assertNotIn("msg_id", api.calls[1])
        self.assertNotEqual(api.calls[0]["msg_seq"], api.calls[1]["msg_seq"])

    async def test_proactive_permission_failure_is_not_retried(self) -> None:
        api = _FakeGroupAPI(RuntimeError("主动消息失败, 无权限"))
        message = _FakeGroupMessage(api)
        client = object.__new__(service.OopzQQClient)

        with self.assertRaisesRegex(RuntimeError, "无权限"):
            await client._reply(message, "done", proactive=True)

        self.assertEqual(len(api.calls), 1)

    async def test_passive_replies_also_use_unique_sequences(self) -> None:
        api = _FakeGroupAPI()
        message = _FakeGroupMessage(api)
        client = object.__new__(service.OopzQQClient)

        await client._reply(message, "one", msg_seq=1)
        await client._reply(message, "two", msg_seq=1)

        self.assertEqual(api.calls[0]["msg_id"], "message-1")
        self.assertEqual(api.calls[1]["msg_id"], "message-1")
        self.assertNotEqual(api.calls[0]["msg_seq"], api.calls[1]["msg_seq"])

    async def test_slow_bridge_only_sends_final_passive_result(self) -> None:
        api = _FakeGroupAPI()
        message = _FakeGroupMessage(
            api,
            message_id=f"slow-{time.monotonic_ns()}",
            content="播放 测试歌曲",
        )
        client = object.__new__(service.OopzQQClient)

        def slow_command(*_args) -> dict:
            time.sleep(0.05)
            return {"ok": True, "message": "已点歌：测试歌曲"}

        with (
            patch.object(service, "COMMAND_DEFER_SECONDS", 0.01),
            patch.object(service, "_forward_command", side_effect=slow_command),
        ):
            await client.on_group_at_message_create(message)
            for _ in range(50):
                if api.calls:
                    break
                await __import__("asyncio").sleep(0.01)

        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.calls[0]["content"], "已点歌：测试歌曲")
        self.assertEqual(api.calls[0]["msg_id"], message.id)


class JMCommandParserTests(unittest.TestCase):
    def test_parses_single_and_multiple_album_ids(self) -> None:
        self.assertEqual(service._parse_jm_album_ids("JM 111111"), ["111111"])
        self.assertEqual(
            service._parse_jm_album_ids("JM 111111 222222 333333"),
            ["111111", "222222", "333333"],
        )

    def test_removes_duplicate_ids_without_reordering(self) -> None:
        self.assertEqual(
            service._parse_jm_album_ids("jm下载 222 111 222"),
            ["222", "111"],
        )

    def test_rejects_unrelated_or_malformed_commands(self) -> None:
        self.assertEqual(service._parse_jm_album_ids("播放 111111"), [])
        self.assertEqual(service._parse_jm_album_ids("JM 123,456"), [])





if __name__ == "__main__":
    unittest.main()
