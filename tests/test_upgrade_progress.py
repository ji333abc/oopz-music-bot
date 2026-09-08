import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import oopzctl


class UpgradeProgressTests(unittest.TestCase):
    def test_child_output_is_delivered_before_child_finishes(self):
        with tempfile.TemporaryDirectory() as name:
            acknowledgement = Path(name) / "received"
            lines = []
            def receive(line):
                lines.append(line)
                acknowledgement.write_text("ok")
            code = (
                "import pathlib,sys,time; print('building layer', flush=True); "
                "p=pathlib.Path(sys.argv[1]);\n"
                "while not p.exists(): time.sleep(0.01)\n"
                "print('done', flush=True)"
            )
            result = oopzctl._run(
                [sys.executable, "-c", code, str(acknowledgement)],
                timeout=5, check=True, on_output=receive,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(lines, ["building layer", "done"])

    def test_streamed_failure_is_redacted_and_raises(self):
        lines = []
        with self.assertRaises(RuntimeError) as raised:
            oopzctl._run(
                [sys.executable, "-c", "print('QQBOT_BRIDGE_TOKEN=private-value'); raise SystemExit(2)"],
                check=True, on_output=lines.append,
            )
        self.assertNotIn("private-value", " ".join(lines) + str(raised.exception))

    def test_streamed_process_timeout_remains_enforced(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            oopzctl._run([sys.executable, "-c", "import time; time.sleep(20)"],
                         timeout=0.2, on_output=lambda _: None)

    def test_tail_is_bounded_while_all_lines_are_streamed(self):
        lines = []
        result = oopzctl._run(
            [sys.executable, "-c", "for i in range(200): print(i)"],
            on_output=lines.append,
        )
        self.assertEqual(len(lines), 200)
        self.assertEqual(len(result.stdout.splitlines()), 64)

    def test_cli_keeps_json_on_stdout_and_progress_on_stderr(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        def upgrade(*args, progress, **kwargs):
            progress.step(4, "构建镜像")
            progress.log("building layer")
            progress.step(8, "升级完成")
            return {"ok": True}
        with patch.object(oopzctl, "_upgrade", side_effect=upgrade), patch(
            "sys.stdout", stdout
        ), patch("sys.stderr", stderr):
            self.assertEqual(oopzctl.main(["upgrade"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"ok": True})
        self.assertIn("阶段 8/8", stderr.getvalue())
        self.assertIn("building layer", stderr.getvalue())
        self.assertNotIn("\033", stderr.getvalue())

    def test_failed_stage_does_not_show_completed_bar(self):
        output = io.StringIO()
        with patch("sys.stderr", output), self.assertRaises(RuntimeError):
            with oopzctl.UpgradeProgress() as progress:
                progress.step(4, "构建镜像")
                raise RuntimeError("disk full")
        self.assertIn("失败", output.getvalue())
        self.assertNotIn("阶段 8/8", output.getvalue())
