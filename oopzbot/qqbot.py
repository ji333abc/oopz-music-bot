"""QQ 官方群机器人客户端：把群 @ 消息转发给本机 Oopzbot。"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import secrets
import shutil
import statistics
import subprocess
import time
from pathlib import Path
from threading import Lock

import botpy
import requests
from botpy.message import GroupMessage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("QQBotService")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

APP_ID = (os.getenv("QQBOT_APP_ID") or "").strip()
APP_SECRET = (os.getenv("QQBOT_APP_SECRET") or "").strip()
BRIDGE_TOKEN = (os.getenv("QQBOT_BRIDGE_TOKEN") or "").strip()
BRIDGE_URL = (
    os.getenv("QQBOT_BRIDGE_URL")
    or (
        f"http://{os.getenv('OOPZBOT_BRIDGE_HOST', '127.0.0.1')}:"
        f"{os.getenv('OOPZBOT_BRIDGE_PORT', '18080')}/internal/qqbot/command"
    )
).strip()
FILE_TEST_URL = (os.getenv("QQBOT_FILE_TEST_URL") or "").strip()
JM_ENABLED = (os.getenv("QQBOT_JM_ENABLED") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
JM_PYTHON = (
    os.getenv("QQBOT_JM_PYTHON")
    or os.getenv("PYTHON", "")
    or __import__("sys").executable
).strip()
JM_WORKER = (
    os.getenv("QQBOT_JM_WORKER")
    or str(Path(__file__).with_name("jm_worker.py"))
).strip()
JM_TEMP_ROOT = Path(
    os.getenv("QQBOT_JM_TEMP_ROOT") or str(_PROJECT_ROOT / "data" / "jm-tasks")
)
JM_MAX_BYTES = int(os.getenv("QQBOT_JM_MAX_BYTES") or str(80 * 1024 * 1024))
JM_TIMEOUT_SECONDS = int(os.getenv("QQBOT_JM_TIMEOUT_SECONDS") or "1200")
JM_NODE = (os.getenv("QQBOT_JM_NODE") or "node").strip()
JM_UPLOADER = (
    os.getenv("QQBOT_JM_UPLOADER")
    or str(_PROJECT_ROOT / "tools" / "qqbot-uploader" / "uploader.mjs")
).strip()
JM_UPLOAD_TIMEOUT_SECONDS = int(
    os.getenv("QQBOT_JM_UPLOAD_TIMEOUT_SECONDS") or "900"
)
JM_FAILURE_RETAIN_SECONDS = int(
    os.getenv("QQBOT_JM_FAILURE_RETAIN_SECONDS") or "1800"
)
JM_INSPECT_TIMEOUT_SECONDS = int(
    os.getenv("QQBOT_JM_INSPECT_TIMEOUT_SECONDS") or "30"
)
JM_BATCH_MAX_ITEMS = max(
    1,
    int(os.getenv("QQBOT_JM_BATCH_MAX_ITEMS") or "3"),
)
JM_TIMING_PATH = Path(
    os.getenv("QQBOT_JM_TIMING_PATH")
    or str(_PROJECT_ROOT / "data" / "jm_timing.json")
)
JM_ALLOWED_USERS = {
    item.strip()
    for item in (os.getenv("QQBOT_JM_ALLOWED_USER_OPENIDS") or "").split(",")
    if item.strip()
}
ALLOWED_GROUPS = {
    item.strip()
    for item in (os.getenv("QQBOT_ALLOWED_GROUP_OPENIDS") or "").split(",")
    if item.strip()
}

_seen_messages: dict[str, float] = {}
_seen_lock = Lock()
_jm_job_lock = Lock()
_jm_timing_lock = Lock()
_jm_tasks: set[asyncio.Task] = set()


def _parse_jm_album_ids(command: str) -> list[str]:
    match = re.fullmatch(
        r"(?:jm|JM|jm下载|JM下载)\s*(\d{1,12}(?:\s+\d{1,12})*)",
        str(command or "").strip(),
    )
    if not match:
        return []

    # 同一条消息中的重复 ID 只处理一次，并保留用户输入顺序。
    return list(dict.fromkeys(re.findall(r"\d{1,12}", match.group(1))))


def _inspect_jm_album(album_id: str) -> int:
    result = subprocess.run(
        [JM_PYTHON, JM_WORKER, "--inspect", album_id],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=JM_INSPECT_TIMEOUT_SECONDS,
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


def _load_jm_timing_samples() -> list[dict]:
    try:
        data = json.loads(JM_TIMING_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    samples = data.get("samples", []) if isinstance(data, dict) else []
    return [item for item in samples if isinstance(item, dict)][-20:]


def _median_or_default(values: list[float], default: float) -> float:
    usable = [value for value in values if math.isfinite(value) and value > 0]
    return statistics.median(usable) if usable else default


def _estimate_jm_seconds(page_count: int) -> int:
    samples = _load_jm_timing_samples()
    sampled_pages = sum(
        max(0, int(item.get("page_count") or 0)) for item in samples
    )
    # A small album contains proportionally more connection/setup overhead and can
    # badly overestimate a large album.  Blend in history gradually until roughly
    # 200 successfully processed pages have been observed.
    confidence = min(1.0, sampled_pages / 200)

    def blended(observed: float, default: float) -> float:
        return default * (1 - confidence) + observed * confidence

    observed_download_per_page = _median_or_default(
        [
            float(item.get("download_seconds", 0)) / float(item.get("page_count", 0))
            for item in samples
            if float(item.get("page_count", 0)) > 0
        ],
        0.20,
    )
    download_per_page = blended(observed_download_per_page, 0.20)
    observed_processing_per_page = _median_or_default(
        [
            float(item.get("processing_seconds", 0))
            / float(item.get("page_count", 0))
            for item in samples
            if float(item.get("page_count", 0)) > 0
        ],
        0.30,
    )
    processing_per_page = blended(observed_processing_per_page, 0.30)
    observed_bytes_per_page = _median_or_default(
        [
            float(item.get("archive_bytes", 0)) / float(item.get("page_count", 0))
            for item in samples
            if float(item.get("page_count", 0)) > 0
        ],
        160 * 1024,
    )
    bytes_per_page = blended(observed_bytes_per_page, 160 * 1024)
    observed_upload_bytes_per_second = _median_or_default(
        [
            float(item.get("archive_bytes", 0))
            / float(item.get("upload_seconds", 0))
            for item in samples
            if float(item.get("upload_seconds", 0)) > 0
        ],
        2 * 1024 * 1024,
    )
    upload_bytes_per_second = blended(
        observed_upload_bytes_per_second,
        2 * 1024 * 1024,
    )
    observed_fixed_seconds = _median_or_default(
        [
            max(
                0.0,
                float(item.get("total_seconds", 0))
                - float(item.get("download_seconds", 0))
                - float(item.get("processing_seconds", 0))
                - float(item.get("upload_seconds", 0)),
            )
            for item in samples
            if float(item.get("total_seconds", 0)) > 0
        ],
        15.0,
    )
    fixed_seconds = blended(observed_fixed_seconds, 15.0)
    seconds = (
        fixed_seconds
        + page_count * download_per_page
        + page_count * processing_per_page
        + page_count * bytes_per_page / upload_bytes_per_second
    )
    return max(30, int(math.ceil(seconds / 10) * 10))


def _record_jm_timing(sample: dict) -> None:
    with _jm_timing_lock:
        samples = _load_jm_timing_samples()
        samples.append(sample)
        JM_TIMING_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = JM_TIMING_PATH.with_suffix(JM_TIMING_PATH.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"samples": samples[-20:]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(JM_TIMING_PATH)


def _run_jm_download(album_id: str, password: str, job_dir: Path) -> tuple[Path, dict]:
    job_dir.mkdir(parents=True, exist_ok=False)
    log_path = job_dir / "download.log"
    environment = os.environ.copy()
    environment["JM_ZIP_PASSWORD"] = password

    with log_path.open("w", encoding="utf-8") as log_file:
        result = subprocess.run(
            [JM_PYTHON, JM_WORKER, album_id, str(job_dir)],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
            timeout=JM_TIMEOUT_SECONDS,
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
    result_path = job_dir / "result.json"
    try:
        download_result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        download_result = {}
    return archive, download_result


class JMUploadError(RuntimeError):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


async def _pump_uploader_stderr(stream: asyncio.StreamReader) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            logger.info("JMUploader: %s", text)


async def _run_jm_upload(
    archive: Path,
    group_openid: str,
    message_id: str,
    display_name: str,
) -> dict:
    process = await asyncio.create_subprocess_exec(
        JM_NODE,
        JM_UPLOADER,
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
        env=os.environ.copy(),
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_task = asyncio.create_task(process.stdout.read())
    stderr_task = asyncio.create_task(_pump_uploader_stderr(process.stderr))

    try:
        await asyncio.wait_for(
            process.wait(),
            timeout=JM_UPLOAD_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise JMUploadError(
            "timeout",
            f"QQ 文件上传超过 {JM_UPLOAD_TIMEOUT_SECONDS} 秒",
        ) from exc
    finally:
        stdout_bytes = await stdout_task
        await stderr_task

    output_lines = [
        line.strip()
        for line in stdout_bytes.decode("utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    if not output_lines:
        raise JMUploadError(
            "api",
            f"QQ 上传器未返回结果（退出码 {process.returncode}）",
        )

    try:
        result = json.loads(output_lines[-1])
    except ValueError as exc:
        raise JMUploadError("api", "QQ 上传器返回了无效结果") from exc

    if process.returncode != 0 or result.get("ok") is not True:
        error_type = str(result.get("errorType") or "api")
        message = str(result.get("message") or "QQ 文件上传失败")
        raise JMUploadError(error_type, message[-500:])
    return result


async def _cleanup_jm_job_later(
    job_dir: Path,
    delay_seconds: int = JM_FAILURE_RETAIN_SECONDS,
) -> None:
    await asyncio.sleep(delay_seconds)
    await asyncio.to_thread(shutil.rmtree, job_dir, True)
    logger.info("JM 失败任务文件已自动清理: %s", job_dir)


def _cleanup_stale_jm_jobs() -> None:
    cutoff = time.time() - JM_FAILURE_RETAIN_SECONDS
    for job_dir in JM_TEMP_ROOT.glob("jm-*"):
        try:
            if job_dir.is_dir() and job_dir.stat().st_mtime <= cutoff:
                shutil.rmtree(job_dir)
                logger.info("已清理启动前遗留的 JM 失败任务: %s", job_dir)
        except OSError:
            logger.exception("清理遗留 JM 任务失败: %s", job_dir)


def _is_duplicate(message_id: str) -> bool:
    now = time.monotonic()
    with _seen_lock:
        expired = [key for key, value in _seen_messages.items() if now - value > 600]
        for key in expired:
            _seen_messages.pop(key, None)
        if message_id in _seen_messages:
            return True
        _seen_messages[message_id] = now
        return False


def _forward_command(
    command: str,
    requester_id: str,
    requester_name: str,
    group_openid: str,
) -> dict:
    response = requests.post(
        BRIDGE_URL,
        json={
            "command": command,
            "requester_id": requester_id,
            "requester_name": requester_name,
            "group_openid": group_openid,
        },
        headers={"X-QQBot-Bridge-Token": BRIDGE_TOKEN},
        timeout=180,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"ok": False, "message": f"Oopzbot 返回 HTTP {response.status_code}"}
    if response.status_code >= 400 and payload.get("ok") is not False:
        payload["ok"] = False
    return payload


class OopzQQClient(botpy.Client):
    async def on_ready(self):
        logger.info("QQ 机器人已上线: %s", self.robot.name)
        if not ALLOWED_GROUPS:
            logger.warning(
                "尚未设置 QQBOT_ALLOWED_GROUP_OPENIDS；当前会接受机器人所在群的命令"
            )

    async def _reply(
        self,
        message: GroupMessage,
        content: str,
        msg_seq: int = 1,
    ) -> None:
        payload = {
            "group_openid": message.group_openid,
            "msg_type": 0,
            "msg_id": message.id,
            "msg_seq": msg_seq,
            "content": content[:1000],
        }
        try:
            await message._api.post_group_message(**payload)
        except Exception as exc:
            error_text = str(exc).replace(" ", "").lower()
            if "msgid已经过期" not in error_text and not (
                "msgid" in error_text and "过期" in error_text
            ):
                raise

            logger.warning(
                "被动回复消息已过期，改为主动群消息发送: group_openid=%s",
                message.group_openid,
            )
            payload.pop("msg_id", None)
            payload.pop("msg_seq", None)
            await message._api.post_group_message(**payload)

    @staticmethod
    def _search_markdown(result: dict) -> str:
        songs = result.get("songs") or []
        lines = ["# QQ音乐搜歌", ""]
        for song in songs:
            index = int(song.get("index") or 0)
            name = str(song.get("name") or "未知歌曲")
            artists = str(song.get("artists") or "未知歌手")
            duration = str(song.get("duration") or "")
            suffix = f" · {duration}" if duration else ""
            lines.append(f"{index}. **{name}** — {artists}{suffix}")
        lines.extend(["", "> 5分钟内点击下方按钮选择歌曲"])
        return "\n".join(lines)

    @staticmethod
    def _search_keyboard(result: dict, requester_id: str) -> dict:
        buttons = []
        for song in result.get("songs") or []:
            index = int(song.get("index") or 0)
            name = str(song.get("name") or "未知歌曲")
            label = f"{index}. {name}"[:12]
            buttons.append(
                {
                    "id": f"select_song_{index}",
                    "render_data": {
                        "label": label,
                        "visited_label": label,
                        "style": 1,
                    },
                    "action": {
                        "type": 2,
                        "permission": {
                            "type": 0,
                            "specify_user_ids": [requester_id],
                        },
                        "click_limit": 1,
                        "data": f"选歌 {index}",
                        "at_bot_show_channel_list": False,
                    },
                }
            )

        rows = [
            {"buttons": buttons[offset : offset + 2]}
            for offset in range(0, len(buttons), 2)
        ]
        return {"content": {"rows": rows}}

    @staticmethod
    def _command_keyboard(
        items: list[dict],
        requester_id: str,
        command_prefix: str,
        buttons_per_row: int = 2,
    ) -> dict:
        buttons = []
        for item in items:
            value = str(item.get("value") or "")
            label = str(item.get("label") or value)[:12]
            command = str(item.get("command") or f"{command_prefix} {value}")
            buttons.append(
                {
                    "id": f"{command_prefix}_{value}".replace(" ", "_"),
                    "render_data": {
                        "label": label,
                        "visited_label": label,
                        "style": 1,
                    },
                    "action": {
                        "type": 2,
                        "permission": {
                            "type": 0,
                            "specify_user_ids": [requester_id],
                        },
                        "click_limit": 1,
                        "data": command,
                        "at_bot_show_channel_list": False,
                    },
                }
            )
        return {
            "content": {
                "rows": [
                    {"buttons": buttons[offset : offset + buttons_per_row]}
                    for offset in range(0, len(buttons), buttons_per_row)
                ]
            }
        }

    async def _reply_rank_catalog(
        self,
        message: GroupMessage,
        result: dict,
        requester_id: str,
    ) -> None:
        ranks = result.get("ranks") or []
        lines = ["# QQ音乐排行榜", ""]
        for rank in ranks:
            lines.append(
                f"- `{rank.get('id', '')}` {rank.get('title', '未知榜单')}"
            )
        lines.extend(["", "> 点击常用榜单，或发送：榜单 ID/名称"])
        items = [
            {
                "value": str(rank.get("id") or ""),
                "label": str(rank.get("title") or "未知榜单")
                .replace("巅峰榜·", ""),
            }
            for rank in (result.get("button_ranks") or ranks[:6])
        ]
        try:
            await message._api.post_group_message(
                group_openid=message.group_openid,
                msg_type=2,
                msg_id=message.id,
                msg_seq=1,
                markdown={"content": "\n".join(lines)},
                keyboard=self._command_keyboard(
                    items,
                    requester_id,
                    "榜单",
                ),
            )
        except Exception as exc:
            logger.warning("QQ 排行榜菜单发送失败，退回纯文本: %s", exc)
            await self._reply(message, str(result.get("message") or "排行榜不可用"))

    async def _reply_rank_results(
        self,
        message: GroupMessage,
        result: dict,
        requester_id: str,
    ) -> None:
        title = str(result.get("title") or "QQ音乐排行榜")
        songs = result.get("songs") or []
        lines = [f"# {title}", ""]
        items = []
        for song in songs:
            rank = int(song.get("rank") or 0)
            name = str(song.get("title") or "未知歌曲")
            artists = str(song.get("artists") or "未知歌手")
            lines.append(f"{rank}. **{name}** — {artists}")
            items.append({"value": str(rank), "label": f"{rank}. {name}"})
        items.append(
            {
                "value": "batch10",
                "label": "前10首加入队列",
                "command": "榜单批量 10",
            }
        )
        lines.extend(["", "> 可单首点歌，也可将前10首批量加入队列"])
        try:
            await message._api.post_group_message(
                group_openid=message.group_openid,
                msg_type=2,
                msg_id=message.id,
                msg_seq=1,
                markdown={"content": "\n".join(lines)},
                keyboard=self._command_keyboard(
                    items,
                    requester_id,
                    "榜单点歌",
                    buttons_per_row=3,
                ),
            )
        except Exception as exc:
            logger.warning("QQ 排行榜详情发送失败，退回纯文本: %s", exc)
            await self._reply(message, str(result.get("message") or "榜单不可用"))

    async def _reply_search_results(
        self,
        message: GroupMessage,
        result: dict,
        requester_id: str,
    ) -> None:
        try:
            await message._api.post_group_message(
                group_openid=message.group_openid,
                msg_type=2,
                msg_id=message.id,
                msg_seq=1,
                markdown={"content": self._search_markdown(result)},
                keyboard=self._search_keyboard(result, requester_id),
            )
        except Exception as exc:
            logger.warning("QQ Markdown/选歌按钮发送失败，退回纯文本: %s", exc)
            await self._reply(
                message,
                str(result.get("message") or "未找到歌曲"),
            )

    async def _reply_song_selected(
        self,
        message: GroupMessage,
        result: dict,
    ) -> None:
        song = result.get("song") or {}
        name = str(song.get("name") or "未知歌曲")
        artists = str(song.get("artists") or "未知歌手")
        album = str(song.get("album") or "未知专辑")
        duration = str(song.get("duration") or "未知")
        cover = str(song.get("cover") or "").strip()
        markdown = (
            "## 已选择歌曲\n"
            f"**{name}**\n\n"
            f"- 歌手：{artists}\n"
            f"- 专辑：{album}\n"
            f"- 时长：{duration}"
        )
        fallback = (
            f"已选择：{name} - {artists}\n"
            f"专辑：{album}\n"
            f"时长：{duration}"
        )

        if cover:
            try:
                media = await message._api.post_group_file(
                    group_openid=message.group_openid,
                    file_type=1,
                    url=cover,
                )
                await message._api.post_group_message(
                    group_openid=message.group_openid,
                    msg_type=1,
                    msg_id=message.id,
                    msg_seq=1,
                    content=fallback,
                    media=media,
                )
                return
            except Exception as exc:
                logger.warning("QQ 图文混排发送失败，退回单条文字消息: %s", exc)

        try:
            await message._api.post_group_message(
                group_openid=message.group_openid,
                msg_type=2,
                msg_id=message.id,
                msg_seq=1,
                markdown={"content": markdown},
            )
        except Exception as exc:
            logger.warning("QQ Markdown 歌曲信息发送失败，退回纯文本: %s", exc)
            await self._reply(message, fallback, msg_seq=1)

    async def _reply_result(
        self,
        message: GroupMessage,
        result: dict,
        requester_id: str,
    ) -> None:
        reply_type = str(result.get("reply_type") or "")
        if reply_type == "search_results" and result.get("songs"):
            await self._reply_search_results(message, result, requester_id)
            return
        if reply_type == "rank_catalog" and result.get("ranks"):
            await self._reply_rank_catalog(message, result, requester_id)
            return
        if reply_type == "rank_results" and result.get("songs"):
            await self._reply_rank_results(message, result, requester_id)
            return
        if reply_type == "song_selected" and result.get("song"):
            await self._reply_song_selected(message, result)
            return
        await self._reply(
            message,
            str(result.get("message") or "命令已处理"),
        )

    async def _run_jm_job(
        self,
        message: GroupMessage,
        album_id: str,
        page_count: int | None = None,
        *,
        send_result: bool = True,
        release_lock: bool = True,
    ) -> dict:
        job_dir = JM_TEMP_ROOT / f"jm-{album_id}-{secrets.token_hex(8)}"
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        password = "".join(secrets.choice(alphabet) for _ in range(14))
        keep_for_debug = False
        archive_created = False
        job_started = time.monotonic()

        try:
            archive, download_result = await asyncio.to_thread(
                _run_jm_download,
                album_id,
                password,
                job_dir,
            )
            archive_created = True
            archive_size = archive.stat().st_size
            if archive_size > JM_MAX_BYTES:
                limit_mib = JM_MAX_BYTES / 1024 / 1024
                actual_mib = archive_size / 1024 / 1024
                raise RuntimeError(
                    f"压缩包 {actual_mib:.1f} MiB，超过 {limit_mib:.0f} MiB 上限"
                )

            logger.info(
                "开始通过 QQ 分片接口上传 JM 文件: album_id=%s size=%s",
                album_id,
                archive_size,
            )
            upload_started = time.monotonic()
            await _run_jm_upload(
                archive=archive,
                group_openid=str(message.group_openid),
                message_id=str(message.id),
                display_name=f"JM{album_id}.zip",
            )
            upload_seconds = time.monotonic() - upload_started
            measured_pages = int(download_result.get("page_count") or 0)
            timing_pages = page_count or measured_pages
            total_seconds = time.monotonic() - job_started
            if timing_pages > 0:
                await asyncio.to_thread(
                    _record_jm_timing,
                    {
                        "timestamp": int(time.time()),
                        "album_id": album_id,
                        "page_count": timing_pages,
                        "download_seconds": float(
                            download_result.get("download_seconds") or 0
                        ),
                        "processing_seconds": float(
                            download_result.get("processing_seconds") or 0
                        ),
                        "upload_seconds": round(upload_seconds, 3),
                        "archive_bytes": archive_size,
                        "total_seconds": round(total_seconds, 3),
                    },
                )
            failed_images = int(download_result.get("failed_images") or 0)
            failed_photos = int(download_result.get("failed_photos") or 0)
            output_format = str(download_result.get("output_format") or "images")
            pdf_quality = download_result.get("pdf_quality")
            fallback_reason = str(download_result.get("fallback_reason") or "").strip()
            if output_format == "pdf":
                content_note = f"\n内容：PDF（质量 {pdf_quality}）"
            else:
                content_note = "\n内容：原始图片"
                if fallback_reason:
                    content_note += "（PDF 生成失败或超过大小限制）"
            warning = ""
            if failed_images or failed_photos:
                warning = (
                    f"\n⚠️ 本次有 {failed_images} 张图片、"
                    f"{failed_photos} 个章节下载失败，压缩包可能不完整"
                )
            if send_result:
                await self._reply(
                    message,
                    f"JM{album_id}.zip 上传完成"
                    f"{content_note}\n解压密码：{password}"
                    f"\n实际用时：{math.ceil(total_seconds)} 秒{warning}",
                    msg_seq=3,
                )
            logger.info(
                "JM 任务完成: album_id=%s size=%s",
                album_id,
                archive_size,
            )
            return {
                "ok": True,
                "album_id": album_id,
                "password": password,
                "seconds": math.ceil(total_seconds),
                "warning": warning.strip(),
            }
        except subprocess.TimeoutExpired:
            logger.warning("JM 任务超时: album_id=%s", album_id)
            error_text = "下载超时，任务已停止"
            if send_result:
                await self._reply(
                    message,
                    f"JM{album_id} {error_text}",
                    msg_seq=2,
                )
            return {"ok": False, "album_id": album_id, "error": error_text}
        except Exception as exc:
            logger.exception("JM 任务失败: album_id=%s", album_id)
            if archive_created:
                keep_for_debug = True
                logger.error(
                    "JM 失败任务文件保留 %s 秒: %s",
                    JM_FAILURE_RETAIN_SECONDS,
                    job_dir,
                )
            if isinstance(exc, JMUploadError):
                error_messages = {
                    "quota": "QQ 文件上传今日额度已用完，请明天再试",
                    "auth": "QQ 文件上传认证失败，请联系管理员检查配置",
                    "size": "文件超过 QQ 平台允许的大小",
                    "timeout": "QQ 文件上传超时，请稍后再试",
                    "network": "QQ 文件上传网络异常，请稍后再试",
                }
                error_text = error_messages.get(
                    exc.error_type,
                    f"QQ 文件上传失败：{str(exc)}",
                )
            else:
                error_text = str(exc).replace("\n", " ")[-500:]
            if send_result:
                await self._reply(
                    message,
                    f"JM{album_id} 任务失败：{error_text}",
                    msg_seq=2,
                )
            return {"ok": False, "album_id": album_id, "error": error_text}
        finally:
            if keep_for_debug:
                cleanup_task = asyncio.create_task(
                    _cleanup_jm_job_later(job_dir)
                )
                _jm_tasks.add(cleanup_task)
                cleanup_task.add_done_callback(_jm_tasks.discard)
            else:
                await asyncio.to_thread(shutil.rmtree, job_dir, True)
            if release_lock:
                _jm_job_lock.release()

    async def _run_jm_batch(
        self,
        message: GroupMessage,
        jobs: list[tuple[str, int | None]],
    ) -> None:
        batch_started = time.monotonic()
        try:
            for index, (album_id, page_count) in enumerate(jobs, start=1):
                result = await self._run_jm_job(
                    message,
                    album_id,
                    page_count,
                    send_result=False,
                    release_lock=False,
                )
                if result.get("ok"):
                    lines = [
                        f"✅ [{index}/{len(jobs)}] JM{album_id}.zip 上传完成",
                        f"解压密码：{result['password']}",
                        f"本项用时：{result['seconds']} 秒",
                    ]
                    if result.get("warning"):
                        lines.append("⚠️ 本项存在下载失败图片，压缩包可能不完整")
                else:
                    detail = str(result.get("error") or "未知错误")
                    lines = [
                        f"❌ [{index}/{len(jobs)}] JM{album_id} 任务失败",
                        detail[:300],
                    ]
                if index == len(jobs):
                    lines.extend(
                        [
                            "批量任务已全部处理完毕",
                            f"总用时：{math.ceil(time.monotonic() - batch_started)} 秒",
                        ]
                    )
                try:
                    await self._reply(
                        message,
                        "\n".join(lines),
                        msg_seq=index + 1,
                    )
                except Exception:
                    logger.exception(
                        "JM 批量任务结果通知失败: album_id=%s",
                        album_id,
                    )
                    if result.get("ok"):
                        # 不把一次性解压密码写入持久日志；通知失败时应重新执行任务。
                        logger.error(
                            "JM 批量任务通知失败，解压密码未记录: album_id=%s",
                            album_id,
                        )
        except Exception:
            logger.exception("JM 批量任务运行失败")
            await self._reply(
                message,
                "JM 批量任务异常停止，请查看服务日志",
                msg_seq=2,
            )
        finally:
            _jm_job_lock.release()

    async def _start_jm_job(
        self,
        message: GroupMessage,
        album_id: str,
        requester_id: str,
    ) -> None:
        if not JM_ENABLED:
            await self._reply(message, "JM 下载功能尚未启用")
            return
        if JM_ALLOWED_USERS and requester_id not in JM_ALLOWED_USERS:
            await self._reply(message, "你没有使用 JM 下载功能的权限")
            return
        if not _jm_job_lock.acquire(blocking=False):
            await self._reply(message, "已有一个 JM 下载任务正在运行，请稍后再试")
            return

        try:
            page_count: int | None = None
            try:
                page_count = await asyncio.to_thread(_inspect_jm_album, album_id)
                estimated_seconds = _estimate_jm_seconds(page_count)
                estimate_text = (
                    f"\n页数：{page_count} 页"
                    f"\n预计约 {estimated_seconds} 秒"
                )
            except Exception as exc:
                logger.warning("JM 页数查询失败: album_id=%s error=%s", album_id, exc)
                estimate_text = "\n预计时间：暂时无法计算"
            await self._reply(
                message,
                f"已开始下载 JM{album_id}"
                f"{estimate_text}"
                "\n当前一次只处理一个任务",
                msg_seq=1,
            )
            task = asyncio.create_task(
                self._run_jm_job(message, album_id, page_count)
            )
            _jm_tasks.add(task)
            task.add_done_callback(_jm_tasks.discard)
        except Exception:
            _jm_job_lock.release()
            raise

    async def _start_jm_batch(
        self,
        message: GroupMessage,
        album_ids: list[str],
        requester_id: str,
    ) -> None:
        if len(album_ids) == 1:
            await self._start_jm_job(message, album_ids[0], requester_id)
            return
        if not JM_ENABLED:
            await self._reply(message, "JM 下载功能尚未启用")
            return
        if JM_ALLOWED_USERS and requester_id not in JM_ALLOWED_USERS:
            await self._reply(message, "你没有使用 JM 下载功能的权限")
            return
        if len(album_ids) > JM_BATCH_MAX_ITEMS:
            await self._reply(
                message,
                f"一次最多提交 {JM_BATCH_MAX_ITEMS} 个 JM ID",
            )
            return
        if not _jm_job_lock.acquire(blocking=False):
            await self._reply(message, "已有一个 JM 下载任务正在运行，请稍后再试")
            return

        try:
            async def inspect(album_id: str) -> tuple[str, int | None]:
                for attempt in range(1, 3):
                    try:
                        page_count = await asyncio.to_thread(
                            _inspect_jm_album,
                            album_id,
                        )
                        return album_id, page_count
                    except Exception as exc:
                        logger.warning(
                            "JM 页数查询失败: album_id=%s attempt=%s error=%s",
                            album_id,
                            attempt,
                            exc,
                        )
                        if attempt == 1:
                            await asyncio.sleep(1)
                return album_id, None

            # JM 元数据端点对并发请求不稳定；按输入顺序逐个查询更可靠。
            jobs: list[tuple[str, int | None]] = []
            for album_id in album_ids:
                jobs.append(await inspect(album_id))
            estimates = [
                _estimate_jm_seconds(page_count)
                for _, page_count in jobs
                if page_count
            ]
            lines = [
                f"已开始 JM 批量任务，共 {len(jobs)} 个",
                "将按以下顺序逐个下载并上传：",
            ]
            for index, (album_id, page_count) in enumerate(jobs, start=1):
                if page_count:
                    estimate = _estimate_jm_seconds(page_count)
                    detail = f"{page_count} 页，约 {estimate} 秒"
                else:
                    detail = "页数和时间暂时无法计算"
                lines.append(f"{index}. JM{album_id}（{detail}）")
            if len(estimates) == len(jobs):
                lines.append(f"预计总用时：约 {sum(estimates)} 秒")
            elif estimates:
                lines.append(f"已知任务预计至少需要：约 {sum(estimates)} 秒")
            await self._reply(message, "\n".join(lines), msg_seq=1)

            task = asyncio.create_task(self._run_jm_batch(message, jobs))
            _jm_tasks.add(task)
            task.add_done_callback(_jm_tasks.discard)
        except Exception:
            _jm_job_lock.release()
            raise

    async def on_group_at_message_create(self, message: GroupMessage):
        group_openid = str(message.group_openid or "").strip()
        message_id = str(message.id or "").strip()
        command = str(message.content or "").strip()
        author = getattr(message, "author", None)
        requester_id = str(
            getattr(author, "member_openid", "")
            or getattr(author, "id", "")
            or "anonymous"
        ).strip()
        requester_name = str(getattr(author, "username", "") or "").strip()

        logger.info(
            "收到群 @ 消息: group_openid=%s command=%r",
            group_openid,
            command,
        )

        if ALLOWED_GROUPS and group_openid not in ALLOWED_GROUPS:
            logger.warning("忽略未授权群的命令: %s", group_openid)
            return
        if not command or _is_duplicate(message_id):
            return

        jm_album_ids = _parse_jm_album_ids(command)
        if jm_album_ids:
            await self._start_jm_batch(
                message,
                jm_album_ids,
                requester_id,
            )
            return
        if command.lower() == "jm":
            await self._reply(
                message,
                "用法：@机器人 JM 作品ID [作品ID ...]"
                f"\n一次最多 {JM_BATCH_MAX_ITEMS} 个，例如：JM 111111 222222 333333",
            )
            return

        if command in {"文件测试", "测试文件"}:
            if not FILE_TEST_URL:
                await self._reply(
                    message,
                    "尚未配置 QQBOT_FILE_TEST_URL",
                )
                return
            try:
                media = await message._api.post_group_file(
                    group_openid=group_openid,
                    file_type=4,
                    url=FILE_TEST_URL,
                    srv_send_msg=False,
                )
                await message._api.post_group_message(
                    group_openid=group_openid,
                    msg_type=7,
                    msg_id=message.id,
                    msg_seq=1,
                    media=media,
                )
                logger.info("QQ 群普通文件测试发送成功: %s", FILE_TEST_URL)
            except Exception as exc:
                logger.exception("QQ 群普通文件测试失败")
                await self._reply(
                    message,
                    f"普通文件发送失败：{exc}",
                )
            return

        try:
            result = await asyncio.to_thread(
                _forward_command,
                command,
                requester_id,
                requester_name,
                group_openid,
            )
        except requests.RequestException as exc:
            logger.error("连接 Oopzbot 桥接接口失败: %s", exc)
            result = {"ok": False, "message": "Oopzbot 当前不可用，请稍后再试"}
        except Exception:
            logger.exception("处理 QQ 群命令失败")
            result = {"ok": False, "message": "处理命令时发生错误"}

        try:
            await self._reply_result(message, result, requester_id)
        except Exception:
            logger.exception("回复 QQ 群消息失败")


def main() -> None:
    missing = [
        name
        for name, value in (
            ("QQBOT_APP_ID", APP_ID),
            ("QQBOT_APP_SECRET", APP_SECRET),
            ("QQBOT_BRIDGE_TOKEN", BRIDGE_TOKEN),
        )
        if not value
    ]
    if missing:
        raise SystemExit("缺少环境变量: " + ", ".join(missing))

    if JM_ENABLED:
        if not Path(JM_UPLOADER).is_file():
            raise SystemExit(f"JM QQ 上传器不存在: {JM_UPLOADER}")
        if not Path(JM_NODE).is_file() and shutil.which(JM_NODE) is None:
            raise SystemExit(f"Node.js 不存在: {JM_NODE}")
        sdk_package = (
            Path(JM_UPLOADER).parent
            / "node_modules"
            / "@tencent-connect"
            / "qqbot-nodejs"
            / "package.json"
        )
        if not sdk_package.is_file():
            raise SystemExit(
                "JM QQ 上传器依赖尚未安装，请在 tools/qqbot-uploader 执行 npm ci"
            )
        JM_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        _cleanup_stale_jm_jobs()

    intents = botpy.Intents(public_messages=True)
    client = OopzQQClient(intents=intents)
    client.run(appid=APP_ID, secret=APP_SECRET)


if __name__ == "__main__":
    main()
