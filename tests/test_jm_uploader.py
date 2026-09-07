from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

from oopzbot.jm.uploader import JMUploadError, upload_archive
from oopzbot.process_env import minimal_child_environment


class JMUploadAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = self.root / "JM123.zip"
        self.archive.write_bytes(b"zip-test")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _uploader(self, body: str) -> str:
        script = self.root / "fake_uploader.py"
        script.write_text(body, encoding="utf-8")
        return str(script)

    async def _upload(self, uploader: str, *, timeout: float = 5) -> dict:
        return await upload_archive(
            node=sys.executable,
            uploader=uploader,
            archive=self.archive,
            group_openid="group-1",
            message_id="message-1",
            display_name="JM123.zip",
            environment=minimal_child_environment(()),
            timeout_seconds=timeout,
            logger=logging.getLogger("JMUploadAdapterTests"),
        )

    async def test_success_result_is_returned(self) -> None:
        uploader = self._uploader(
            "import json\n"
            "print(json.dumps({'ok': True, 'fileUuid': 'uuid-1', 'ttl': 60}))\n"
        )

        result = await self._upload(uploader)

        self.assertEqual(result["fileUuid"], "uuid-1")

    async def test_structured_failure_becomes_upload_error(self) -> None:
        uploader = self._uploader(
            "import json, sys\n"
            "print(json.dumps({'ok': False, 'errorType': 'quota', 'message': 'daily limit'}))\n"
            "sys.exit(1)\n"
        )

        with self.assertRaises(JMUploadError) as raised:
            await self._upload(uploader)

        self.assertEqual(raised.exception.error_type, "quota")

    async def test_timeout_kills_uploader(self) -> None:
        uploader = self._uploader("import time\ntime.sleep(10)\n")

        with self.assertRaises(JMUploadError) as raised:
            await self._upload(uploader, timeout=0.05)

        self.assertEqual(raised.exception.error_type, "timeout")


if __name__ == "__main__":
    unittest.main()
