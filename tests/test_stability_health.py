from __future__ import annotations

import asyncio
import json
import sys
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

    class _FakeJSONResponse:
        def __init__(self, content, status_code=200):
            self.body = json.dumps(content, ensure_ascii=False).encode("utf-8")
            self.status_code = status_code

    fastapi_module.APIRouter = _FakeRouter
    fastapi_module.Request = object
    responses_module.JSONResponse = _FakeJSONResponse
    sys.modules["fastapi"] = fastapi_module
    sys.modules["fastapi.responses"] = responses_module

from oopzbot import bridge
from oopzbot.operations import COMPONENT_STATUSES, OperationsRegistry


class ComponentStatusTests(unittest.TestCase):
    def test_component_statuses_are_finite_and_reasons_are_bounded(self) -> None:
        registry = OperationsRegistry()
        reason = "line one\nline two\t" + ("x" * 400)

        registry.set_component("test", "degraded", reason)

        component = registry.snapshot()["components"]["test"]
        self.assertIn(component["status"], COMPONENT_STATUSES)
        self.assertEqual(component["reason"], component["message"])
        self.assertLessEqual(len(component["reason"]), 240)
        self.assertNotRegex(component["reason"], r"[\r\n\t]")

    def test_unknown_component_status_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            OperationsRegistry().set_component("test", "broken-state", "bad")


class HealthSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_dependency = bridge._music_dependency
        bridge._music_dependency = None

    def tearDown(self) -> None:
        bridge._music_dependency = self.previous_dependency

    def test_snapshot_has_separate_runtime_components(self) -> None:
        runtime = types.SimpleNamespace(
            ready=True,
            _voice_started=True,
            _closed=types.SimpleNamespace(is_set=lambda: False),
        )
        music = types.SimpleNamespace(
            runtime=runtime,
            queue=types.SimpleNamespace(_redis=object()),
            _voice_channel_id="voice",
        )
        with patch.object(bridge, "_music_dependency", music):
            with patch.object(bridge.requests, "get") as request:
                request.return_value.raise_for_status.return_value = None
                snapshot = bridge._service_health(music)

        self.assertEqual(
            set(("internal_api", "legacy_core", "redis", "qqmusic", "oopz_websocket", "oopz_voice", "qq_bot")),
            set(snapshot).intersection(
                {"internal_api", "legacy_core", "redis", "qqmusic", "oopz_websocket", "oopz_voice", "qq_bot"}
            ),
        )
        self.assertEqual(snapshot["internal_api"]["status"], "ok")
        self.assertEqual(snapshot["legacy_core"]["status"], "ok")
        self.assertEqual(snapshot["oopz_websocket"]["status"], "ok")
        self.assertEqual(snapshot["oopz_voice"]["status"], "ok")
        self.assertEqual(snapshot["redis"]["status"], "ok")
        self.assertEqual(snapshot["qqmusic"]["status"], "ok")

    def test_redis_memory_fallback_is_degraded(self) -> None:
        runtime = types.SimpleNamespace(ready=True, _voice_started=False)
        # A real class is needed because __class__ cannot be overridden by an instance attribute.
        memory_redis = type("_InMemoryRedis", (), {})()
        music = types.SimpleNamespace(runtime=runtime, queue=types.SimpleNamespace(_redis=memory_redis))

        snapshot = bridge._service_health(music)

        self.assertEqual(snapshot["redis"]["status"], "degraded")

    def test_readyz_is_not_ready_without_the_music_handler(self) -> None:
        with patch.object(bridge.requests, "get", side_effect=OSError("offline")):
            response = asyncio.run(bridge.readyz())

        status_code = getattr(response, "status_code", 503)
        payload = (
            json.loads(response.body)
            if hasattr(response, "body")
            else response
        )
        self.assertEqual(status_code, 503)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["components"]["internal_api"]["status"], "ok")

    def test_healthz_is_live_without_external_dependencies(self) -> None:
        with patch.object(bridge.requests, "get", side_effect=AssertionError("network call")):
            result = asyncio.run(bridge.healthz())

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
