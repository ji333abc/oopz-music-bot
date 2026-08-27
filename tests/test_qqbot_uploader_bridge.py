from __future__ import annotations

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path


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

    async def post_group_message(self, **payload) -> None:
        self.calls.append(payload)
        if self.first_error is not None:
            error = self.first_error
            self.first_error = None
            raise error


class _FakeGroupMessage:
    def __init__(self, api: _FakeGroupAPI) -> None:
        self._api = api
        self.group_openid = "group-1"
        self.id = "message-1"


class GroupReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_reply_falls_back_to_active_group_message(self) -> None:
        api = _FakeGroupAPI(RuntimeError("msgid已经过期,不能回复"))
        message = _FakeGroupMessage(api)
        client = object.__new__(service.OopzQQClient)

        await client._reply(message, "done", msg_seq=3)

        self.assertEqual(len(api.calls), 2)
        self.assertEqual(api.calls[0]["msg_id"], "message-1")
        self.assertEqual(api.calls[0]["msg_seq"], 3)
        self.assertNotIn("msg_id", api.calls[1])
        self.assertNotIn("msg_seq", api.calls[1])
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
        self.assertNotIn("msg_seq", api.calls[1])


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


class UploadBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.archive = self.root / "JM123.zip"
        self.archive.write_bytes(b"zip-test")
        self.original_node = service.JM_NODE
        self.original_uploader = service.JM_UPLOADER
        self.original_timeout = service.JM_UPLOAD_TIMEOUT_SECONDS
        service.JM_NODE = sys.executable

    async def asyncTearDown(self) -> None:
        service.JM_NODE = self.original_node
        service.JM_UPLOADER = self.original_uploader
        service.JM_UPLOAD_TIMEOUT_SECONDS = self.original_timeout
        self.temp_dir.cleanup()

    def _write_fake_uploader(self, body: str) -> None:
        script = self.root / "fake_uploader.py"
        script.write_text(body, encoding="utf-8")
        service.JM_UPLOADER = str(script)

    async def test_success_result_is_returned(self) -> None:
        payload = {"ok": True, "fileUuid": "uuid-1", "ttl": 60}
        self._write_fake_uploader(
            "import json, sys\n"
            "print('progress 1/1', file=sys.stderr)\n"
            f"print(json.dumps({payload!r}))\n"
        )

        result = await service._run_jm_upload(
            self.archive,
            "group-1",
            "message-1",
            "JM123.zip",
        )

        self.assertEqual(result, payload)

    async def test_structured_failure_becomes_upload_error(self) -> None:
        payload = {
            "ok": False,
            "errorType": "quota",
            "message": "daily limit",
        }
        self._write_fake_uploader(
            "import json, sys\n"
            f"print(json.dumps({payload!r}))\n"
            "sys.exit(1)\n"
        )

        with self.assertRaises(service.JMUploadError) as raised:
            await service._run_jm_upload(
                self.archive,
                "group-1",
                "message-1",
                "JM123.zip",
            )

        self.assertEqual(raised.exception.error_type, "quota")

    async def test_timeout_kills_uploader(self) -> None:
        self._write_fake_uploader("import time\ntime.sleep(10)\n")
        service.JM_UPLOAD_TIMEOUT_SECONDS = 0.05

        with self.assertRaises(service.JMUploadError) as raised:
            await service._run_jm_upload(
                self.archive,
                "group-1",
                "message-1",
                "JM123.zip",
            )

        self.assertEqual(raised.exception.error_type, "timeout")


class JMEstimateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_timing_path = service.JM_TIMING_PATH
        service.JM_TIMING_PATH = Path(self.temp_dir.name) / "jm_timing.json"

    def tearDown(self) -> None:
        service.JM_TIMING_PATH = self.original_timing_path
        self.temp_dir.cleanup()

    def test_default_estimate_is_rounded_up(self) -> None:
        self.assertEqual(service._estimate_jm_seconds(410), 260)

    def test_completed_job_calibrates_future_estimates(self) -> None:
        service._record_jm_timing(
            {
                "page_count": 100,
                "download_seconds": 10,
                "processing_seconds": 20,
                "upload_seconds": 5,
                "archive_bytes": 10 * 1024 * 1024,
                "total_seconds": 35.5,
            }
        )

        self.assertEqual(service._estimate_jm_seconds(200), 110)

    def test_small_sample_only_partially_reweights_defaults(self) -> None:
        service._record_jm_timing(
            {
                "page_count": 20,
                "download_seconds": 11.829,
                "processing_seconds": 3.409,
                "upload_seconds": 9.431,
                "archive_bytes": 10_341_819,
                "total_seconds": 25.067,
            }
        )

        self.assertEqual(service._estimate_jm_seconds(20), 30)


if __name__ == "__main__":
    unittest.main()
