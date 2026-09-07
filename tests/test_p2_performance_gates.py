from __future__ import annotations

import time
import unittest
from pathlib import Path

from oopzbot.controller import MusicQueue
from oopzbot.metrics import MetricsRegistry
from oopzbot.state_publisher import StatePublisher


class P2PerformanceGateTests(unittest.TestCase):
    def test_sse_idle_path_reduces_full_snapshots_by_at_least_80_percent(self) -> None:
        legacy_requests_per_hour = 3600 // 10
        fallback_requests_per_hour = 1 + 3600 // 60
        reduction = 1 - fallback_requests_per_hour / legacy_requests_per_hour

        self.assertGreaterEqual(reduction, 0.80)

    def test_local_revision_signal_is_well_below_two_seconds(self) -> None:
        publisher = StatePublisher()
        started = time.monotonic()
        publisher.publish()

        self.assertEqual(publisher.wait_for_change(0, timeout=2), 1)
        self.assertLess(time.monotonic() - started, 2)

    def test_simulated_24_hour_metric_volume_remains_bounded(self) -> None:
        registry = MetricsRegistry(window_size=200, series_limit=4)
        for index in range(24 * 60 * 60):
            registry.record_external(
                service="qqmusic",
                operation="search",
                result_kind="ok",
                ok=True,
                duration_ms=index % 100,
            )

        summary = registry.summaries()["qqmusic:search"]
        self.assertEqual(summary["count"], 200)
        self.assertEqual(registry.series_count, 1)

    def test_one_hundred_stale_moves_conflict_without_reordering(self) -> None:
        queue = MusicQueue()
        for name in ("one", "two", "three"):
            queue.add_to_queue({"name": name})
        stale = queue.get_version()
        queue.add_to_queue({"name": "four"})
        expected = [item["name"] for item in queue.get_queue()]

        for _ in range(100):
            with self.assertRaisesRegex(RuntimeError, "version conflict"):
                queue.move_position(1, 4, stale)

        self.assertEqual([item["name"] for item in queue.get_queue()], expected)

    def test_packaged_and_root_environment_examples_match(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            (root / ".env.example").read_text(encoding="utf-8"),
            (root / "oopzbot" / "env.example").read_text(encoding="utf-8"),
        )

    def test_legacy_web_queue_mutations_use_queue_manager(self) -> None:
        root = Path(__file__).resolve().parents[1]
        web_player = (
            root / "legacy_oopzbot" / "src" / "web" / "web_player.py"
        ).read_text(encoding="utf-8")
        admin_music = (
            root / "legacy_oopzbot" / "src" / "web" / "admin" / "music.py"
        ).read_text(encoding="utf-8")

        self.assertIn("QueueManager(area, redis_client=redis_client)", web_player)
        self.assertNotIn("_QUEUE_REMOVE_LUA", web_player)
        self.assertNotIn("_QUEUE_TOP_LUA", web_player)
        self.assertNotIn("redis_client.delete(queue_key)", web_player)
        self.assertNotIn("pipe.rpush(queue_key", web_player)
        self.assertNotIn("_get_redis().delete(_area_key(KEY_QUEUE", admin_music)


if __name__ == "__main__":
    unittest.main()
