from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from oopzbot.qqmusic_credential import CredentialStore
from oopzbot.qqmusic_login import _decode_qr_modules, _print_terminal_qr, build_parser, cmd_cookie


class QQMusicLoginCliTests(unittest.TestCase):
    @staticmethod
    def _qr_image() -> tuple[bytes, list[list[bool]]]:
        size = 21
        modules = [[False for _ in range(size)] for _ in range(size)]

        def draw_finder(left: int, top: int) -> None:
            for y in range(7):
                for x in range(7):
                    modules[top + y][left + x] = (
                        x in {0, 6} or y in {0, 6} or (2 <= x <= 4 and 2 <= y <= 4)
                    )

        draw_finder(0, 0)
        draw_finder(size - 7, 0)
        draw_finder(0, size - 7)
        for offset in range(8, size - 8):
            modules[6][offset] = offset % 2 == 0
            modules[offset][6] = offset % 2 == 0

        scale = 4
        border = 4
        image = Image.new("L", ((size + border * 2) * scale,) * 2, color=255)
        draw = ImageDraw.Draw(image)
        for y, row in enumerate(modules):
            for x, dark in enumerate(row):
                if dark:
                    x0 = (x + border) * scale
                    y0 = (y + border) * scale
                    draw.rectangle((x0, y0, x0 + scale - 1, y0 + scale - 1), fill=0)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue(), modules

    def test_parser_accepts_cookie_subcommand(self) -> None:
        args = build_parser().parse_args(["cookie"])
        self.assertEqual(args.action, "cookie")

    def test_parser_enables_terminal_qr_by_default(self) -> None:
        args = build_parser().parse_args(["login", "--no-open"])
        self.assertFalse(args.no_terminal_qr)

    def test_terminal_qr_recovers_modules_and_prints_ansi_blocks(self) -> None:
        image, expected = self._qr_image()
        self.assertEqual(_decode_qr_modules(image), expected)

        output = io.StringIO()
        _print_terminal_qr(image, output)
        rendered = output.getvalue()
        self.assertIn("终端二维码", rendered)
        self.assertIn("\x1b[40m", rendered)
        self.assertIn("\x1b[107m", rendered)

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
