from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_queue_manager():
    root = Path(__file__).resolve().parents[1]
    legacy_src = root / "legacy_oopzbot" / "src"
    legacy_root = root / "legacy_oopzbot"
    for path in (str(legacy_src), str(legacy_root)):
        if path not in sys.path:
            sys.path.insert(0, path)

    fake_redis = types.ModuleType("redis")
    fake_redis.Redis = lambda **_kwargs: None
    module_path = legacy_src / "core" / "queue_manager.py"
    spec = importlib.util.spec_from_file_location("test_queue_manager_runtime", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"redis": fake_redis}):
        spec.loader.exec_module(module)
    return module


queue_manager = _load_queue_manager()


class RedisFailoverTests(unittest.TestCase):
    def setUp(self) -> None:
        queue_manager._redis_client = None
        queue_manager._last_redis_retry = 0.0

    def tearDown(self) -> None:
        queue_manager._redis_client = None
        queue_manager._last_redis_retry = 0.0

    def test_startup_failure_uses_memory_queue(self) -> None:
        with patch.object(queue_manager, "_try_connect_redis", return_value=None):
            client = queue_manager.get_redis_client()

        self.assertIsInstance(client, queue_manager._InMemoryRedis)

    def test_socket_timeout_exceeds_the_blocking_queue_poll(self) -> None:
        captured = {}
        healthy = types.SimpleNamespace(ping=lambda: True)

        def build_client(**kwargs):
            captured.update(kwargs)
            return healthy

        with patch.object(queue_manager.redis, "Redis", side_effect=build_client):
            self.assertIs(queue_manager._try_connect_redis(), healthy)

        self.assertGreater(float(captured["socket_timeout"]), 2.0)

    def test_runtime_disconnect_switches_to_memory_queue(self) -> None:
        broken = types.SimpleNamespace(
            ping=lambda: (_ for _ in ()).throw(ConnectionError("offline"))
        )
        queue_manager._redis_client = broken

        client = queue_manager.get_redis_client()

        self.assertIsInstance(client, queue_manager._InMemoryRedis)

    def test_memory_queue_recovers_to_real_redis(self) -> None:
        memory = queue_manager._InMemoryRedis()
        recovered = types.SimpleNamespace(ping=lambda: True)
        queue_manager._redis_client = memory
        queue_manager._last_redis_retry = 0.0

        with patch.object(queue_manager, "_try_connect_redis", return_value=recovered):
            client = queue_manager.get_redis_client()

        self.assertIs(client, recovered)

    def test_recovery_does_not_discard_nonempty_memory_queue(self) -> None:
        memory = queue_manager._InMemoryRedis()
        memory.rpush("music:queue", '{"song_id":"1"}')
        recovered = types.SimpleNamespace(ping=lambda: True)
        queue_manager._redis_client = memory
        queue_manager._last_redis_retry = 0.0

        with patch.object(queue_manager, "_try_connect_redis", return_value=recovered):
            client = queue_manager.get_redis_client()

        self.assertIs(client, memory)
        self.assertEqual(memory.llen("music:queue"), 1)

    def test_memory_adapter_batch_remove_is_atomic_and_one_based(self) -> None:
        memory = queue_manager._InMemoryRedis()
        queue_manager._redis_client = memory
        queue_manager._last_redis_retry = float("inf")
        manager = queue_manager.QueueManager()
        for index in range(1, 5):
            memory.rpush("music:queue", json.dumps({"song_id": str(index)}))

        with self.assertRaises(IndexError):
            manager.remove_positions([1, 9])
        self.assertEqual(manager.get_queue_length(), 4)

        removed = manager.remove_positions([4, 2, 2])
        self.assertEqual([item["song_id"] for item in removed], ["2", "4"])
        self.assertEqual(
            [item["song_id"] for item in manager.get_queue()],
            ["1", "3"],
        )


if __name__ == "__main__":
    unittest.main()
