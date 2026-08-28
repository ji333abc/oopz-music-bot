from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

from scripts.backup import create_backup
from scripts.restore import _validate_target, restore_backup, validate_archive


class BackupRestoreTests(unittest.TestCase):
    def test_data_backup_restore_and_checksum_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            (data / "queue.json").write_text('{"items":["song"]}', encoding="utf-8")
            (data / ".env").write_text("SECRET=must-not-be-archived", encoding="utf-8")
            archive = root / "backup.zip"
            compose = root / "compose.yaml"

            create_backup(data, archive, include_redis=False)
            (data / "queue.json").write_text('{"items":[]}', encoding="utf-8")
            (data / "extra.txt").write_text("remove", encoding="utf-8")
            recovery = restore_backup(
                archive,
                data,
                component="data",
                compose_file=compose,
            )

            self.assertEqual((data / "queue.json").read_text(encoding="utf-8"), '{"items":["song"]}')
            self.assertFalse((data / "extra.txt").exists())
            self.assertTrue((data / ".env").exists())
            self.assertTrue(recovery.is_file())
            _, manifest, _ = validate_archive(archive)
            self.assertFalse(manifest["env_included"])

    def test_corrupt_archive_and_path_traversal_are_rejected_before_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            (data / "keep.txt").write_text("keep", encoding="utf-8")
            archive = root / "backup.zip"
            compose = root / "compose.yaml"
            create_backup(data, archive, include_redis=False)
            corrupt = root / "corrupt.zip"
            corrupt.write_bytes(archive.read_bytes().replace(b"keep", b"changed", 1))
            with self.assertRaises(ValueError):
                restore_backup(
                    corrupt,
                    data,
                    component="data",
                    compose_file=compose,
                )
            self.assertEqual((data / "keep.txt").read_text(encoding="utf-8"), "keep")

            traversal = root / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as output:
                output.writestr("../outside.txt", "bad")
            with self.assertRaises(ValueError):
                validate_archive(traversal)

    def test_restore_cli_requires_confirm_without_modifying_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            marker = data / "marker.txt"
            marker.write_text("original", encoding="utf-8")
            archive = root / "backup.zip"
            create_backup(data, archive, include_redis=False)
            result = subprocess.run(
                [sys.executable, "scripts/restore.py", str(archive), "--data-dir", str(data)],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(marker.read_text(encoding="utf-8"), "original")

    def test_backup_manifest_can_include_a_controlled_redis_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            snapshot = root / "dump.rdb"
            snapshot.write_bytes(b"fake-rdb")
            archive = root / "backup-with-redis.zip"

            create_backup(data, archive, redis_snapshot=snapshot)

            _, manifest, payload = validate_archive(archive)
            self.assertEqual(manifest["redis"]["status"], "provided-file")
            self.assertEqual(payload["redis/dump.rdb"], b"fake-rdb")

    def test_restore_target_must_match_compose_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compose = root / "compose.yaml"

            self.assertEqual(_validate_target(root / "data", compose), root / "data")
            with self.assertRaises(ValueError):
                _validate_target(Path.home(), compose)
            with self.assertRaises(ValueError):
                _validate_target(root / "other-data", compose)

    @unittest.skipIf(os.name == "nt", "Windows 创建符号链接通常需要额外权限")
    def test_restore_target_rejects_a_symlinked_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            (root / "data").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(ValueError):
                _validate_target(root / "data", root / "compose.yaml")

    def test_archive_cannot_hide_an_env_payload_behind_manifest_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            (data / "safe.txt").write_text("safe", encoding="utf-8")
            original = root / "original.zip"
            malicious = root / "malicious.zip"
            create_backup(data, original, include_redis=False)

            with zipfile.ZipFile(original) as source:
                payload = {name: source.read(name) for name in source.namelist()}
            env_name = "data/.env"
            env_value = b"SECRET=must-not-be-restored"
            digest = hashlib.sha256(env_value).hexdigest()
            checksums = payload["checksums.sha256"].decode("utf-8") + f"{digest}  {env_name}\n"
            manifest = json.loads(payload["manifest.json"])
            manifest["files"].append({"path": env_name, "sha256": digest})
            manifest["files"] = sorted(manifest["files"], key=lambda item: item["path"])
            manifest["checksums_sha256"] = hashlib.sha256(checksums.encode()).hexdigest()
            with zipfile.ZipFile(malicious, "w") as output:
                output.writestr("manifest.json", json.dumps(manifest))
                output.writestr("checksums.sha256", checksums)
                output.writestr("data/safe.txt", payload["data/safe.txt"])
                output.writestr(env_name, env_value)

            with self.assertRaisesRegex(ValueError, "包含 .env"):
                validate_archive(malicious)

    def test_live_wal_database_uses_a_consistent_sqlite_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            database = data / "live.sqlite3"
            (data / "ordinary-wal").write_text("not a sqlite sidecar", encoding="utf-8")
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE songs(name TEXT NOT NULL)")
                connection.execute("INSERT INTO songs VALUES ('first')")
                connection.commit()
                connection.execute("INSERT INTO songs VALUES ('second')")
                connection.commit()
                archive = root / "sqlite.zip"
                create_backup(data, archive, include_redis=False)

            _, _, payload = validate_archive(archive)
            snapshot = root / "snapshot.sqlite3"
            snapshot.write_bytes(payload["data/live.sqlite3"])
            with closing(sqlite3.connect(snapshot)) as restored:
                names = [row[0] for row in restored.execute("SELECT name FROM songs ORDER BY rowid")]

            self.assertEqual(names, ["first", "second"])
            self.assertNotIn("data/live.sqlite3-wal", payload)
            self.assertNotIn("data/live.sqlite3-shm", payload)
            self.assertEqual(payload["data/ordinary-wal"], b"not a sqlite sidecar")


if __name__ == "__main__":
    unittest.main()
