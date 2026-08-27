from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
