from __future__ import annotations

import unittest
from pathlib import Path


class DockerConfigurationTests(unittest.TestCase):
    def test_qqmusic_image_matches_legacy_native_api(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile.qqmusic").read_text(encoding="utf-8")

        self.assertIn("@sansenjian/qq-music-api@2.4.0", dockerfile)
        self.assertNotIn("Rain120/qq-music-api", dockerfile)
        self.assertIn("QQ_MUSIC_API_CONFIG_DIR=/opt/qqmusic-config", dockerfile)

    def test_bot_image_and_compose_use_container_jm_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        compose = (root / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("QQ_MUSIC_COOKIE: ${QQ_MUSIC_COOKIE:-}", compose)

        expected = {
            "QQBOT_JM_UPLOADER": "/app/tools/qqbot-uploader/uploader.mjs",
            "QQBOT_JM_TEMP_ROOT": "/app/data/jm-tasks",
            "QQBOT_JM_TIMING_PATH": "/app/data/jm_timing.json",
        }
        for name, value in expected.items():
            self.assertIn(f"{name}={value}", dockerfile.replace(" ", ""))
            self.assertIn(f"{name}: {value}", compose)

    def test_legacy_oopz_core_uses_persistent_redis_and_data_volume(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        compose = (root / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("COPY legacy_oopzbot ./legacy_oopzbot", dockerfile)
        self.assertIn("OOPZBOT_USE_LEGACY_CORE: \"true\"", compose)
        self.assertIn("BOT_REDIS_HOST: redis", compose)
        self.assertIn("redis:7.4-alpine", compose)
        self.assertIn("redis-data:/data", compose)
        self.assertIn("OOPZ_LEGACY_DATA_DIR: /app/data/legacy", compose)


if __name__ == "__main__":
    unittest.main()
