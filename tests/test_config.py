from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oopzbot.config import Settings, ensure_bridge_token


class SettingsTests(unittest.TestCase):
    def test_packaged_template_matches_root_template(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            (root / ".env.example").read_text(encoding="utf-8").rstrip(),
            (root / "oopzbot" / "env.example").read_text(encoding="utf-8").rstrip(),
        )

    def test_complete_password_configuration_is_valid(self) -> None:
        environment = {
            "QQBOT_APP_ID": "app",
            "QQBOT_APP_SECRET": "secret",
            "QQBOT_BRIDGE_TOKEN": "bridge",
            "QQBOT_OOPZ_AREA_ID": "area",
            "QQBOT_OOPZ_TEXT_CHANNEL_ID": "text",
            "QQBOT_OOPZ_VOICE_CHANNEL_ID": "voice",
            "OOPZ_LOGIN_PHONE": "13800000000",
            "OOPZ_LOGIN_PASSWORD": "password",
            "QQ_MUSIC_BASE_URL": "http://127.0.0.1:3200",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(Settings.from_env().validate(), [])

    def test_legacy_core_requires_agora_app_id(self) -> None:
        environment = {
            "QQBOT_APP_ID": "app",
            "QQBOT_APP_SECRET": "secret",
            "QQBOT_BRIDGE_TOKEN": "bridge",
            "QQBOT_OOPZ_AREA_ID": "area",
            "QQBOT_OOPZ_TEXT_CHANNEL_ID": "text",
            "QQBOT_OOPZ_VOICE_CHANNEL_ID": "voice",
            "OOPZ_LOGIN_PHONE": "13800000000",
            "OOPZ_LOGIN_PASSWORD": "password",
            "OOPZBOT_USE_LEGACY_CORE": "true",
        }
        with patch.dict(os.environ, environment, clear=True):
            errors = Settings.from_env().validate()
        self.assertIn("启用旧版 OOPZ 核心时必须配置 OOPZ_AGORA_APP_ID", errors)

    def test_public_bridge_host_is_rejected(self) -> None:
        with patch.dict(os.environ, {"OOPZBOT_BRIDGE_HOST": "0.0.0.0"}, clear=True):
            errors = Settings.from_env().validate()
        self.assertTrue(any("回环地址" in error for error in errors))

    def test_private_docker_bridge_bind_is_explicitly_allowed(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OOPZBOT_BRIDGE_HOST": "0.0.0.0",
                "OOPZBOT_BRIDGE_PRIVATE_NETWORK": "true",
            },
            clear=True,
        ):
            settings = Settings.from_env()
            errors = settings.validate()
        self.assertFalse(any("回环地址" in error for error in errors))
        self.assertEqual(
            settings.bridge_url,
            "http://127.0.0.1:18080/internal/qqbot/command",
        )

    def test_external_music_url_preserves_legacy_external_mode(self) -> None:
        with patch.dict(
            os.environ,
            {"QQ_MUSIC_BASE_URL": "https://music.example.test"},
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertFalse(settings.qq_music_managed)

    def test_managed_music_rejects_public_host(self) -> None:
        with patch.dict(
            os.environ,
            {
                "QQ_MUSIC_MANAGED": "true",
                "QQ_MUSIC_BASE_URL": "http://0.0.0.0:3200",
            },
            clear=True,
        ):
            errors = Settings.from_env().validate()
        self.assertTrue(any("回环地址" in error for error in errors))

    def test_bridge_token_is_generated_in_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("QQBOT_BRIDGE_TOKEN=\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                token = ensure_bridge_token(path)
            self.assertGreaterEqual(len(token), 32)
            self.assertIn(f"QQBOT_BRIDGE_TOKEN={token}", path.read_text(encoding="utf-8"))

    def test_qqmusic_refresh_settings_parse_and_validate(self) -> None:
        with patch.dict(
            os.environ,
            {
                "QQ_MUSIC_CREDENTIAL_FILE": "data/custom.json",
                "QQ_MUSIC_AUTO_REFRESH": "false",
                "QQ_MUSIC_REFRESH_MIN_HOURS": "8",
                "QQ_MUSIC_REFRESH_MAX_HOURS": "12",
                "QQ_MUSIC_COOKIE_API_URL": "http://qqmusic:3201",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.qq_music_credential_file, "data/custom.json")
        self.assertFalse(settings.qq_music_auto_refresh)
        self.assertEqual(settings.qq_music_refresh_min_hours, 8)
        self.assertEqual(settings.qq_music_refresh_max_hours, 12)
        self.assertEqual(settings.qq_music_cookie_api_url, "http://qqmusic:3201")

    def test_qqmusic_refresh_window_must_be_ordered(self) -> None:
        with patch.dict(
            os.environ,
            {"QQ_MUSIC_REFRESH_MIN_HOURS": "25", "QQ_MUSIC_REFRESH_MAX_HOURS": "6"},
            clear=True,
        ):
            errors = Settings.from_env().validate()
        self.assertIn("QQ_MUSIC_REFRESH_MIN_HOURS 不能大于 MAX_HOURS", errors)

    def test_search_cache_settings_parse_and_bound_invalid_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OOPZ_SEARCH_CACHE_ENABLED": "false",
                "OOPZ_SEARCH_CACHE_TTL_SECONDS": "120",
                "OOPZ_SEARCH_CACHE_MAX_ENTRIES": "512",
                "OOPZ_SEARCH_NEGATIVE_CACHE_TTL_SECONDS": "20",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertFalse(settings.search_cache_enabled)
        self.assertEqual(settings.search_cache_ttl_seconds, 120)
        self.assertEqual(settings.search_cache_max_entries, 512)
        self.assertEqual(settings.search_negative_cache_ttl_seconds, 20)

        with patch.dict(
            os.environ,
            {
                "OOPZ_SEARCH_CACHE_TTL_SECONDS": "-1",
                "OOPZ_SEARCH_CACHE_MAX_ENTRIES": "unbounded",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.search_cache_ttl_seconds, 60)
        self.assertEqual(settings.search_cache_max_entries, 256)

    def test_album_request_settings_parse_and_are_bounded(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OOPZ_ALBUM_REQUEST_ENABLED": "true",
                "OOPZ_ALBUM_REQUEST_MAX_TRACKS": "50",
                "OOPZ_ALBUM_REQUEST_SESSION_TTL_SECONDS": "600",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertTrue(settings.album_request_enabled)
        self.assertEqual(settings.album_request_max_tracks, 50)
        self.assertEqual(settings.album_request_session_ttl_seconds, 600)

        with patch.dict(
            os.environ,
            {
                "OOPZ_ALBUM_REQUEST_MAX_TRACKS": "0",
                "OOPZ_ALBUM_REQUEST_SESSION_TTL_SECONDS": "9999",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.album_request_max_tracks, 30)
        self.assertEqual(settings.album_request_session_ttl_seconds, 300)

    def test_panel_sse_settings_are_bounded(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OOPZ_PANEL_SSE_ENABLED": "false",
                "OOPZ_PANEL_SSE_HEARTBEAT_SECONDS": "25",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertFalse(settings.panel_sse_enabled)
        self.assertEqual(settings.panel_sse_heartbeat_seconds, 25)

        with patch.dict(
            os.environ,
            {"OOPZ_PANEL_SSE_HEARTBEAT_SECONDS": "1"},
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.panel_sse_heartbeat_seconds, 20)

    def test_metric_and_history_limits_are_bounded(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OOPZ_METRICS_WINDOW_SIZE": "500",
                "OOPZ_PLAYBACK_HISTORY_LIMIT": "75",
                "OOPZ_FAILURE_HISTORY_LIMIT": "125",
                "OOPZ_COMMAND_HISTORY_LIMIT": "250",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.metrics_window_size, 500)
        self.assertEqual(settings.playback_history_limit, 75)
        self.assertEqual(settings.failure_history_limit, 125)
        self.assertEqual(settings.command_history_limit, 250)


if __name__ == "__main__":
    unittest.main()
