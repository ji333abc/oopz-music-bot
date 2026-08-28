from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

import oopzbot


class VersionConsistencyTests(unittest.TestCase):
    def test_python_panel_lock_and_changelog_share_the_canonical_version(self) -> None:
        root = Path(__file__).resolve().parents[1]
        panel_package = json.loads((root / "panel" / "package.json").read_text(encoding="utf-8"))
        panel_lock = json.loads((root / "panel" / "package-lock.json").read_text(encoding="utf-8"))
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        version = oopzbot.__version__

        self.assertRegex((root / "pyproject.toml").read_text(encoding="utf-8"), r"dynamic\s*=\s*\[\"version\"\]")
        self.assertEqual(panel_package["version"], version)
        self.assertEqual(panel_lock["version"], version)
        self.assertIsNotNone(
            re.search(rf"^## \[{re.escape(version)}\]", changelog, flags=re.MULTILINE)
        )

    def test_existing_release_tag_cannot_point_to_another_commit(self) -> None:
        root = Path(__file__).resolve().parents[1]
        tag = f"v{oopzbot.__version__}"
        tagged = subprocess.run(
            ["git", "rev-list", "-n", "1", tag],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if tagged.returncode != 0:
            self.skipTest(f"开发提交尚未创建 {tag} 标签")
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(tagged.stdout.strip(), head.stdout.strip())


if __name__ == "__main__":
    unittest.main()
