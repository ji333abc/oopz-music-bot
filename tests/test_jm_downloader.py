from __future__ import annotations

import unittest
from pathlib import Path

from oopzbot.jm.downloader import _PACKAGED_WORKER, _worker_command


class JMWorkerCommandTests(unittest.TestCase):
    def test_packaged_worker_uses_module_entrypoint(self) -> None:
        self.assertEqual(
            _worker_command("python", str(_PACKAGED_WORKER), "--inspect", "123"),
            ["python", "-m", "oopzbot.jm_worker", "--inspect", "123"],
        )

    def test_external_worker_keeps_script_entrypoint(self) -> None:
        worker = Path("/custom/jm_worker.py")
        self.assertEqual(
            _worker_command("python", str(worker), "123", "/tmp/job"),
            ["python", str(worker), "123", "/tmp/job"],
        )


if __name__ == "__main__":
    unittest.main()
