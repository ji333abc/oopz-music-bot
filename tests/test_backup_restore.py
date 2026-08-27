from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.backup import create_backup
from scripts.restore import restore_backup, validate_archive


class BackupRestoreTests(unittest.TestCase):
    def test_data_backup_restore_and_checksum_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            (data / "queue.json").write_text('{"items":["song"]}', encoding="utf-8")
            (data / ".env").write_text("SECRET=must-not-be-archived", encoding="utf-8")
            archive = root / "backup.zip"

            create_backup(data, archive, include_redis=False)
            (data / "queue.json").write_text('{"items":[]}', encoding="utf-8")
            (data / "extra.txt").write_text("remove", encoding="utf-8")
            recovery = restore_backup(archive, data, component="data")

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
            create_backup(data, archive, include_redis=False)
            corrupt = root / "corrupt.zip"
            corrupt.write_bytes(archive.read_bytes().replace(b"keep", b"changed", 1))
            with self.assertRaises(ValueError):
                restore_backup(corrupt, data, component="data")
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


if __name__ == "__main__":
    unittest.main()
