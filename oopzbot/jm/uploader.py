"""QQ file uploader subprocess adapter."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path


class JMUploadError(RuntimeError):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


async def _pump_stderr(stream: asyncio.StreamReader, logger: logging.Logger) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            logger.info("JMUploader: %s", text)


async def upload_archive(
    *,
    node: str,
    uploader: str,
    archive: Path,
    group_openid: str,
    message_id: str,
    display_name: str,
    environment: dict[str, str],
    timeout_seconds: int,
    logger: logging.Logger,
) -> dict:
    process = await asyncio.create_subprocess_exec(
        node,
        uploader,
        "--group-openid",
        group_openid,
        "--msg-id",
        message_id,
        "--file",
        str(archive.resolve()),
        "--name",
        display_name,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_task = asyncio.create_task(process.stdout.read())
    stderr_task = asyncio.create_task(_pump_stderr(process.stderr, logger))
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise JMUploadError("timeout", f"QQ 文件上传超过 {timeout_seconds} 秒") from exc
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    finally:
        stdout_bytes = await stdout_task
        await stderr_task

    output_lines = [
        line.strip()
        for line in stdout_bytes.decode("utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    if not output_lines:
        raise JMUploadError("api", f"QQ 上传器未返回结果（退出码 {process.returncode}）")
    try:
        result = json.loads(output_lines[-1])
    except ValueError as exc:
        raise JMUploadError("api", "QQ 上传器返回了无效结果") from exc
    if process.returncode != 0 or result.get("ok") is not True:
        error_type = str(result.get("errorType") or "api")
        message = str(result.get("message") or "QQ 文件上传失败")
        raise JMUploadError(error_type, message[-500:])
    return result
