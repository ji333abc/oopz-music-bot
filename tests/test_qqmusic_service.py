from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from oopzbot.qqmusic_service import (
    QQMUSIC_COMMIT,
    QQMUSIC_MARKER,
    ManagedQQMusicService,
    install_marker,
    managed_installation_errors,
    managed_url_error,
)


class QQMusicServiceTests(unittest.TestCase):
    def test_managed_url_must_be_loopback_http(self) -> None:
        self.assertEqual(managed_url_error("http://127.0.0.1:3200"), "")
        self.assertEqual(managed_url_error("http://localhost:3200"), "")
        self.assertIn("回环地址", managed_url_error("http://0.0.0.0:3200"))
        self.assertIn("http", managed_url_error("https://127.0.0.1:3200"))

    def test_installation_marker_and_runtime_files_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "node_modules" / "ts-node" / "register").mkdir(parents=True)
            (root / "package.json").write_text("{}", encoding="utf-8")
            (root / "src" / "app.ts").write_text("", encoding="utf-8")
            (root / "node_modules" / "ts-node" / "register" / "transpile-only.js").write_text(
                "",
                encoding="utf-8",
            )
            (root / QQMUSIC_MARKER).write_text(
                json.dumps(install_marker()),
                encoding="utf-8",
            )
            self.assertEqual(managed_installation_errors(root), [])

            marker = install_marker()
            marker["commit"] = "wrong"
            (root / QQMUSIC_MARKER).write_text(json.dumps(marker), encoding="utf-8")
            self.assertTrue(
                any(QQMUSIC_COMMIT[:12] in error for error in managed_installation_errors(root))
            )

    def test_child_process_does_not_receive_application_secrets(self) -> None:
        settings = SimpleNamespace(
            qq_music_enabled=True,
            qq_music_managed=True,
            qq_music_base_url="http://127.0.0.1:3200",
            qq_music_service_dir=".services/qqmusic-api",
        )
        process = Mock()
        process.poll.return_value = None
        with (
            patch.dict(
                os.environ,
                {"PATH": "test-path", "QQBOT_APP_SECRET": "must-not-leak"},
                clear=True,
            ),
            patch(
                "oopzbot.qqmusic_service.managed_installation_errors",
                return_value=[],
            ),
            patch("oopzbot.qqmusic_service.shutil.which", return_value="node"),
            patch("oopzbot.qqmusic_service.subprocess.Popen", return_value=process) as popen,
        ):
            service = ManagedQQMusicService(settings, timeout=0.1)
            service._compatible_service_is_ready = Mock(side_effect=[False, True])
            service.start()

        child_env = popen.call_args.kwargs["env"]
        self.assertEqual(child_env["PATH"], "test-path")
        self.assertNotIn("QQBOT_APP_SECRET", child_env)
        self.assertEqual(child_env["PORT"], "3200")

    def test_existing_compatible_service_is_not_owned(self) -> None:
        settings = SimpleNamespace(
            qq_music_enabled=True,
            qq_music_managed=True,
            qq_music_base_url="http://127.0.0.1:3200",
            qq_music_service_dir=".services/qqmusic-api",
        )
        service = ManagedQQMusicService(settings)
        service._compatible_service_is_ready = Mock(return_value=True)
        with patch("oopzbot.qqmusic_service.subprocess.Popen") as popen:
            service.start()
            service.close()
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
