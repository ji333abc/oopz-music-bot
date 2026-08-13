from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from oopzbot.qqmusic_service import QQMUSIC_COMMIT, QQMUSIC_REPOSITORY
from scripts.install_qqmusic import install


class QQMusicInstallerTests(unittest.TestCase):
    def test_fresh_install_fetches_only_the_pinned_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "qqmusic"
            with (
                patch(
                    "scripts.install_qqmusic.require_tool",
                    side_effect=lambda name: name,
                ),
                patch("scripts.install_qqmusic.require_node_18"),
                patch("scripts.install_qqmusic.try_run", side_effect=[QQMUSIC_REPOSITORY, ""]),
                patch("scripts.install_qqmusic.run") as run,
                patch(
                    "scripts.install_qqmusic.managed_installation_errors",
                    return_value=[],
                ),
            ):
                run.side_effect = lambda command, **_kwargs: (
                    QQMUSIC_COMMIT if command[1:3] == ["rev-parse", "HEAD"] else ""
                )
                install(target)

            self.assertIn(
                call(
                    ["git", "fetch", "--depth", "1", "origin", QQMUSIC_COMMIT],
                    cwd=target.resolve(),
                ),
                run.call_args_list,
            )
            self.assertIn(
                call(
                    ["npm", "ci", "--include=dev", "--no-audit", "--no-fund"],
                    cwd=target.resolve(),
                ),
                run.call_args_list,
            )


if __name__ == "__main__":
    unittest.main()
