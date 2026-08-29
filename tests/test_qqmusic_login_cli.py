from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from oopzbot.qqmusic_credential import CredentialStore
from oopzbot.qqmusic_login import build_parser, cmd_cookie


class QQMusicLoginCliTests(unittest.TestCase):
    def test_parser_accepts_cookie_subcommand(self) -> None:
        args = build_parser().parse_args(["cookie"])
        self.assertEqual(args.action, "cookie")

    def test_cookie_command_publishes_custom_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(Path(directory) / "credential.json")
            store.save(
                {
                    "musicid": "10001",
                    "musickey": "cookie-key",
                    "musickey_create_time": 1,
                    "key_expires_in": 1,
                },
                source="test",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                code = cmd_cookie(None, store)
            self.assertEqual(code, 0)
            self.assertIn("cookie-key", store.state_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
