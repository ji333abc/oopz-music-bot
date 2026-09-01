from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oopzbot.metrics import (
    CommandTiming,
    FailureRecord,
    LatencySummary,
    PlaybackHistoryItem,
)
from oopzbot.operations import (
    COMMAND_HISTORY_LIMIT,
    FAILURE_HISTORY_LIMIT,
    MAX_STATE_BYTES,
    PANEL_HISTORY_VIEW_LIMIT,
    PLAYBACK_HISTORY_LIMIT,
    STATE_SCHEMA_VERSION,
    OperationsRegistry,
)


class OperationsRegistryTests(unittest.TestCase):
    def test_events_and_jm_jobs_survive_reload_without_passwords(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "panel-state.json"
            registry = OperationsRegistry(path)
            registry.set_component("qq_bot", "ok", "QQ 网关已连接")
            job_id = registry.begin_jm_job("351587", requester="QQ 群用户")
            registry.update_jm_job(
                job_id,
                status="completed",
                phase="completed",
                page_count=42,
                archive_bytes=12345,
            )

            restored = OperationsRegistry(path).snapshot()

            self.assertEqual(restored["components"]["qq_bot"]["status"], "ok")
            self.assertEqual(restored["components"]["qq_bot"]["reason"], "QQ 网关已连接")
            self.assertEqual(restored["jm_jobs"][0]["album_id"], "351587")
            self.assertEqual(restored["jm_jobs"][0]["status"], "completed")
            self.assertTrue(restored["events"])
            self.assertNotIn("password", json.dumps(restored).lower())

    def test_events_and_job_errors_are_redacted_before_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "panel-state.json"
            with patch.dict(
                "os.environ",
                {"QQBOT_BRIDGE_TOKEN": "fake-bridge-secret"},
                clear=False,
            ):
                registry = OperationsRegistry(path)
                registry.record_event(
                    "failure",
                    "request token=fake-bridge-secret",
                    source="token=fake-bridge-secret",
                )
                job_id = registry.begin_jm_job("123")
                registry.update_jm_job(
                    job_id,
                    status="failed",
                    error="request token=fake-bridge-secret",
                )
                snapshot = registry.snapshot()
                persisted = path.read_text(encoding="utf-8")

            self.assertNotIn("fake-bridge-secret", json.dumps(snapshot))
            self.assertNotIn("fake-bridge-secret", persisted)

    def test_existing_state_is_rewritten_in_redacted_form_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "panel-state.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "components": {},
                        "events": [
                            {
                                "message": "token=historical-secret",
                                "source": "system",
                            }
                        ],
                        "jm_jobs": [],
                        "command_history": [
                            {
                                "command_id": "command-12345678",
                                "source": "panel",
                                "kind": "status",
                                "ok": False,
                                "duration_ms": "historical-secret",
                                "created_at": "token=historical-secret",
                            }
                        ],
                        "external_metrics": {
                            "qqmusic:search": {
                                "count": "historical-secret",
                                "p95_ms": "historical-secret",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {"QQBOT_BRIDGE_TOKEN": "historical-secret"},
                clear=False,
            ):
                OperationsRegistry(path)

            rewritten = path.read_text(encoding="utf-8")
            self.assertNotIn("historical-secret", rewritten)
            state = json.loads(rewritten)
            self.assertEqual(state["command_history"][0]["duration_ms"], 0.0)
            self.assertEqual(state["external_metrics"]["qqmusic:search"]["count"], 0)

    def test_version_one_state_migrates_with_bounded_p2_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "panel-state.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "components": {"redis": {"status": "ok", "message": "ready"}},
                        "events": [],
                        "jm_jobs": [],
                        "command_history": [
                            {
                                "command_id": str(index),
                                "source": "panel",
                                "kind": "status",
                                "ok": True,
                            }
                            for index in range(COMMAND_HISTORY_LIMIT + 25)
                        ],
                        "playback_history": [{}] * (PLAYBACK_HISTORY_LIMIT + 5),
                        "failure_history": [{}] * (FAILURE_HISTORY_LIMIT + 5),
                    }
                ),
                encoding="utf-8",
            )

            snapshot = OperationsRegistry(path).snapshot()
            persisted = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(snapshot["schema_version"], STATE_SCHEMA_VERSION)
            self.assertEqual(persisted["version"], STATE_SCHEMA_VERSION)
            self.assertEqual(persisted["schema_version"], STATE_SCHEMA_VERSION)
            self.assertEqual(len(persisted["command_history"]), COMMAND_HISTORY_LIMIT)
            self.assertEqual(len(persisted["playback_history"]), PLAYBACK_HISTORY_LIMIT)
            self.assertEqual(len(persisted["failure_history"]), FAILURE_HISTORY_LIMIT)
            self.assertEqual(len(snapshot["command_history"]), PANEL_HISTORY_VIEW_LIMIT)
            self.assertEqual(snapshot["components"]["redis"]["status"], "ok")
            self.assertLessEqual(path.stat().st_size, MAX_STATE_BYTES)

    def test_p2_history_and_metric_records_are_redacted_before_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "panel-state.json"
            with patch.dict(
                "os.environ",
                {"QQBOT_BRIDGE_TOKEN": "p2-secret-value"},
                clear=False,
            ):
                registry = OperationsRegistry(path)
                registry.record_command_timing(
                    CommandTiming(
                        command_id="command-12345678",
                        source="token=p2-secret-value",
                        kind="search",
                        ok=False,
                        error_kind="dependency",
                        duration_ms=12.5,
                    )
                )
                registry.record_playback(
                    PlaybackHistoryItem(
                        song_id="song-1",
                        name="token=p2-secret-value",
                        artists="artist",
                        platform="qq",
                        source="panel",
                        result="failed",
                        started_at="now",
                    )
                )
                registry.record_failure(
                    FailureRecord(
                        component="qqmusic",
                        error_kind="network",
                        message=(
                            "Authorization: p2-secret-value "
                            "https://media.invalid/play.mp3?token=signed"
                        ),
                        created_at="now",
                    )
                )
                registry.set_external_metric(
                    "qqmusic",
                    "search",
                    LatencySummary(
                        count=1,
                        success=0,
                        failure=1,
                        last_ms=12.5,
                        p50_ms=12.5,
                        p95_ms=12.5,
                    ),
                )
                snapshot = registry.snapshot()
                persisted = path.read_text(encoding="utf-8")

            self.assertNotIn("p2-secret-value", json.dumps(snapshot))
            self.assertNotIn("p2-secret-value", persisted)
            self.assertNotIn("media.invalid", persisted)
            self.assertEqual(snapshot["external_metrics"]["qqmusic:search"]["count"], 1)
            self.assertEqual(snapshot["command_history"][0]["duration_ms"], 12.5)


if __name__ == "__main__":
    unittest.main()
