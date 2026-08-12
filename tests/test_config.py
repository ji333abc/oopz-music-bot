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

    def test_public_bridge_host_is_rejected(self) -> None:
        with patch.dict(os.environ, {"OOPZBOT_BRIDGE_HOST": "0.0.0.0"}, clear=True):
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


if __name__ == "__main__":
    unittest.main()
