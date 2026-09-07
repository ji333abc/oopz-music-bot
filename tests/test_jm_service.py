from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from oopzbot import jm_service
from oopzbot.jm.contracts import JMJob
from oopzbot.jm.queue import ClaimedJMJob


class JMWorkerProcessTests(unittest.IsolatedAsyncioTestCase):
    def claim(self, *, max_bytes: int = 1024) -> ClaimedJMJob:
        job = JMJob(
            job_id="a" * 32,
            album_id="123",
            requester_ref="masked",
            group_openid="group",
            message_id="message",
            password="password123",
            max_archive_bytes=max_bytes,
            timeout_seconds=60,
        )
        return ClaimedJMJob(job=job, raw="raw")

    async def test_success_uploads_and_always_cleans_job_directory(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            task_root = Path(root).resolve()

            def download(**kwargs):
                job_dir = kwargs["job_dir"]
                archive = job_dir / "archives" / "JM123.zip"
                archive.parent.mkdir(parents=True)
                archive.write_bytes(b"archive")
                return archive, {"page_count": 7}

            with (
                patch.object(jm_service, "TASK_ROOT", task_root),
                patch.object(jm_service, "download_album", side_effect=download),
                patch.object(jm_service, "upload_archive", new=AsyncMock()),
            ):
                result = await jm_service._process(self.claim())

            self.assertTrue(result["ok"])
            self.assertEqual(result["page_count"], 7)
            self.assertEqual(result["archive_bytes"], 7)
            self.assertFalse((task_root / ("a" * 32)).exists())

    async def test_size_limit_prevents_upload_and_cleans(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            task_root = Path(root).resolve()

            def download(**kwargs):
                job_dir = kwargs["job_dir"]
                archive = job_dir / "archives" / "JM123.zip"
                archive.parent.mkdir(parents=True)
                archive.write_bytes(b"too-large")
                return archive, {}

            upload = AsyncMock()
            with (
                patch.object(jm_service, "TASK_ROOT", task_root),
                patch.object(jm_service, "download_album", side_effect=download),
                patch.object(jm_service, "upload_archive", new=upload),
            ):
                result = await jm_service._process(self.claim(max_bytes=2))

            self.assertFalse(result["ok"])
            self.assertIn("size limit", result["error"])
            upload.assert_not_awaited()
            self.assertFalse((task_root / ("a" * 32)).exists())

    async def test_download_timeout_and_upload_failure_are_results(self) -> None:
        for failure in (
            subprocess.TimeoutExpired("worker", 60),
            RuntimeError("upload failed"),
        ):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as root:
                task_root = Path(root).resolve()
                if isinstance(failure, subprocess.TimeoutExpired):
                    download = patch.object(
                        jm_service, "download_album", side_effect=failure
                    )
                    upload = patch.object(
                        jm_service, "upload_archive", new=AsyncMock()
                    )
                else:
                    def downloaded(**kwargs):
                        job_dir = kwargs["job_dir"]
                        archive = job_dir / "archives" / "JM123.zip"
                        archive.parent.mkdir(parents=True)
                        archive.write_bytes(b"ok")
                        return archive, {}

                    download = patch.object(
                        jm_service, "download_album", side_effect=downloaded
                    )
                    upload = patch.object(
                        jm_service, "upload_archive", new=AsyncMock(side_effect=failure)
                    )
                with patch.object(jm_service, "TASK_ROOT", task_root), download, upload:
                    result = await jm_service._process(self.claim())

                self.assertFalse(result["ok"])
                self.assertFalse((task_root / ("a" * 32)).exists())


if __name__ == "__main__":
    unittest.main()
