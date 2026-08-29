"""JM worker subprocess adapter."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_PACKAGED_WORKER = Path(__file__).resolve().parents[1] / "jm_worker.py"
_PACKAGED_WORKER_MODULE = "oopzbot.jm_worker"


def _worker_command(python: str, worker: str, *arguments: str) -> list[str]:
    """Build a worker command without shadowing the stdlib ``http`` package.

    Running ``/app/oopzbot/jm_worker.py`` as a script puts ``/app/oopzbot``
    first on ``sys.path``.  That makes the local ``oopzbot/http`` package mask
    Python's standard-library ``http`` package.  The packaged worker must be
    started as a module instead.  Retain support for an explicitly configured
    external worker script for legacy deployments.
    """
    try:
        is_packaged_worker = Path(worker).resolve() == _PACKAGED_WORKER
    except OSError:
        is_packaged_worker = False
    if is_packaged_worker:
        return [python, "-m", _PACKAGED_WORKER_MODULE, *arguments]
    return [python, worker, *arguments]


def inspect_album(
    *,
    python: str,
    worker: str,
    album_id: str,
    environment: dict[str, str],
    timeout_seconds: int,
) -> int:
    result = subprocess.run(
        _worker_command(python, worker, "--inspect", album_id),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=environment,
        timeout=timeout_seconds,
        check=False,
    )
    for line in reversed(result.stdout.splitlines()):
        if not line.startswith("JM_METADATA="):
            continue
        metadata = json.loads(line.removeprefix("JM_METADATA="))
        page_count = int(metadata.get("page_count") or 0)
        if page_count > 0:
            return page_count
    detail = (result.stderr or result.stdout).strip().replace("\n", " ")[-500:]
    raise RuntimeError(detail or f"JM 元数据查询失败（退出码 {result.returncode}）")


def download_album(
    *,
    python: str,
    worker: str,
    album_id: str,
    job_dir: Path,
    environment: dict[str, str],
    timeout_seconds: int,
) -> tuple[Path, dict]:
    job_dir.mkdir(parents=True, exist_ok=False)
    log_path = job_dir / "download.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        result = subprocess.run(
            _worker_command(python, worker, album_id, str(job_dir)),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
            timeout=timeout_seconds,
            check=False,
        )
    if result.returncode != 0:
        try:
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        except OSError:
            detail = ""
        raise RuntimeError(
            f"JMComic 下载进程退出码 {result.returncode}"
            + (f"：{detail}" if detail else "")
        )

    archive = job_dir / "archives" / f"JM{album_id}.zip"
    if not archive.is_file():
        raise RuntimeError("下载完成但未找到 ZIP 文件")
    try:
        download_result = json.loads(
            (job_dir / "result.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        download_result = {}
    return archive, download_result
