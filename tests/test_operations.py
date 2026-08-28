from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oopzbot.operations import OperationsRegistry


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

            self.assertNotIn("historical-secret", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
