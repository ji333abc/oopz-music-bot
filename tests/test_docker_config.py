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

    def test_cookie_refresh_is_installed_and_wired_to_internal_endpoint(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        compose = (root / "compose.yaml").read_text(encoding="utf-8")
        launcher = (root / "oopzbot" / "qqmusic_launcher.cjs").read_text(encoding="utf-8")

        self.assertIn('FROM source AS core', dockerfile)
        self.assertIn('.[legacy,qqmusic-login]', dockerfile)
        self.assertIn('QQ_MUSIC_COOKIE_API_PORT: "3201"', compose)
        self.assertIn("QQ_MUSIC_COOKIE_API_URL: http://qqmusic:3201", compose)
        self.assertIn("x-qqbot-bridge-token", launcher)
        self.assertIn('"/internal/cookie"', launcher)

    def test_jm_profile_uses_separate_image_and_container_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        compose = (root / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("QQ_MUSIC_COOKIE: ${QQ_MUSIC_COOKIE:-}", compose)

        expected = {
            "QQBOT_JM_UPLOADER": "/app/tools/qqbot-uploader/uploader.mjs",
            "QQBOT_JM_TEMP_ROOT": "/app/data/jm-tasks",
        }
        for name, value in expected.items():
            self.assertIn(f"{name}={value}", dockerfile.replace(" ", ""))
            self.assertIn(f"{name}: {value}", compose)
        core = dockerfile.split("FROM source AS core", 1)[1].split("FROM source AS jm-worker", 1)[0]
        worker = dockerfile.split("FROM source AS jm-worker", 1)[1].split("FROM core AS final", 1)[0]
        self.assertNotIn(".[jm", core)
        self.assertNotIn("qqbot-uploader", core)
        self.assertIn('".[jm]"', worker)
        self.assertNotIn(".[jm,legacy]", worker)
        self.assertIn('profiles: ["jm"]', compose)
        self.assertIn("target: jm-worker", compose)
        self.assertIn("target: core", compose)
        self.assertIn("OOPZ_BOT_IMAGE", compose)
        self.assertIn("OOPZ_JM_IMAGE", compose)

    def test_bot_has_no_local_jm_execution_backdoor(self) -> None:
        root = Path(__file__).resolve().parents[1]
        qqbot = (root / "oopzbot" / "qqbot.py").read_text(encoding="utf-8")
        compose = (root / "compose.yaml").read_text(encoding="utf-8")

        self.assertNotIn("JM_EXECUTION_MODE", qqbot)
        self.assertNotIn("_run_jm_job", qqbot)
        self.assertNotIn("from .jm.downloader", qqbot)
        self.assertNotIn("from .jm.uploader", qqbot)
        self.assertNotIn("QQBOT_JM_EXECUTION_MODE", compose)
        self.assertIn("queue.submit_many", qqbot)

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
        self.assertIn("OOPZ_LEGACY_SOURCE_ROOT: /app/legacy_oopzbot", compose)
        self.assertIn("OOPZ_LEGACY_SOURCE_ROOT=/app/legacy_oopzbot", dockerfile)

    def test_external_dependencies_do_not_block_degraded_startup(self) -> None:
        root = Path(__file__).resolve().parents[1]
        compose = (root / "compose.yaml").read_text(encoding="utf-8")

        self.assertNotIn("condition: service_healthy", compose)
        self.assertGreaterEqual(compose.count("condition: service_started"), 3)

    def test_bot_entrypoint_repairs_bind_mount_permissions_before_dropping_root(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        compose = (root / "compose.yaml").read_text(encoding="utf-8")
        entrypoint = (root / "docker-entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("gosu", dockerfile)
        self.assertIn('ENTRYPOINT ["/usr/local/bin/oopzbot-entrypoint"]', dockerfile)
        self.assertIn("chown -R oopzbot:oopzbot /app/data", entrypoint)
        self.assertIn("export HOME=/app", entrypoint)
        self.assertIn('exec gosu oopzbot "$@"', entrypoint)
        self.assertIn("- gosu\n        - oopzbot\n        - python", compose)

    def test_internal_services_are_not_published_to_the_host(self) -> None:
        root = Path(__file__).resolve().parents[1]
        compose = (root / "compose.yaml").read_text(encoding="utf-8")

        self.assertNotIn(":6379:6379", compose)
        self.assertNotIn(":3200:3200", compose)
        self.assertNotIn(":18080:18080", compose)
        self.assertIn('"${OOPZ_LEGACY_WEB_BIND:-127.0.0.1}:${OOPZ_LEGACY_WEB_PORT:-18081}:18081"', compose)
        self.assertIn('"${OOPZ_PANEL_BIND:-127.0.0.1}:${OOPZ_PANEL_PORT:-3000}:3000"', compose)
        self.assertIn("/healthz", compose)


if __name__ == "__main__":
    unittest.main()
