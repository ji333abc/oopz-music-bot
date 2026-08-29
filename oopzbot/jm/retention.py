"""JM temporary-file retention policy."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path


async def cleanup_later(
    job_dir: Path,
    *,
    delay_seconds: int,
    logger: logging.Logger,
) -> None:
    await asyncio.sleep(delay_seconds)
    await asyncio.to_thread(shutil.rmtree, job_dir, True)
    logger.info("JM 失败任务文件已自动清理: %s", job_dir)


def cleanup_stale(
    temp_root: Path,
    *,
    retain_seconds: int,
    logger: logging.Logger,
) -> None:
    cutoff = time.time() - retain_seconds
    for job_dir in temp_root.glob("jm-*"):
        try:
            if job_dir.is_dir() and job_dir.stat().st_mtime <= cutoff:
                shutil.rmtree(job_dir)
                logger.info("已清理启动前遗留的 JM 失败任务: %s", job_dir)
        except OSError:
            logger.exception("清理遗留 JM 任务失败: %s", job_dir)
