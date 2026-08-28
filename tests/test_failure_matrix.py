from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from oopzbot.config import Settings
from oopzbot.controller import MusicController
from oopzbot.music import QQMusic
from oopzbot.operations import OperationsRegistry


def _load_qqbot_module():
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


qqbot = _load_qqbot_module()


class QQMusicFailureTests(unittest.TestCase):
    def test_http_404_and_timeout_are_user_facing_music_errors(self) -> None:
        client = QQMusic.__new__(QQMusic)
        client.base_url = "http://fake-qqmusic"
        client.cookie = ""

        class NotFoundResponse:
            status_code = 404

            def raise_for_status(self):
                raise requests.HTTPError("fake 404", response=self)

            def json(self):
                return {}

        client._session = types.SimpleNamespace(get=lambda *args, **kwargs: NotFoundResponse())
        not_found = client.summarize("missing-song")

        self.assertEqual(not_found["code"], "error")
        self.assertIn("HTTP 404", not_found["message"])

        client._session = types.SimpleNamespace(
            get=lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout("fake timeout"))
        )
        timeout_result = client.summarize("timeout-song")
        self.assertEqual(timeout_result["code"], "error")
        self.assertIn("超时", timeout_result["message"])

    def test_empty_play_url_is_not_reported_as_a_success(self) -> None:
        client = QQMusic.__new__(QQMusic)
        client.quality = "320"
        client.fallback_quality = "128"
        client._get = lambda *_args, **_kwargs: {"data": {"url": ""}}

        self.assertIsNone(client.get_song_url("empty-url"))

    def test_cookie_rejection_is_reported_as_http_auth_failure(self) -> None:
        client = QQMusic.__new__(QQMusic)
        client.base_url = "http://fake-qqmusic"
        client.cookie = "expired-cookie"

        class UnauthorizedResponse:
            status_code = 401

            def raise_for_status(self):
                raise requests.HTTPError("fake 401", response=self)

            def json(self):
                return {}

        client._session = types.SimpleNamespace(
            get=lambda *_args, **_kwargs: UnauthorizedResponse()
        )

        result = client.summarize("login-only-song")

        self.assertEqual(result["code"], "error")
        self.assertIn("HTTP 401", result["message"])


class JMFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_failure_releases_the_task_lock(self) -> None:
        original_root = qqbot.JM_TEMP_ROOT
        with tempfile.TemporaryDirectory() as temporary:
            qqbot.JM_TEMP_ROOT = Path(temporary)
            acquired = qqbot._jm_job_lock.acquire(blocking=False)
            self.assertTrue(acquired)
            try:
                result = await qqbot.OopzQQClient._run_jm_job(
                    object.__new__(qqbot.OopzQQClient),
                    types.SimpleNamespace(group_openid="group", id="message"),
                    "123456",
                    send_result=False,
                )
            finally:
                # The job itself must release the lock; this fallback only protects the test.
                if qqbot._jm_job_lock.locked():
                    qqbot._jm_job_lock.release()
                qqbot.JM_TEMP_ROOT = original_root

        self.assertFalse(result["ok"])
        self.assertTrue(qqbot._jm_job_lock.acquire(blocking=False))
        qqbot._jm_job_lock.release()

    async def test_oversized_archive_releases_lock_and_returns_structured_error(self) -> None:
        original_root = qqbot.JM_TEMP_ROOT
        original_limit = qqbot.JM_MAX_BYTES
        original_retain = qqbot.JM_FAILURE_RETAIN_SECONDS
        with tempfile.TemporaryDirectory() as temporary:
            qqbot.JM_TEMP_ROOT = Path(temporary)
            qqbot.JM_MAX_BYTES = 1
            qqbot.JM_FAILURE_RETAIN_SECONDS = 0

            def oversized_download(_album_id, _password, job_dir):
                job_dir.mkdir(parents=True)
                archive = job_dir / "archive.zip"
                archive.write_bytes(b"too-large")
                return archive, {"page_count": 1}

            qqbot._run_jm_download = oversized_download
            self.assertTrue(qqbot._jm_job_lock.acquire(blocking=False))
            try:
                result = await qqbot.OopzQQClient._run_jm_job(
                    object.__new__(qqbot.OopzQQClient),
                    types.SimpleNamespace(group_openid="group", id="message"),
                    "123456",
                    send_result=False,
                )
                await asyncio.sleep(0.01)
            finally:
                if qqbot._jm_job_lock.locked():
                    qqbot._jm_job_lock.release()
                qqbot.JM_TEMP_ROOT = original_root
                qqbot.JM_MAX_BYTES = original_limit
                qqbot.JM_FAILURE_RETAIN_SECONDS = original_retain

        self.assertFalse(result["ok"])
        self.assertIn("超过", result["error"])
        self.assertTrue(qqbot._jm_job_lock.acquire(blocking=False))
        qqbot._jm_job_lock.release()

    async def asyncSetUp(self) -> None:
        self.download = qqbot._run_jm_download
        qqbot._run_jm_download = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("fake download failed")
        )

    async def asyncTearDown(self) -> None:
        qqbot._run_jm_download = self.download


