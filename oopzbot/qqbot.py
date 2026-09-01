"""QQ 官方群机器人客户端：把群 @ 消息转发给本机 Oopzbot。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import secrets
import time
from threading import Lock

import botpy
import requests
from botpy.message import GroupMessage

from .jm.contracts import JMJob
from .jm.queue import RedisJMQueue
from .jm.service import JMTaskCoordinator
from .metrics import CommandTiming, utc_now
from .observability import command_context, ensure_command_id
from .operations import operations
from .qq.formatter import plain_text as _format_qq_plain_text
from .qq.reply_policy import ReplyErrorKind, ReplyPolicy, classify_reply_error

logger = logging.getLogger("QQBotService")

APP_ID = (os.getenv("QQBOT_APP_ID") or "").strip()
APP_SECRET = (os.getenv("QQBOT_APP_SECRET") or "").strip()
BRIDGE_TOKEN = (os.getenv("QQBOT_BRIDGE_TOKEN") or "").strip()
_bridge_host = os.getenv("OOPZBOT_BRIDGE_HOST", "127.0.0.1")
if _bridge_host in {"0.0.0.0", "::"}:
    _bridge_host = "127.0.0.1"
BRIDGE_URL = (
    os.getenv("QQBOT_BRIDGE_URL")
    or f"http://{_bridge_host}:{os.getenv('OOPZBOT_BRIDGE_PORT', '18080')}"
    "/internal/qqbot/command"
).strip()
FILE_TEST_URL = (os.getenv("QQBOT_FILE_TEST_URL") or "").strip()
JM_ENABLED = (os.getenv("QQBOT_JM_ENABLED") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
JM_MAX_BYTES = int(os.getenv("QQBOT_JM_MAX_BYTES") or str(80 * 1024 * 1024))
JM_TIMEOUT_SECONDS = int(os.getenv("QQBOT_JM_TIMEOUT_SECONDS") or "1200")
JM_UPLOAD_TIMEOUT_SECONDS = int(
    os.getenv("QQBOT_JM_UPLOAD_TIMEOUT_SECONDS") or "900"
)
JM_BATCH_MAX_ITEMS = max(
    1,
    int(os.getenv("QQBOT_JM_BATCH_MAX_ITEMS") or "3"),
)
JM_ALLOWED_USERS = {
    item.strip()
    for item in (os.getenv("QQBOT_JM_ALLOWED_USER_OPENIDS") or "").split(",")
    if item.strip()
}
COMMAND_DEFER_SECONDS = max(
    0.1,
    float(os.getenv("QQBOT_COMMAND_DEFER_SECONDS") or "2.5"),
)
ALLOWED_GROUPS = {
    item.strip()
    for item in (os.getenv("QQBOT_ALLOWED_GROUP_OPENIDS") or "").split(",")
    if item.strip()
}

_seen_messages: dict[str, float] = {}
_seen_lock = Lock()
_jm_coordinator = JMTaskCoordinator()
_jm_job_lock = _jm_coordinator.lock
_jm_tasks = _jm_coordinator.tasks
_command_tasks: set[asyncio.Task] = set()
_msg_seq_lock = Lock()
_msg_seq = secrets.randbelow(65535)


def _next_msg_seq() -> int:
    """Return a process-wide unique sequence for every group message."""
    global _msg_seq
    with _msg_seq_lock:
        _msg_seq = (_msg_seq % 65535) + 1
        return _msg_seq


def _qq_plain_text(content: str) -> str:
    """Remove OOPZ-only inline attachment markers from QQ text replies."""
    return _format_qq_plain_text(content)


def _passive_reply_unavailable(error: Exception) -> bool:
    return classify_reply_error(error) is ReplyErrorKind.PASSIVE_UNAVAILABLE


def _group_message_was_deduplicated(error: Exception) -> bool:
    return classify_reply_error(error) is ReplyErrorKind.DEDUPLICATED


def _group_message_request_timed_out(error: Exception) -> bool:
    return classify_reply_error(error) is ReplyErrorKind.TIMED_OUT


def _parse_jm_album_ids(command: str) -> list[str]:
    match = re.fullmatch(
        r"(?:jm|JM|jm下载|JM下载)\s*(\d{1,12}(?:\s+\d{1,12})*)",
        str(command or "").strip(),
    )
    if not match:
        return []

    # 同一条消息中的重复 ID 只处理一次，并保留用户输入顺序。
    return list(dict.fromkeys(re.findall(r"\d{1,12}", match.group(1))))


def _safe_update_jm_job(job_id: str, **changes) -> None:
    try:
        operations.update_jm_job(job_id, **changes)
    except Exception:
        logger.exception("记录 JM 诊断状态时发生错误: job_id=%s", job_id)


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
    command_id: str | None = None,
) -> dict:
    correlation_id = ensure_command_id(command_id)
    response = requests.post(
        BRIDGE_URL,
        json={
            "command": command,
            "requester_id": requester_id,
            "requester_name": requester_name,
            "group_openid": group_openid,
            "command_id": correlation_id,
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


_reply_policy = ReplyPolicy(_next_msg_seq, logger=logger)


class OopzQQClient(botpy.Client):
    async def on_ready(self):
        logger.info("QQ 机器人已上线: %s", self.robot.name)
        operations.set_component("qq_bot", "ok", "QQ 网关已连接")
        operations.record_event("gateway", "QQ 机器人已上线", source="QQ Bot")
        if not ALLOWED_GROUPS:
            logger.warning(
                "尚未设置 QQBOT_ALLOWED_GROUP_OPENIDS；当前会接受机器人所在群的命令"
            )

    async def _post_group_message(
        self,
        message: GroupMessage,
        payload: dict,
    ) -> None:
        await _reply_policy.send(
            message._api.post_group_message,
            group_openid=message.group_openid,
            payload=payload,
        )

    @staticmethod
    def _reply_identity(
        message: GroupMessage,
        msg_seq: int = 1,
        *,
        proactive: bool = False,
    ) -> dict:
        del msg_seq
        return _reply_policy.identity(message.id, proactive=proactive)

    async def _reply(
        self,
        message: GroupMessage,
        content: str,
        msg_seq: int = 1,
        *,
        proactive: bool = False,
    ) -> None:
        plain_content = _qq_plain_text(content) or "命令已处理"
        payload = {
            "msg_type": 0,
            **self._reply_identity(message, msg_seq, proactive=proactive),
            "content": plain_content[:1000],
        }
        await self._post_group_message(message, payload)
        logger.info(
            "QQ 群回复已发送 group_openid=%s proactive=%s",
            message.group_openid,
            proactive,
        )

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
    def _queue_markdown(result: dict) -> str:
        current = result.get("current") or {}
        items = result.get("queue_items") or []
        total = int(result.get("queue_length") or 0)
        lines = ["# Oopz 播放队列", ""]
        notice = str(result.get("notice") or "").strip()
        if notice:
            lines.extend([f"> {notice}", ""])
        if current:
            lines.append(
                f"正在播放：**{current.get('name', '未知歌曲')}** — "
                f"{current.get('artists', '未知歌手')}"
            )
        else:
            lines.append("正在播放：无")
        lines.extend(["", f"待播队列：{total} 首"])
        for item in items:
            lines.append(
                f"{item.get('index', 0)}. **{item.get('name', '未知歌曲')}** — "
                f"{item.get('artists', '未知歌手')}"
            )
        if total > len(items):
            lines.append(f"……另有 {total - len(items)} 首")
        if items:
            lines.extend(["", "> 点击下方按钮删除对应待播歌曲"])
        return "\n".join(lines)

    def _queue_keyboard(self, result: dict, requester_id: str) -> dict:
        items = [
            {
                "value": str(item.get("index") or ""),
                "label": f"删 {item.get('index', '')}. {item.get('name', '未知歌曲')}",
                "command": f"删除 {item.get('index', '')}",
            }
            for item in (result.get("queue_items") or [])
        ]
        return self._command_keyboard(
            items,
            requester_id,
            "删除",
            buttons_per_row=2,
        )

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
        *,
        proactive: bool = False,
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
            await self._post_group_message(
                message,
                {
                    "msg_type": 2,
                    **self._reply_identity(message, proactive=proactive),
                    "markdown": {"content": "\n".join(lines)},
                    "keyboard": self._command_keyboard(
                        items,
                        requester_id,
                        "榜单",
                    ),
                },
            )
        except Exception as exc:
            logger.warning("QQ 排行榜菜单发送失败，退回纯文本: %s", exc)
            await self._reply(
                message,
                str(result.get("message") or "排行榜不可用"),
                proactive=proactive,
            )

    async def _reply_rank_results(
        self,
        message: GroupMessage,
        result: dict,
        requester_id: str,
        *,
        proactive: bool = False,
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
            await self._post_group_message(
                message,
                {
                    "msg_type": 2,
                    **self._reply_identity(message, proactive=proactive),
                    "markdown": {"content": "\n".join(lines)},
                    "keyboard": self._command_keyboard(
                        items,
                        requester_id,
                        "榜单点歌",
                        buttons_per_row=3,
                    ),
                },
            )
        except Exception as exc:
            logger.warning("QQ 排行榜详情发送失败，退回纯文本: %s", exc)
            await self._reply(
                message,
                str(result.get("message") or "榜单不可用"),
                proactive=proactive,
            )

    async def _reply_search_results(
        self,
        message: GroupMessage,
        result: dict,
        requester_id: str,
        *,
        proactive: bool = False,
    ) -> None:
        try:
            await self._post_group_message(
                message,
                {
                    "msg_type": 2,
                    **self._reply_identity(message, proactive=proactive),
                    "markdown": {"content": self._search_markdown(result)},
                    "keyboard": self._search_keyboard(result, requester_id),
                },
            )
        except Exception as exc:
            logger.warning("QQ Markdown/选歌按钮发送失败，退回纯文本: %s", exc)
            await self._reply(
                message,
                str(result.get("message") or "未找到歌曲"),
                proactive=proactive,
            )

    async def _reply_song_selected(
        self,
        message: GroupMessage,
        result: dict,
        *,
        proactive: bool = False,
    ) -> None:
        song = result.get("song") or {}
        name = str(song.get("name") or "未知歌曲")
        artists = str(song.get("artists") or "未知歌手")
        album = str(song.get("album") or "未知专辑")
        duration = str(song.get("duration") or "未知")
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

        try:
            await self._post_group_message(
                message,
                {
                    "msg_type": 2,
                    **self._reply_identity(message, proactive=proactive),
                    "markdown": {"content": markdown},
                },
            )
        except Exception as exc:
            logger.warning("QQ Markdown 歌曲信息发送失败，退回纯文本: %s", exc)
            await self._reply(
                message,
                fallback,
                msg_seq=1,
                proactive=proactive,
            )

    async def _reply_queue_panel(
        self,
        message: GroupMessage,
        result: dict,
        requester_id: str,
        *,
        proactive: bool = False,
    ) -> None:
        items = result.get("queue_items") or []
        try:
            payload = {
                "msg_type": 2,
                **self._reply_identity(message, proactive=proactive),
                "markdown": {"content": self._queue_markdown(result)},
            }
            if items:
                payload["keyboard"] = self._queue_keyboard(result, requester_id)
            await self._post_group_message(message, payload)
        except Exception as exc:
            logger.warning("QQ 队列面板发送失败，退回纯文本: %s", exc)
            await self._reply(
                message,
                str(result.get("message") or "队列为空"),
                proactive=proactive,
            )

    async def _reply_result(
        self,
        message: GroupMessage,
        result: dict,
        requester_id: str,
        *,
        proactive: bool = False,
    ) -> None:
        reply_type = str(result.get("reply_type") or "")
        if reply_type == "search_results" and result.get("songs"):
            await self._reply_search_results(
                message, result, requester_id, proactive=proactive
            )
            return
        if reply_type == "rank_catalog" and result.get("ranks"):
            await self._reply_rank_catalog(
                message, result, requester_id, proactive=proactive
            )
            return
        if reply_type == "rank_results" and result.get("songs"):
            await self._reply_rank_results(
                message, result, requester_id, proactive=proactive
            )
            return
        if reply_type == "song_selected" and result.get("song"):
            await self._reply_song_selected(message, result, proactive=proactive)
            return
        if reply_type == "queue_panel":
            await self._reply_queue_panel(
                message, result, requester_id, proactive=proactive
            )
            return
        await self._reply(
            message,
            str(result.get("message") or "命令已处理"),
            proactive=proactive,
        )


    async def _start_jm_job(
        self,
        message: GroupMessage,
        album_id: str,
        requester_id: str,
    ) -> None:
        await self._start_jm_batch(message, [album_id], requester_id)

    async def _start_jm_batch(
        self,
        message: GroupMessage,
        album_ids: list[str],
        requester_id: str,
    ) -> None:
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
        if not _jm_coordinator.acquire():
            await self._reply(message, "已有一个 JM 下载任务正在运行，请稍后再试")
            return

        await self._start_external_jm_jobs(message, album_ids, requester_id)

    async def _start_external_jm_jobs(
        self,
        message: GroupMessage,
        album_ids: list[str],
        requester_id: str,
    ) -> None:
        """Submit JM work without importing or running JM packages in the bot."""

        queue = RedisJMQueue()
        if not await asyncio.to_thread(queue.available):
            _jm_coordinator.release()
            await self._reply(message, "JM 服务未启用或当前不可用")
            return

        jobs: list[tuple[JMJob, str]] = []
        requester_ref = hashlib.sha256(requester_id.encode("utf-8")).hexdigest()[:16]
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        try:
            for index, album_id in enumerate(album_ids, 1):
                try:
                    job_id = operations.begin_jm_job(
                        album_id,
                        requester="QQ 群用户",
                        batch_index=index,
                        batch_total=len(album_ids),
                    )
                except Exception:
                    job_id = secrets.token_hex(16)
                    logger.exception(
                        "创建 JM 诊断记录失败，继续提交 worker: job_id=%s",
                        job_id,
                    )
                password = "".join(secrets.choice(alphabet) for _ in range(14))
                job = JMJob(
                    job_id=job_id,
                    album_id=album_id,
                    requester_ref=requester_ref,
                    group_openid=str(message.group_openid),
                    message_id=str(message.id),
                    password=password,
                    max_archive_bytes=JM_MAX_BYTES,
                    timeout_seconds=JM_TIMEOUT_SECONDS,
                    lease_seconds=JM_TIMEOUT_SECONDS + JM_UPLOAD_TIMEOUT_SECONDS + 60,
                )
                jobs.append((job, job_id))
            await asyncio.to_thread(queue.submit_many, [job for job, _ in jobs])
        except Exception as exc:
            for _, job_id in jobs:
                _safe_update_jm_job(
                    job_id,
                    status="failed",
                    phase="failed",
                    error=f"提交 JM worker 失败：{type(exc).__name__}",
                )
            _jm_coordinator.release()
            raise

        for _, job_id in jobs:
            _safe_update_jm_job(job_id, phase="queued")
        task = asyncio.create_task(self._watch_external_jm_jobs(message, queue, jobs))
        _jm_coordinator.track(task)
        try:
            await self._reply(
                message,
                f"已提交 JM 任务，共 {len(jobs)} 个；由独立 worker 顺序处理",
                msg_seq=1,
            )
        except Exception:
            logger.exception("JM 已提交但确认消息发送失败")

    async def _watch_external_jm_jobs(
        self,
        message: GroupMessage,
        queue: RedisJMQueue,
        jobs: list[tuple[JMJob, str]],
    ) -> None:
        try:
            for index, (job, job_id) in enumerate(jobs, 1):
                deadline = time.monotonic() + JM_TIMEOUT_SECONDS + JM_UPLOAD_TIMEOUT_SECONDS
                result = None
                while time.monotonic() < deadline:
                    result = await asyncio.to_thread(queue.result, job_id)
                    if result is not None:
                        break
                    await asyncio.sleep(2)
                if result is None:
                    result = {"ok": False, "error": "JM worker 结果等待超时"}
                if result.get("ok"):
                    _safe_update_jm_job(
                        job_id,
                        status="completed",
                        phase="completed",
                        page_count=int(result.get("page_count") or 0),
                        archive_bytes=int(result.get("archive_bytes") or 0),
                    )
                    text = (
                        f"✅ [{index}/{len(jobs)}] JM{job.album_id}.zip 上传完成"
                        f"\n解压密码：{job.password}"
                        f"\n用时：{int(result.get('seconds') or 0)} 秒"
                    )
                else:
                    error = str(result.get("error") or "JM worker 处理失败")[-500:]
                    _safe_update_jm_job(
                        job_id, status="failed", phase="failed", error=error
                    )
                    text = f"❌ [{index}/{len(jobs)}] JM{job.album_id} 任务失败\n{error}"
                await self._reply(message, text, msg_seq=index + 1)
        finally:
            _jm_coordinator.release()

    async def _deliver_deferred_command(
        self,
        message: GroupMessage,
        bridge_task: asyncio.Task,
        requester_id: str,
        command_id: str | None = None,
    ) -> None:
        with command_context(command_id):
            try:
                result = await bridge_task
            except requests.RequestException as exc:
                logger.error("连接 Oopzbot 桥接接口失败: %s", exc)
                result = {"ok": False, "message": "Oopzbot 当前不可用，请稍后再试"}
            except Exception:
                logger.exception("处理 QQ 群命令失败")
                result = {"ok": False, "message": "处理命令时发生错误"}

            try:
                await self._reply_result(
                    message,
                    result,
                    requester_id,
                    proactive=False,
                )
            except Exception:
                logger.exception("发送 QQ 群命令结果失败")

    async def _handle_group_at_message_create(
        self,
        message: GroupMessage,
        command_id: str,
    ):
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
                await self._post_group_message(
                    message,
                    {
                        "msg_type": 7,
                        "msg_id": message.id,
                        "msg_seq": _next_msg_seq(),
                        "media": media,
                    },
                )
                logger.info("QQ 群普通文件测试发送成功: %s", FILE_TEST_URL)
            except Exception as exc:
                logger.exception("QQ 群普通文件测试失败")
                await self._reply(
                    message,
                    f"普通文件发送失败：{exc}",
                )
            return

        bridge_task = asyncio.create_task(
            asyncio.to_thread(
                _forward_command,
                command,
                requester_id,
                requester_name,
                group_openid,
                command_id,
            )
        )
        try:
            result = await asyncio.wait_for(
                asyncio.shield(bridge_task),
                timeout=COMMAND_DEFER_SECONDS,
            )
        except TimeoutError:
            try:
                operations.record_command_timing(
                    CommandTiming(
                        command_id=command_id,
                        source="qq",
                        kind=f"{command.split(' ', 1)[0] or 'unknown'}:accepted",
                        ok=True,
                        duration_ms=COMMAND_DEFER_SECONDS * 1000,
                        created_at=utc_now(),
                    )
                )
            except Exception as exc:
                logger.warning("记录延迟命令接受耗时失败: %s", type(exc).__name__)
            delivery_task = asyncio.create_task(
                self._deliver_deferred_command(
                    message,
                    bridge_task,
                    requester_id,
                    command_id,
                )
            )
            _command_tasks.add(delivery_task)
            delivery_task.add_done_callback(_command_tasks.discard)
            return
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

    async def on_group_at_message_create(self, message: GroupMessage):
        command_id = ensure_command_id(getattr(message, "command_id", None))
        with command_context(command_id):
            logger.info("QQ 命令入口 command_id=%s", command_id)
            return await self._handle_group_at_message_create(message, command_id)


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

    intents = botpy.Intents(public_messages=True)
    client = OopzQQClient(intents=intents)
    operations.set_component("qq_bot", "starting", "正在连接 QQ 网关")
    try:
        client.run(appid=APP_ID, secret=APP_SECRET)
    except Exception as exc:
        operations.set_component(
            "qq_bot",
            "error",
            f"QQ 网关异常：{type(exc).__name__}",
        )
        raise
    finally:
        current = operations.snapshot().get("components", {}).get("qq_bot", {})
        if current.get("status") != "error":
            operations.set_component("qq_bot", "offline", "QQ 网关已停止")


if __name__ == "__main__":
    from .logging_config import configure_logging

    configure_logging(os.getenv("LOG_LEVEL") or "INFO")
    main()
