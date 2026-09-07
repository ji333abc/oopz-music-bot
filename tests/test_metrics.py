from __future__ import annotations

import ast
import unittest
from pathlib import Path

from oopzbot.metrics import (
    BoundedWindow,
    ExternalCallResult,
    LatencyWindow,
    MetricsRegistry,
)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds

    def timestamp(self) -> str:
        return f"fake-{self.value:.3f}"


class MetricsContractTests(unittest.TestCase):
    def test_module_has_no_framework_or_network_imports(self) -> None:
        source = (Path(__file__).parents[1] / "oopzbot" / "metrics.py").read_text(
            encoding="utf-8"
        )
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            str(node.module or "").split(".", 1)[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
        )
        self.assertTrue(
            imported_roots.isdisjoint(
                {"fastapi", "redis", "requests", "uvicorn", "panel"}
            )
        )

    def test_bounded_window_discards_oldest_items(self) -> None:
        window = BoundedWindow[int](3)
        for value in range(6):
            window.append(value)

        self.assertEqual(window.snapshot(), (3, 4, 5))
        self.assertEqual(len(window), 3)

    def test_latency_summary_defines_empty_and_nearest_rank_percentiles(self) -> None:
        window = LatencyWindow(10)
        self.assertEqual(window.summary().count, 0)
        self.assertIsNone(window.summary().p95_ms)

        for duration, ok in ((40, True), (10, True), (30, False), (20, True)):
            window.append(
                ExternalCallResult(
                    service="qqmusic",
                    operation="search",
                    result_kind="ok" if ok else "timeout",
                    duration_ms=duration,
                    ok=ok,
                    created_at="2026-08-30T00:00:00Z",
                )
            )

        summary = window.summary()
        self.assertEqual(summary.count, 4)
        self.assertEqual(summary.success, 3)
        self.assertEqual(summary.failure, 1)
        self.assertEqual(summary.last_ms, 20)
        self.assertEqual(summary.p50_ms, 20)
        self.assertEqual(summary.p95_ms, 40)
        self.assertEqual(summary.success_rate, 0.75)
        self.assertEqual(summary.result_counts, {"ok": 3, "timeout": 1})

    def test_registry_uses_injected_monotonic_and_timestamp_clocks(self) -> None:
        clock = _FakeClock()
        registry = MetricsRegistry(
            window_size=2,
            series_limit=2,
            monotonic_clock=clock.monotonic,
            timestamp_clock=clock.timestamp,
        )
        timer = registry.timer()
        clock.advance(0.125)

        result = registry.record_external(
            service="qqmusic",
            operation="search",
            result_kind="ok",
            ok=True,
            duration_ms=timer.elapsed_ms(),
        )

        self.assertEqual(result.created_at, "fake-100.125")
        self.assertAlmostEqual(result.duration_ms, 125.0)

    def test_registry_bounds_samples_and_series_cardinality(self) -> None:
        registry = MetricsRegistry(window_size=2, series_limit=2)
        for operation in ("one", "two", "three"):
            for duration in (1, 2, 3):
                registry.record_external(
                    service="qqmusic",
                    operation=operation,
                    result_kind="ok",
                    ok=True,
                    duration_ms=duration,
                    created_at="now",
                )

        summaries = registry.summaries()
        self.assertEqual(registry.series_count, 2)
        self.assertEqual(registry.evictions, 1)
        self.assertNotIn("qqmusic:one", summaries)
        self.assertEqual(summaries["qqmusic:three"]["count"], 2)
        self.assertEqual(summaries["qqmusic:three"]["p50_ms"], 2)

    def test_registry_normalizes_non_finite_duration_and_bounds_names(self) -> None:
        registry = MetricsRegistry(window_size=2, series_limit=2)
        result = registry.record_external(
            service="s" * 200,
            operation="o" * 200,
            result_kind="ok",
            ok=True,
            duration_ms=float("inf"),
            created_at="t" * 200,
        )

        self.assertEqual(len(result.service), 80)
        self.assertEqual(len(result.operation), 80)
        self.assertEqual(len(result.created_at), 64)
        self.assertEqual(result.duration_ms, 0.0)

    def test_invalid_capacities_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BoundedWindow[int](0)
        with self.assertRaises(ValueError):
            MetricsRegistry(window_size=0)
        with self.assertRaises(ValueError):
            MetricsRegistry(series_limit=0)


if __name__ == "__main__":
    unittest.main()
