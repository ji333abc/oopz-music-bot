from __future__ import annotations

import json
import re
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


if __name__ == "__main__":
    unittest.main()