class OopzFailureTests(unittest.TestCase):
    def test_voice_join_failure_returns_a_structured_result(self) -> None:
        runtime = types.SimpleNamespace(
            ready=True,
            join_voice=lambda *_args: (_ for _ in ()).throw(RuntimeError("fake voice offline")),
            leave_voice=lambda: None,
        )
        settings = Settings(
            qqbot_app_id="app",
            qqbot_app_secret="secret",
            bridge_token="bridge",
            bridge_host="127.0.0.1",
            bridge_port=18080,
            oopz_area_id="area",
            oopz_text_channel_id="text",
            oopz_voice_channel_id="voice",
            oopz_person_uid="bot",
            qq_music_enabled=False,
            qq_music_managed=False,
            qq_music_base_url="",
            qq_music_service_dir=".services/qqmusic-api",
            qq_music_cookie="",
            qq_music_quality="320",
            qq_music_fallback_quality="128",
            log_level="INFO",
        )
        controller = MusicController(settings, runtime)
        self.addCleanup(controller._closed.set)

        result = controller.enter_voice_channel("voice", "area")

        self.assertEqual(result["error"], "fake voice offline")

    def test_voice_timeout_returns_a_structured_result(self) -> None:
        runtime = types.SimpleNamespace(
            ready=True,
            join_voice=lambda *_args: (_ for _ in ()).throw(
                TimeoutError("voice startup timed out")
            ),
            leave_voice=lambda: None,
        )
        settings = Settings(
            qqbot_app_id="app",
            qqbot_app_secret="secret",
            bridge_token="bridge",
            bridge_host="127.0.0.1",
            bridge_port=18080,
            oopz_area_id="area",
            oopz_text_channel_id="text",
            oopz_voice_channel_id="voice",
            oopz_person_uid="bot",
            qq_music_enabled=False,
            qq_music_managed=False,
            qq_music_base_url="",
            qq_music_service_dir=".services/qqmusic-api",
            qq_music_cookie="",
            qq_music_quality="320",
            qq_music_fallback_quality="128",
            log_level="INFO",
        )
        controller = MusicController(settings, runtime)
        self.addCleanup(controller._closed.set)

        result = controller.enter_voice_channel("voice", "area")

        self.assertEqual(result["error"], "voice startup timed out")


class StateSafetyTests(unittest.TestCase):
    def test_component_reason_is_redacted_before_panel_persistence(self) -> None:
        with patch.dict(os.environ, {"QQBOT_BRIDGE_TOKEN": "bridge-secret-value"}, clear=False):
            registry = OperationsRegistry()
            registry.set_component("bridge", "error", "failed token=bridge-secret-value")

        reason = registry.snapshot()["components"]["bridge"]["reason"]
        self.assertNotIn("bridge-secret-value", reason)


if __name__ == "__main__":
    unittest.main()
