from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import oopzctl


class OopzctlTests(unittest.TestCase):
    def test_diagnose_bundle_is_bounded_and_excludes_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "diagnose.zip"
            with patch.object(oopzctl, "_docker_info", return_value={"available": False}), patch.object(
                oopzctl, "_git", side_effect=["abc", "main", ""]
            ):
                report = oopzctl.diagnose(output)
            with zipfile.ZipFile(output) as archive:
                payload = archive.read("diagnose.json").decode("utf-8")

        self.assertEqual(report["schema_version"], 1)
        self.assertNotIn("QQBOT_BRIDGE_TOKEN=", payload)
        self.assertNotIn("Cookie=", payload)
        self.assertEqual(json.loads(payload)["excluded"][0], ".env")

    def test_restore_requires_explicit_confirmation(self) -> None:
        with patch.object(oopzctl, "_redact", return_value="confirmation required"):
            self.assertEqual(oopzctl.main(["restore", "missing.zip"]), 1)

    def test_upgrade_ref_rejects_option_and_traversal_injection(self) -> None:
        for value in ("--upload-pack=evil", "../main", "main;whoami"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                oopzctl._validate_ref(value)

    def test_dependency_manifest_proves_core_has_no_jm_or_node_uploader(self) -> None:
        manifest = oopzctl.dependency_manifest()
        core = " ".join(manifest["core"]["python"])
        worker = " ".join(manifest["jm-worker"]["python"])

        self.assertNotIn("jmcomic", core)
        self.assertNotIn("img2pdf", core)
        self.assertFalse(manifest["core"]["node_uploader"])
        self.assertIn("jmcomic", worker)
        self.assertIn("redis==7.4.1", worker)
        self.assertNotIn("playwright", worker)
        self.assertTrue(manifest["jm-worker"]["node_uploader"])

    def test_failed_upgrade_restores_recorded_old_images_without_data_restore(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / ".env").write_text(
                "QQBOT_BRIDGE_TOKEN=test\nOOPZ_PANEL_PASSWORD=test\n",
                encoding="utf-8",
            )
            (root / "data").mkdir()
            release_dir = root / "oopz-releases"
            commands = []

            def fake_git(*args, **_kwargs):
                if args == ("status", "--porcelain"):
                    return ""
                if args == ("rev-parse", "HEAD"):
                    return "1" * 40
                if args == ("rev-parse", "FETCH_HEAD"):
                    return "2" * 40
                return ""

            def fake_run(args, **_kwargs):
                commands.append(args)
                if "build" in args:
                    raise RuntimeError("simulated build failure")
                return type("Result", (), {"stdout": "", "stderr": "", "returncode": 0})()

            def fake_backup(_source, output, **_kwargs):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"backup")
                return output

            with (
                patch.object(oopzctl, "ROOT", root),
                patch.object(oopzctl, "RELEASE_DIR", release_dir),
                patch.object(oopzctl, "_git", side_effect=fake_git),
                patch.object(oopzctl, "_run", side_effect=fake_run),
                patch.object(oopzctl, "_docker_info", return_value={"available": True}),
                patch.object(
                    oopzctl,
                    "_running_image_environment",
                    return_value={"OOPZ_BOT_IMAGE": "oopz-bot:old"},
                ),
                patch.object(oopzctl, "create_backup", side_effect=fake_backup),
                patch.object(oopzctl, "validate_archive"),
                patch.object(oopzctl, "_verify_services"),
                patch.object(oopzctl.shutil, "which", return_value="docker"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated build"):
                    oopzctl.upgrade("main", profile=None, dry_run=False)

            self.assertTrue(any("--no-build" in item for item in commands))
            failure = next(release_dir.glob("*-failed.json"))
            manifest = json.loads(failure.read_text(encoding="utf-8"))
            self.assertEqual(manifest["rollback_health"], "ok")
            self.assertFalse(manifest["data_restored"])

    def test_switch_and_health_failures_restore_the_old_release(self) -> None:
        for failure_stage in ("container unhealthy", "readyz failed", "public smoke failed"):
            with self.subTest(failure_stage=failure_stage), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                (root / ".env").write_text(
                    "QQBOT_BRIDGE_TOKEN=test\nOOPZ_PANEL_PASSWORD=test\n",
                    encoding="utf-8",
                )
                (root / "data").mkdir()
                release_dir = root / "oopz-releases"
                commands = []
                verify_calls = 0

                def fake_git(*args, **_kwargs):
                    if args == ("status", "--porcelain"):
                        return ""
                    if args == ("rev-parse", "HEAD"):
                        return "1" * 40
                    if args == ("rev-parse", "FETCH_HEAD"):
                        return "2" * 40
                    return ""

                def fake_run(
                    args,
                    _command_log=commands,
                    _failure_stage=failure_stage,
                    **_kwargs,
                ):
                    _command_log.append(args)
                    if (
                        _failure_stage == "container unhealthy"
                        and "up" in args
                        and "--no-build" not in args
                    ):
                        raise RuntimeError(_failure_stage)
                    return type(
                        "Result", (), {"stdout": "", "stderr": "", "returncode": 0}
                    )()

                def fake_verify(
                    *_args,
                    _failure_stage=failure_stage,
                    **_kwargs,
                ):
                    nonlocal verify_calls
                    verify_calls += 1
                    if verify_calls == 1 and _failure_stage != "container unhealthy":
                        raise RuntimeError(_failure_stage)

                def fake_backup(_source, output, **_kwargs):
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(b"backup")
                    return output

                with (
                    patch.object(oopzctl, "ROOT", root),
                    patch.object(oopzctl, "RELEASE_DIR", release_dir),
                    patch.object(oopzctl, "_git", side_effect=fake_git),
                    patch.object(oopzctl, "_run", side_effect=fake_run),
                    patch.object(oopzctl, "_docker_info", return_value={"available": True}),
                    patch.object(
                        oopzctl,
                        "_running_image_environment",
                        return_value={"OOPZ_BOT_IMAGE": "oopz-bot:old"},
                    ),
                    patch.object(oopzctl, "create_backup", side_effect=fake_backup),
                    patch.object(oopzctl, "validate_archive"),
                    patch.object(oopzctl, "_verify_services", side_effect=fake_verify),
                    patch.object(oopzctl.shutil, "which", return_value="docker"),
                ):
                    with self.assertRaisesRegex(RuntimeError, failure_stage):
                        oopzctl.upgrade("main", profile=None, dry_run=False)

                self.assertTrue(any("--no-build" in item for item in commands))
                manifest = json.loads(
                    next(release_dir.glob("*-failed.json")).read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["rollback_health"], "ok")
                self.assertFalse(manifest["data_restored"])

    def test_preflight_rejects_dirty_tree_missing_secret_and_profile_mismatch(self) -> None:
        with patch.object(oopzctl, "_git", return_value=" M local.py"):
            with self.assertRaisesRegex(RuntimeError, "未提交"):
                oopzctl._require_clean_worktree()

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            env_path = root / ".env"
            with patch.object(oopzctl, "ROOT", root):
                env_path.write_text("QQBOT_BRIDGE_TOKEN=test\n", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "OOPZ_PANEL_PASSWORD"):
                    oopzctl._required_environment(None)

                env_path.write_text(
                    "QQBOT_BRIDGE_TOKEN=test\n"
                    "OOPZ_PANEL_PASSWORD=test\n"
                    "QQBOT_JM_ENABLED=true\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(RuntimeError, "Profile"):
                    oopzctl._required_environment(None)

    def test_release_prune_keeps_two_and_only_removes_scoped_backups(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            release_dir = Path(name).resolve()
            for index in range(4):
                backup = release_dir / f"backup-{index}.zip"
                backup.write_bytes(b"backup")
                (release_dir / f"2026010{index}.json").write_text(
                    json.dumps({"backup": str(backup)}), encoding="utf-8"
                )
            with patch.object(oopzctl, "RELEASE_DIR", release_dir):
                result = oopzctl.prune_releases(keep=2)

            self.assertEqual(result["kept"], 2)
            self.assertEqual(len(list(release_dir.glob("*.json"))), 2)
            self.assertEqual(len(list(release_dir.glob("*.zip"))), 2)

        with self.assertRaises(ValueError):
            oopzctl.prune_releases(keep=1)


if __name__ == "__main__":
    unittest.main()
