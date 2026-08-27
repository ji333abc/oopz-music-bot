from __future__ import annotations

import unittest
from pathlib import Path


class DockerConfigurationTests(unittest.TestCase):
    def test_bot_image_and_compose_use_container_jm_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        compose = (root / "compose.yaml").read_text(encoding="utf-8")

        expected = {
            "QQBOT_JM_UPLOADER": "/app/tools/qqbot-uploader/uploader.mjs",
            "QQBOT_JM_TEMP_ROOT": "/app/data/jm-tasks",
            "QQBOT_JM_TIMING_PATH": "/app/data/jm_timing.json",
        }
        for name, value in expected.items():
            self.assertIn(f"{name}={value}", dockerfile.replace(" ", ""))
            self.assertIn(f"{name}: {value}", compose)


if __name__ == "__main__":
    unittest.main()
