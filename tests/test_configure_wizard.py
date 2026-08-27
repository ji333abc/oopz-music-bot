from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.configure import configure, configure_oopz_targets, read_values, write_values


class ConfigureWizardTests(unittest.TestCase):
    def test_oopz_targets_are_selected_from_area_id_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = Path(__file__).resolve().parents[1] / ".env.example"
            (root / ".env.example").write_text(
                template.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            env_path = root / ".env"
            values: dict[str, str] = {}
            responses = [
                {"areas": [{"id": "area-1", "name": "我的域", "groups": []}]},
                {
                    "areas": [
                        {
                            "id": "area-1",
                            "groups": [
                                {
                                    "name": "常用频道",
                                    "channels": [
                                        {"id": "text-1", "name": "聊天", "type": "TEXT"},
                                        {"id": "voice-1", "name": "音乐", "type": "VOICE"},
                                    ],
                                }
                            ],
                        }
                    ]
                },
            ]
            answers = iter(["", "", ""])

            with (
                patch("scripts.configure.query_oopz", side_effect=responses) as query,
                patch("builtins.input", side_effect=lambda _prompt="": next(answers)),
            ):
                configure_oopz_targets(root, env_path, values)

            self.assertEqual(values["QQBOT_OOPZ_AREA_ID"], "area-1")
            self.assertEqual(values["QQBOT_OOPZ_TEXT_CHANNEL_ID"], "text-1")
            self.assertEqual(values["QQBOT_OOPZ_VOICE_CHANNEL_ID"], "voice-1")
            query.assert_any_call(root, env_path, area_id="area-1")

    def test_write_values_quotes_special_characters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / ".env.example"
            output = root / ".env"
            template.write_text("SECRET=\nPLAIN=\n", encoding="utf-8")

            write_values(
                template,
                output,
                {
                    "SECRET": "has spaces # and = signs $HOME and 'quote'\\",
                    "PLAIN": "abc-123",
                },
            )

            self.assertEqual(
                read_values(output),
                {
                    "SECRET": "has spaces # and = signs $HOME and 'quote'\\",
                    "PLAIN": "abc-123",
                },
            )
            self.assertIn(
                "SECRET='has spaces # and = signs $HOME and \\'quote\\'\\\\'",
                output.read_text(encoding="utf-8"),
            )
            if __import__("os").name != "nt":
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_new_configuration_walks_through_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = Path(__file__).resolve().parents[1] / ".env.example"
            (root / ".env.example").write_text(
                template.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            env_path = root / ".env"
            answers = iter(
                [
                    "app-id",
                    "",
                    "",
                    "13800000000",
                    "n",
                    "area-id",
                    "text-id",
                    "voice-id",
                    "agora-id",
                    "",
                    "",
                ]
            )
            secrets = iter(["app-secret", "oopz-password", "music-cookie"])

            with (
                patch("builtins.input", side_effect=lambda _prompt="": next(answers)),
                patch("getpass.getpass", side_effect=lambda _prompt="": next(secrets)),
            ):
                result = configure(root, env_path, with_jm=False)

            self.assertEqual(result, 0)
            values = read_values(env_path)
            self.assertEqual(values["QQBOT_APP_ID"], "app-id")
            self.assertEqual(values["QQBOT_APP_SECRET"], "app-secret")
            self.assertEqual(values["OOPZ_LOGIN_PHONE"], "13800000000")
            self.assertEqual(values["QQBOT_OOPZ_VOICE_CHANNEL_ID"], "voice-id")
            self.assertEqual(values["OOPZ_AGORA_APP_ID"], "agora-id")
            self.assertEqual(values["QQ_MUSIC_BASE_URL"], "http://127.0.0.1:3200")
            self.assertEqual(values["QQ_MUSIC_MANAGED"], "true")
            self.assertEqual(values["QQBOT_JM_ENABLED"], "false")
            self.assertTrue(values["QQBOT_BRIDGE_TOKEN"])

    def test_external_music_mode_keeps_custom_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = Path(__file__).resolve().parents[1] / ".env.example"
            (root / ".env.example").write_text(
                template.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            env_path = root / ".env"
            env_path.write_text(
                "QQ_MUSIC_BASE_URL=https://music.example.test\n",
                encoding="utf-8",
            )
            answers = iter([""])
            with patch("builtins.input", side_effect=lambda _prompt="": next(answers)):
                result = configure(
                    root,
                    env_path,
                    with_jm=False,
                    music_mode="external",
                )
            self.assertEqual(result, 0)
            values = read_values(env_path)
            self.assertEqual(values["QQ_MUSIC_MANAGED"], "false")
            self.assertEqual(values["QQ_MUSIC_BASE_URL"], "https://music.example.test")


if __name__ == "__main__":
    unittest.main()
