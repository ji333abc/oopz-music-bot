"""Long-running isolated JM download and upload worker."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from .jm.downloader import download_album
from .jm.queue import ClaimedJMJob, RedisJMQueue
from .jm.uploader import upload_archive
from .process_env import minimal_child_environment

logger = logging.getLogger("JMWorkerService")
TASK_ROOT = Path(os.getenv("QQBOT_JM_TEMP_ROOT", "/app/data/jm-tasks")).resolve()


def _safe_job_dir(job_id: str) -> Path:
    root = TASK_ROOT.resolve()
    target = (root / job_id).resolve()
    if target.parent != root:
        raise ValueError("JM task path escaped task root")
    return target


async def _process(claim: ClaimedJMJob) -> dict:
    job = claim.job
    job_dir = _safe_job_dir(job.job_id)
    job_dir.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        worker_env = minimal_child_environment(("QQBOT_JM_MAX_BYTES",))
        worker_env["JM_ZIP_PASSWORD"] = job.password
        worker_env["QQBOT_JM_MAX_BYTES"] = str(job.max_archive_bytes)
        archive, metadata = await asyncio.to_thread(
            download_album,
            python=sys.executable,
            worker=str(Path(__file__).with_name("jm_worker.py")),
            album_id=job.album_id,
            job_dir=job_dir,
            environment=worker_env,
            timeout_seconds=job.timeout_seconds,
        )
        if archive.stat().st_size > job.max_archive_bytes:
            raise RuntimeError("JM archive exceeds configured size limit")
        uploader = os.getenv(
            "QQBOT_JM_UPLOADER", "/app/tools/qqbot-uploader/uploader.mjs"
        )
        await upload_archive(
            node=os.getenv("QQBOT_JM_NODE", "node"),
            uploader=uploader,
            archive=archive,
            group_openid=job.group_openid,
            message_id=job.message_id,
            display_name=f"JM{job.album_id}.zip",
            environment=minimal_child_environment(
                ("QQBOT_APP_ID", "QQBOT_APP_SECRET", "QQBOT_JM_MAX_BYTES")
            ),
            timeout_seconds=int(os.getenv("QQBOT_JM_UPLOAD_TIMEOUT_SECONDS", "900")),
            logger=logger,
        )
        return {
            "ok": True,
            "job_id": job.job_id,
            "album_id": job.album_id,
            "password": job.password,
            "seconds": int(time.monotonic() - started + 0.999),
            "page_count": int(metadata.get("page_count") or 0),
            "archive_bytes": archive.stat().st_size,
        }
    except Exception as exc:
        logger.exception("JM worker job failed job_id=%s", job.job_id)
        return {
            "ok": False,
            "job_id": job.job_id,
            "album_id": job.album_id,
            "error": str(exc).replace("\n", " ")[-500:],
        }
    finally:
        await asyncio.to_thread(shutil.rmtree, job_dir, True)


async def run() -> None:
    queue = RedisJMQueue()
    await asyncio.to_thread(queue.recover_stale)

    async def heartbeat() -> None:
        while True:
            await asyncio.to_thread(queue.heartbeat)
            await asyncio.sleep(5)

    async def recovery() -> None:
        # A worker may restart before the previous process' lease expires.  Keep
        # scanning so that job is requeued when the lease eventually lapses.
        while True:
            await asyncio.sleep(15)
            recovered = await asyncio.to_thread(queue.recover_stale)
            if recovered:
                logger.warning("requeued %s stale JM job(s)", recovered)

    heartbeat_task = asyncio.create_task(heartbeat())
    recovery_task = asyncio.create_task(recovery())
    try:
        while True:
            claim = await asyncio.to_thread(queue.claim, timeout=5)
            if claim is None:
                continue
            async def renew_lease(active_claim: ClaimedJMJob = claim) -> None:
                interval = max(5, min(30, active_claim.job.lease_seconds // 3))
                while True:
                    await asyncio.sleep(interval)
                    if not await asyncio.to_thread(queue.renew, active_claim):
                        logger.error(
                            "lost JM job lease job_id=%s",
                            active_claim.job.job_id,
                        )
                        return

            lease_task = asyncio.create_task(renew_lease())
            try:
                result = await _process(claim)
            finally:
                lease_task.cancel()
                await asyncio.gather(lease_task, return_exceptions=True)
            completed = await asyncio.to_thread(queue.complete, claim, result)
            if not completed:
                logger.error("discarded stale JM result job_id=%s", claim.job.job_id)
    finally:
        heartbeat_task.cancel()
        recovery_task.cancel()
        await asyncio.gather(
            heartbeat_task, recovery_task, return_exceptions=True
        )


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    asyncio.run(run())


if __name__ == "__main__":
    main()
