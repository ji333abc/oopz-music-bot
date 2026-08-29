from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_domain_has_no_framework_or_infrastructure_imports(self) -> None:
        forbidden = ("fastapi", "botpy", "redis", "legacy_oopzbot", "oopzbot.infrastructure")
        for path in (ROOT / "oopzbot" / "domain").glob("*.py"):
            for imported in _imports(path):
                self.assertFalse(
                    imported.startswith(forbidden),
                    f"{path.name} must not import {imported}",
                )

    def test_application_does_not_import_transports_or_concrete_adapters(self) -> None:
        forbidden = (
            "fastapi",
            "botpy",
            "redis",
            "legacy_oopzbot",
            "oopzbot.bridge",
            "oopzbot.qqbot",
            "oopzbot.infrastructure",
        )
        for path in (ROOT / "oopzbot" / "application").glob("*.py"):
            for imported in _imports(path):
                self.assertFalse(
                    imported.startswith(forbidden),
                    f"{path.name} must not import {imported}",
                )

    def test_qq_policy_does_not_import_music_redis_or_botpy(self) -> None:
        forbidden = ("botpy", "redis", "legacy_oopzbot", "oopzbot.music", "oopzbot.controller")
        for path in (ROOT / "oopzbot" / "qq").glob("*.py"):
            for imported in _imports(path):
                self.assertFalse(
                    imported.startswith(forbidden),
                    f"{path.name} must not import {imported}",
                )

    def test_http_helpers_do_not_operate_the_music_queue(self) -> None:
        forbidden_calls = ("._get_queue(", ".play_next(", ".stop_play(", ".add_to_queue(")
        for path in (ROOT / "oopzbot" / "http").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for marker in forbidden_calls:
                self.assertNotIn(marker, source, f"{path.name} contains {marker}")

    def test_new_packages_import_without_legacy_path_bootstrap(self) -> None:
        modules = (
            "oopzbot.application.command_service",
            "oopzbot.application.playback_service",
            "oopzbot.application.queue_service",
            "oopzbot.commands.registry",
            "oopzbot.domain.contracts",
            "oopzbot.http.validation",
            "oopzbot.infrastructure.queue_adapter",
            "oopzbot.jm.service",
            "oopzbot.qq.reply_policy",
        )
        for module in modules:
            self.assertIsNotNone(importlib.import_module(module))


if __name__ == "__main__":
    unittest.main()
