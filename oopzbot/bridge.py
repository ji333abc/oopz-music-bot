"""QQ 官方机器人到 Oopzbot 的本机命令桥接接口。"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .config import get_settings
from .operations import operations

logger = logging.getLogger("QQBotBridge")
router = APIRouter()
_music_dependency = None

_command_lock = Lock()
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_search_sessions: dict[str, dict] = {}
_SEARCH_SESSION_TTL_SECONDS = 300
_rank_sessions: dict[str, dict] = {}
_RANK_SESSION_TTL_SECONDS = 300

_QQ_RANKS = (
    (26, "巅峰榜·热歌"),
    (27, "巅峰榜·新歌"),
    (62, "飙升榜"),
    (4, "巅峰榜·流行指数"),
    (60, "抖音热歌榜"),
    (28, "巅峰榜·网络歌曲"),
    (78, "国乐榜"),
    (58, "说唱榜"),
    (57, "电音榜"),
    (5, "巅峰榜·内地"),
    (3, "巅峰榜·欧美"),
    (59, "香港地区榜"),
    (16, "巅峰榜·韩国"),
    (29, "巅峰榜·影视金曲"),
    (17, "巅峰榜·日本"),
    (201, "巅峰榜·MV"),
    (36, "巅峰榜·K歌金曲"),
    (61, "台湾地区榜"),
    (63, "DJ舞曲榜"),
    (64, "综艺新歌榜"),
    (65, "国风热歌榜"),
    (67, "听歌识曲榜"),
    (72, "动漫音乐榜"),
    (73, "游戏音乐榜"),
    (75, "有声榜"),
)
_QQ_RANK_BY_ID = dict(_QQ_RANKS)
_QQ_COMMON_RANK_IDS = (26, 27, 62, 4, 60, 28)

_HELP_MESSAGE = """Music-bot 使用帮助
所有命令都需要先 @机器人

🎵 音乐播放
├─ 点歌 <歌名> / 播放 <歌名>
├─ 搜歌 <关键词>
│  └─ 选歌 <编号>（结果保留5分钟）
├─ 状态 —— 当前歌曲、进度和在线人数
├─ 面板 / 队列 —— 查看当前队列和删除按钮
├─ 删除 <编号...> —— 删除指定待播歌曲
├─ 暂停 / 继续
├─ 切歌
└─ 停止

📊 QQ音乐排行榜
├─ 排行榜 —— 查看可用榜单
├─ 榜单 <ID或名称>
├─ 榜单点歌 <编号>
└─ 榜单批量 10 —— 前10首加入队列

👥 OOPZ频道
├─ 在线 —— 显示所有语音频道及成员
└─ 有谁 —— 查看 Music 频道成员

📦 JM下载
├─ JM <作品ID>
└─ JM <ID1> <ID2> <ID3>（最多3个，依次处理）"""


def _qq_song_payload(song: dict, index: int | None = None) -> dict:
    """Return only the song metadata needed by the QQ reply renderer."""
    payload = {
        "id": str(song.get("id") or song.get("mid") or song.get("song_id") or ""),
        "name": str(song.get("name") or "未知歌曲"),
        "artists": str(song.get("artists") or "未知歌手"),
        "album": str(song.get("album") or ""),
        "duration": str(song.get("durationText") or song.get("duration") or ""),
        "durationText": str(song.get("durationText") or song.get("duration") or ""),
        "cover": str(song.get("cover") or ""),
        "platform": str(song.get("platform") or "qq"),
    }
    if index is not None:
        payload["index"] = index
    return payload


def _qq_music_api_base_url() -> str:
    return get_settings().qq_music_base_url.rstrip("/")


def _qq_music_api_get(path: str, params: dict | None = None) -> dict | None:
    try:
        response = requests.get(
            f"{_qq_music_api_base_url()}{path}",
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.warning("QQ音乐排行榜接口请求失败: %s", exc)
        return None


def _notify_music(music, *, text: str, channel: str, area: str, **kwargs) -> bool:
    """Send an OOPZ notification without failing the already-started action."""
    notifier = getattr(music, "notify_message", None)
    if callable(notifier):
        return bool(notifier(text=text, channel=channel, area=area, **kwargs))
    try:
        music.sender.send_message(text=text, channel=channel, area=area, **kwargs)
    except Exception as exc:
        logger.warning("OOPZ 文字消息发送失败，继续执行: %s", exc)
        return False
    return True


def _rank_catalog() -> dict:
    lines = ["QQ音乐排行榜"]
    for rank_id, title in _QQ_RANKS:
        lines.append(f"{rank_id}. {title}")
    lines.append("发送：榜单 <ID或名称>")
    return {
        "ok": True,
        "reply_type": "rank_catalog",
        "message": "\n".join(lines),
        "ranks": [
            {"id": rank_id, "title": title}
            for rank_id, title in _QQ_RANKS
        ],
        "button_ranks": [
            {
                "id": rank_id,
                "title": _QQ_RANK_BY_ID[rank_id],
            }
            for rank_id in _QQ_COMMON_RANK_IDS
        ],
    }


def _normalize_rank_name(value: str) -> str:
    normalized = re.sub(r"[\s·・]", "", value).lower()
    return (
        normalized.replace("qq音乐", "")
        .replace("排行榜", "")
        .replace("巅峰榜", "")
        .replace("榜", "")
    )


def _resolve_rank(value: str) -> tuple[int, str] | None:
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        rank_id = int(value)
        title = _QQ_RANK_BY_ID.get(rank_id)
        return (rank_id, title) if title else None

    needle = _normalize_rank_name(value)
    exact = [
        item
        for item in _QQ_RANKS
        if _normalize_rank_name(item[1]) == needle
    ]
    if exact:
        return exact[0]

    partial = [
        item
        for item in _QQ_RANKS
        if needle and needle in _normalize_rank_name(item[1])
    ]
    return partial[0] if len(partial) == 1 else None


def _collect_rank_songs(payload: dict, limit: int = 10) -> list[dict]:
    songs: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def walk(value) -> None:
        if len(songs) >= limit:
            return
        if isinstance(value, dict):
            title = str(value.get("title") or "").strip()
            artists = str(value.get("singerName") or "").strip()
            rank = value.get("rank")
            if title and artists and rank is not None:
                key = (title, artists)
                if key not in seen:
                    seen.add(key)
                    album_mid = str(value.get("albumMid") or "").strip()
                    cover = str(value.get("cover") or "").strip()
                    if not cover and album_mid:
                        cover = (
                            "https://y.gtimg.cn/music/photo_new/"
                            f"T002R300x300M000{album_mid}.jpg"
                        )
                    songs.append(
                        {
                            "rank": int(rank),
                            "title": title,
                            "artists": artists,
                            "album_mid": album_mid,
                            "cover": cover.replace("http://", "https://", 1),
                        }
                    )
                return
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    songs.sort(key=lambda song: song["rank"])
    return songs[:limit]


def _rank_detail(rank_id: int, title: str, requester_key: str) -> dict:
    payload = _qq_music_api_get(
        "/getRanks",
        params={"topId": rank_id, "limit": 10, "page": 1},
    )
    songs = _collect_rank_songs(payload or {}, limit=10)
    if not songs:
        return {"ok": False, "message": f"暂时无法获取QQ音乐{title}"}

    _rank_sessions[requester_key] = {
        "expires_at": time.monotonic() + _RANK_SESSION_TTL_SECONDS,
        "rank_id": rank_id,
        "title": title,
        "songs": songs,
    }
    lines = [title]
    lines.extend(
        f"{song['rank']}. {song['title']} - {song['artists']}"
        for song in songs
    )
    lines.append("5分钟内发送：榜单点歌 <编号>")
    return {
        "ok": True,
        "reply_type": "rank_results",
        "message": "\n".join(lines),
        "rank_id": rank_id,
        "title": title,
        "songs": songs,
    }


def _select_rank_song(
    music,
    index: int,
    requester_key: str,
    area: str,
    text_channel: str,
    voice_channel: str,
    bot_user: str,
) -> dict:
    session = _rank_sessions.get(requester_key)
    if not session or time.monotonic() > float(session.get("expires_at") or 0):
        _rank_sessions.pop(requester_key, None)
        return {"ok": False, "message": "排行榜结果已失效，请重新打开榜单"}

    rank_songs = session.get("songs") or []
    selected = next(
        (song for song in rank_songs if int(song.get("rank") or 0) == index),
        None,
    )
    if selected is None and 1 <= index <= len(rank_songs):
        selected = rank_songs[index - 1]
    if selected is None:
        return {"ok": False, "message": f"编号无效，请输入 1-{len(rank_songs)}"}

    song = _resolve_rank_song_candidate(music, selected)
    if song is None:
        return {"ok": False, "message": f"无法解析榜单歌曲：{selected['title']}"}

    current_channel = getattr(music, "_voice_channel_id", None)
    current_area = getattr(music, "_voice_channel_area", None)
    if current_channel != voice_channel or current_area != area:
        result = music.enter_voice_channel(voice_channel, area)
        if not isinstance(result, dict) or result.get("error"):
            detail = result.get("error") if isinstance(result, dict) else "unknown"
            return {"ok": False, "message": f"进入 Oopz 语音频道失败: {detail}"}

    _rank_sessions.pop(requester_key, None)
    music.play_song_choice(song, text_channel, area, bot_user)
    return {
        "ok": True,
        "reply_type": "song_selected",
        "message": f"已选择：{song.get('name', '未知歌曲')} - {song.get('artists', '未知歌手')}",
        "song": _qq_song_payload(song),
    }


def _resolve_rank_song_candidate(music, selected: dict) -> dict | None:
    """把排行榜条目解析成可播放的 QQ 音乐搜索结果。"""
    keyword = f"{selected['title']} {selected['artists']}"
    candidates = music.search_candidates(keyword, "qq", limit=5)
    if not candidates:
        candidates = music.search_candidates(selected["title"], "qq", limit=5)
    if not candidates:
        return None

    normalized_title = re.sub(r"\s+", "", selected["title"]).lower()
    song = next(
        (
            candidate
            for candidate in candidates
            if re.sub(r"\s+", "", str(candidate.get("name") or "")).lower()
            == normalized_title
        ),
        candidates[0],
    )
    return dict(song, platform="qq")


def _prepare_rank_song_data(
    music,
    selected: dict,
    text_channel: str,
    area: str,
    bot_user: str,
) -> tuple[dict, dict] | None:
    """解析排行榜歌曲并构造 Oopz 队列使用的统一数据。"""
    song = _resolve_rank_song_candidate(music, selected)
    if song is None:
        return None

    platform = music.platforms.get("qq")
    song_id = song.get("id") or song.get("song_id") or song.get("mid")
    if platform is None or not song_id:
        return None

    try:
        url = platform.get_song_url(
            song_id,
            expected_duration_ms=(
                song.get("duration", 0) or song.get("duration_ms", 0) or 0
            ),
            song_name=song.get("name", ""),
        )
    except TypeError:
        url = platform.get_song_url(song_id)
    if not url:
        return None

    playable = dict(song)
    playable["url"] = url
    song_data = music._build_song_data_from_platform_data(
        playable,
        "qq",
        song_id,
        text_channel,
        area,
        bot_user,
    )
    return song, song_data


def _queue_rank_songs(
    music,
    count: int,
    requester_key: str,
    area: str,
    text_channel: str,
    voice_channel: str,
    bot_user: str,
) -> dict:
    """把当前排行榜前若干首按顺序加入播放队列，最多十首。"""
    session = _rank_sessions.get(requester_key)
    if not session or time.monotonic() > float(session.get("expires_at") or 0):
        _rank_sessions.pop(requester_key, None)
        return {"ok": False, "message": "排行榜结果已失效，请重新打开榜单"}

    count = max(1, min(int(count), 10))
    selected_songs = list(session.get("songs") or [])[:count]
    if not selected_songs:
        return {"ok": False, "message": "当前榜单没有可加入的歌曲"}

    prepared: list[tuple[dict, dict]] = []
    failed: list[str] = []
    for selected in selected_songs:
        try:
            item = _prepare_rank_song_data(
                music,
                selected,
                text_channel,
                area,
                bot_user,
            )
        except Exception as exc:
            logger.warning("解析排行榜歌曲失败 (%s): %s", selected.get("title"), exc)
            item = None
        if item is None:
            failed.append(str(selected.get("title") or "未知歌曲"))
        else:
            prepared.append(item)

    if not prepared:
        return {"ok": False, "message": "榜单歌曲均无法获取播放链接，请稍后重试"}

    current_channel = getattr(music, "_voice_channel_id", None)
    current_area = getattr(music, "_voice_channel_area", None)
    if current_channel != voice_channel or current_area != area:
        result = music.enter_voice_channel(voice_channel, area)
        if not isinstance(result, dict) or result.get("error"):
            detail = result.get("error") if isinstance(result, dict) else "unknown"
            return {"ok": False, "message": f"进入 Oopz 语音频道失败: {detail}"}

    first_song, first_data = prepared[0]
    music._kickoff_cover_prefetch(first_data)
    user_name = music.names.user(bot_user) if bot_user else "未知用户"
    first_result = music._commit_song_request(
        first_data,
        prefix=f"{user_name} 从搜歌结果中选择了",
    )
    _notify_music(
        music,
        text=first_result["message"],
        attachments=first_result.get("attachments", []),
        channel=text_channel,
        area=area,
    )

    queue = music._get_queue(area)
    for _, song_data in prepared[1:]:
        queue.add_to_queue(song_data)
    if len(prepared) > 1:
        music._preload_next_song_if_any()

    _rank_sessions.pop(requester_key, None)
    title = str(session.get("title") or "QQ音乐榜单")
    lines = [
        f"已批量加入：{title}",
        f"├─ 成功：{len(prepared)} 首",
    ]
    if failed:
        lines.append(f"├─ 失败：{len(failed)} 首（{', '.join(failed)}）")
    lines.append("└─ 发送“队列”可查看待播顺序")
    return {
        "ok": True,
        "reply_type": "rank_batch_queued",
        "message": "\n".join(lines),
        "added_count": len(prepared),
        "failed_count": len(failed),
        "first_song": _qq_song_payload(first_song),
    }


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _music_handler():
    return _music_dependency


def set_music_handler(handler) -> None:
    global _music_dependency
    _music_dependency = handler


def _command_config() -> tuple[str, str, str, str]:
    settings = get_settings()
    bot_user = settings.oopz_person_uid
    handler = _music_handler()
    runtime_bot = getattr(getattr(handler, "runtime", None), "bot", None)
    if not bot_user and runtime_bot is not None:
        bot_user = str(getattr(getattr(runtime_bot, "config", None), "person_uid", "") or "")
    return (
        settings.oopz_area_id,
        settings.oopz_text_channel_id,
        settings.oopz_voice_channel_id,
        bot_user,
    )


def _play_keyword(command: str) -> str:
    for prefix in ("播放歌曲", "点播歌曲", "来一首", "放一首", "点歌", "播放", "点播"):
        if command.startswith(prefix):
            return command[len(prefix):].strip()
    return ""


def _voice_member_summary(music, area: str, voice_channel: str, bot_user: str) -> str:
    """返回固定 Oopz 语音频道中的成员摘要。"""
    sender = getattr(music, "sender", None)
    if sender is None or not hasattr(sender, "get_voice_channel_members"):
        return "Oopz 语音成员查询接口不可用"

    channel_members = sender.get_voice_channel_members(area=area)
    if not isinstance(channel_members, dict):
        return "查询 Oopz 语音成员失败"

    members = channel_members.get(voice_channel) or []
    if isinstance(members, dict):
        members = members.get("members") or members.get("list") or []
    if not isinstance(members, list):
        members = []

    names: list[str] = []
    seen_uids: set[str] = set()
    resolver = getattr(music, "names", None)

    for member in members:
        if isinstance(member, dict):
            uid = str(
                member.get("uid")
                or member.get("id")
                or member.get("personUid")
                or member.get("person_uid")
                or ""
            ).strip()
            display_name = str(
                member.get("name")
                or member.get("nickname")
                or member.get("username")
                or ""
            ).strip()
        else:
            uid = str(member or "").strip()
            display_name = ""

        if not uid or uid == bot_user or uid in seen_uids:
            continue
        seen_uids.add(uid)

        if not display_name and resolver is not None:
            try:
                display_name = str(resolver.user(uid) or "").strip()
            except Exception:
                display_name = ""
        names.append(display_name or f"用户 {uid[:8]}")

    if not names:
        return "Oopz Music 频道当前没有其他成员"

    return f"Oopz Music 频道当前有 {len(names)} 人：\n" + "\n".join(
        f"{index}. {name}" for index, name in enumerate(names, 1)
    )


def _all_voice_channels_summary(music, area: str, bot_user: str) -> str:
    """以目录树格式返回域内全部语音频道及当前成员。"""
    sender = getattr(music, "sender", None)
    if sender is None:
        return "Oopz 频道查询接口不可用"

    try:
        groups = sender.get_area_channels(area=area, quiet=True)
        channel_members = sender.get_voice_channel_members(area=area)
    except Exception:
        logger.exception("查询 Oopz 语音频道失败")
        return "查询 Oopz 语音频道失败"

    if not isinstance(groups, list) or not isinstance(channel_members, dict):
        return "查询 Oopz 语音频道失败"

    voice_channels: list[tuple[str, str]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        for channel in group.get("channels") or []:
            if not isinstance(channel, dict):
                continue
            channel_type = str(channel.get("type") or "").upper()
            if channel_type not in {"VOICE", "AUDIO"}:
                continue
            channel_id = str(channel.get("id") or "").strip()
            if not channel_id:
                continue
            channel_name = str(channel.get("name") or "").strip()
            voice_channels.append((channel_id, channel_name or channel_id[:8]))

    if not voice_channels:
        return "当前域没有语音频道"

    resolver = getattr(music, "names", None)
    lines = ["Oopz 语音频道"]

    for channel_index, (channel_id, channel_name) in enumerate(voice_channels):
        raw_members = channel_members.get(channel_id) or []
        if isinstance(raw_members, dict):
            raw_members = raw_members.get("members") or raw_members.get("list") or []
        if not isinstance(raw_members, list):
            raw_members = []

        member_names: list[str] = []
        seen_uids: set[str] = set()
        for member in raw_members:
            if isinstance(member, dict):
                uid = str(
                    member.get("uid")
                    or member.get("id")
                    or member.get("personUid")
                    or member.get("person_uid")
                    or ""
                ).strip()
                display_name = str(
                    member.get("name")
                    or member.get("nickname")
                    or member.get("username")
                    or ""
                ).strip()
            else:
                uid = str(member or "").strip()
                display_name = ""

            if not uid or uid == bot_user or uid in seen_uids:
                continue
            seen_uids.add(uid)

            if not display_name and resolver is not None:
                try:
                    display_name = str(resolver.user(uid) or "").strip()
                except Exception:
                    display_name = ""
            member_names.append(display_name or f"用户 {uid[:8]}")

        channel_is_last = channel_index == len(voice_channels) - 1
        channel_branch = "└─" if channel_is_last else "├─"
        count_text = f"{len(member_names)}人" if member_names else "空"
        lines.append(f"{channel_branch} {channel_name}（{count_text}）")

        child_prefix = "   " if channel_is_last else "│  "
        for member_index, display_name in enumerate(member_names):
            member_branch = "└─" if member_index == len(member_names) - 1 else "├─"
            lines.append(f"{child_prefix}{member_branch} {display_name}")

    return "\n".join(lines)


def _voice_channels_payload(
    music,
    area: str,
    configured_channel: str,
    bot_user: str,
) -> list[dict]:
    """返回面板可直接渲染的语音频道和成员数据。"""
    sender = getattr(music, "sender", None)
    if sender is None:
        raise RuntimeError("Oopz 频道查询接口不可用")
    groups = sender.get_area_channels(area=area, quiet=True)
    channel_members = sender.get_voice_channel_members(area=area)
    if not isinstance(groups, list) or not isinstance(channel_members, dict):
        raise RuntimeError("Oopz 频道接口返回格式无效")

    resolver = getattr(music, "names", None)
    channels: list[dict] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        for channel in group.get("channels") or []:
            if not isinstance(channel, dict):
                continue
            if str(channel.get("type") or "").upper() not in {"VOICE", "AUDIO"}:
                continue
            channel_id = str(channel.get("id") or "").strip()
            if not channel_id:
                continue
            raw_members = channel_members.get(channel_id) or []
            if isinstance(raw_members, dict):
                raw_members = raw_members.get("members") or raw_members.get("list") or []
            if not isinstance(raw_members, list):
                raw_members = []
            members: list[dict] = []
            seen: set[str] = set()
            for member in raw_members:
                if isinstance(member, dict):
                    uid = str(
                        member.get("uid")
                        or member.get("id")
                        or member.get("personUid")
                        or member.get("person_uid")
                        or ""
                    ).strip()
                    display_name = str(
                        member.get("name")
                        or member.get("nickname")
                        or member.get("username")
                        or ""
                    ).strip()
                else:
                    uid = str(member or "").strip()
                    display_name = ""
                if not uid or uid in seen:
                    continue
                seen.add(uid)
                if not display_name and resolver is not None:
                    try:
                        display_name = str(resolver.user(uid) or "").strip()
                    except Exception:
                        display_name = ""
                members.append(
                    {
                        "id": uid,
                        "name": display_name or f"用户 {uid[:8]}",
                        "is_bot": uid == bot_user,
                    }
                )
            channels.append(
                {
                    "id": channel_id,
                    "name": str(channel.get("name") or channel_id[:8]),
                    "configured": channel_id == configured_channel,
                    "members": members,
                    "member_count": sum(not item["is_bot"] for item in members),
                }
            )
    return channels


def _format_seconds(value) -> str:
    try:
        seconds = max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        seconds = 0
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _music_channel_member_count(music, area: str, voice_channel: str, bot_user: str) -> int:
    sender = getattr(music, "sender", None)
    if sender is None:
        return 0
    try:
        channel_members = sender.get_voice_channel_members(area=area)
    except Exception:
        return 0
    if not isinstance(channel_members, dict):
        return 0
    members = channel_members.get(voice_channel) or []
    if isinstance(members, dict):
        members = members.get("members") or members.get("list") or []
    if not isinstance(members, list):
        return 0

    uids: set[str] = set()
    for member in members:
        if isinstance(member, dict):
            uid = str(
                member.get("uid")
                or member.get("id")
                or member.get("personUid")
                or member.get("person_uid")
                or ""
            ).strip()
        else:
            uid = str(member or "").strip()
        if uid and uid != bot_user:
            uids.add(uid)
    return len(uids)


def _playback_payload(music, area: str, voice_channel: str, bot_user: str) -> dict:
    queue = music._get_queue(area)
    current = queue.get_current()
    play_state = queue.get_play_state() or {}
    duration_seconds = 0.0
    elapsed = 0.0
    if current:
        duration_seconds = float(current.get("duration_ms") or 0) / 1000
        if not duration_seconds:
            duration_seconds = float(play_state.get("duration") or 0)
        if play_state.get("paused"):
            elapsed = float(play_state.get("pause_elapsed") or 0)
        else:
            start_time = float(play_state.get("start_time") or 0)
            elapsed = max(0.0, time.time() - start_time) if start_time else 0.0
        if duration_seconds:
            elapsed = min(elapsed, duration_seconds)

    return {
        "current": _qq_song_payload(current) if current else None,
        "playing": bool(current),
        "paused": bool(play_state.get("paused")),
        "loading": bool(play_state.get("loading")),
        "progress": elapsed,
        "duration": duration_seconds,
        "queue_length": int(queue.get_queue_length() or 0),
        "online_count": _music_channel_member_count(
            music,
            area,
            voice_channel,
            bot_user,
        ),
    }


def _status_summary(
    music,
    area: str,
    voice_channel: str,
    bot_user: str,
    playback: dict | None = None,
) -> str:
    playback = playback or _playback_payload(music, area, voice_channel, bot_user)
    current = playback["current"]

    lines = ["Oopz Music 状态"]
    if current:
        state_text = "已暂停" if playback["paused"] else "播放中"
        lines.extend(
            [
                "├─ 当前播放",
                f"│  ├─ {current['name']} - {current['artists']}",
                f"│  ├─ {_format_seconds(playback['progress'])} / "
                f"{_format_seconds(playback['duration'])}",
                f"│  └─ {state_text}",
            ]
        )
    else:
        lines.append("├─ 当前播放：无")

    lines.append(f"├─ 待播队列：{playback['queue_length']} 首")
    lines.append(f"└─ Music 在线：{playback['online_count']} 人")
    return "\n".join(lines)


def _queue_summary(music, area: str) -> str:
    queue = music._get_queue(area)
    current = queue.get_current()
    pending = queue.get_queue()
    lines = ["Oopz 播放队列"]

    if current:
        lines.append(
            f"├─ 正在播放：{current.get('name', '未知歌曲')} - "
            f"{current.get('artists', '未知歌手')}"
        )
    else:
        lines.append("├─ 正在播放：无")

    if not pending:
        lines.append("└─ 待播：空")
        return "\n".join(lines)

    lines.append(f"└─ 待播（{len(pending)}首）")
    shown = pending[:10]
    for index, song in enumerate(shown, 1):
        branch = "└─" if index == len(shown) and len(pending) <= 10 else "├─"
        lines.append(
            f"   {branch} {index}. {song.get('name', '未知歌曲')} - "
            f"{song.get('artists', '未知歌手')}"
        )
    if len(pending) > 10:
        lines.append(f"   └─ ……另有 {len(pending) - 10} 首")
    lines.append("发送：删除 <编号...>，例如“删除 2 5”")
    return "\n".join(lines)


def _queue_panel(music, area: str, notice: str = "") -> dict:
    queue = music._get_queue(area)
    current = queue.get_current()
    pending = queue.get_queue()
    message = _queue_summary(music, area)
    if notice:
        message = f"{notice}\n\n{message}"
    return {
        "ok": True,
        "reply_type": "queue_panel",
        "message": message,
        "notice": notice,
        "current": _qq_song_payload(current) if current else None,
        "queue_items": [
            _qq_song_payload(song, index)
            for index, song in enumerate(pending[:10], 1)
        ],
        "queue_all": [
            _qq_song_payload(song, index)
            for index, song in enumerate(pending, 1)
        ],
        "queue_length": len(pending),
    }


def _parse_queue_positions(value: str) -> list[int] | None:
    tokens = [token for token in re.split(r"[\s,，]+", value.strip()) if token]
    if not tokens or len(tokens) > 10 or any(not token.isdigit() for token in tokens):
        return None
    positions = [int(token) for token in tokens]
    return positions if all(position > 0 for position in positions) else None


def _remove_queue_items(music, area: str, positions: list[int]) -> dict:
    queue = music._get_queue(area)
    try:
        remove_positions = getattr(queue, "remove_positions", None)
        if callable(remove_positions):
            removed = remove_positions(positions)
        else:
            # The embedded legacy Redis queue exposes remove_from_queue with
            # zero-based indexes. Validate every requested position before
            # mutating, then delete from the end so earlier indexes stay valid.
            pending = queue.get_queue()
            normalized = sorted(set(positions))
            if (
                not normalized
                or normalized[0] < 1
                or normalized[-1] > len(pending)
            ):
                raise IndexError("queue position out of range")
            removed = [pending[position - 1] for position in normalized]
            for position in reversed(normalized):
                if queue.remove_from_queue(position - 1) is False:
                    raise RuntimeError(f"删除队列位置 {position} 失败")
    except IndexError:
        length = queue.get_queue_length()
        if not length:
            return {"ok": False, "message": "待播队列为空，没有可删除的歌曲"}
        return {"ok": False, "message": f"编号超出范围，请输入 1-{length}"}

    names = "、".join(str(song.get("name") or "未知歌曲") for song in removed)
    return _queue_panel(music, area, f"已删除 {len(removed)} 首：{names}")


def _search_keyword(command: str) -> str:
    for prefix in ("搜索歌曲", "搜歌"):
        if command.startswith(prefix):
            return command[len(prefix):].strip()
    return ""


def _search_songs(music, keyword: str, requester_key: str) -> dict:
    if not keyword:
        return {"ok": False, "message": "请输入关键词，例如：搜歌 搁浅"}
    if len(keyword) > 100:
        return {"ok": False, "message": "搜索关键词过长"}

    results = music.search_candidates(keyword, "qq", limit=10)
    if not results:
        return {"ok": False, "message": f"QQ音乐未找到：{keyword}"}

    songs = [dict(song, platform="qq") for song in results[:10]]
    _search_sessions[requester_key] = {
        "expires_at": time.monotonic() + _SEARCH_SESSION_TTL_SECONDS,
        "songs": songs,
    }

    lines = [f'搜歌“{keyword}”', "├─ 候选歌曲"]
    for index, song in enumerate(songs, 1):
        branch = "└─" if index == len(songs) else "├─"
        duration = str(song.get("durationText") or "")
        suffix = f" [{duration}]" if duration else ""
        lines.append(
            f"│  {branch} {index}. {song.get('name', '未知歌曲')} - "
            f"{song.get('artists', '未知歌手')}{suffix}"
        )
    lines.append("└─ 5分钟内发送：选歌 <编号>")
    return {
        "ok": True,
        "reply_type": "search_results",
        "message": "\n".join(lines),
        "songs": [
            _qq_song_payload(song, index)
            for index, song in enumerate(songs, 1)
        ],
    }


def _select_song(
    music,
    index: int,
    requester_key: str,
    area: str,
    text_channel: str,
    voice_channel: str,
    bot_user: str,
) -> dict:
    session = _search_sessions.get(requester_key)
    if not session or time.monotonic() > float(session.get("expires_at") or 0):
        _search_sessions.pop(requester_key, None)
        return {"ok": False, "message": "搜歌结果已失效，请重新发送：搜歌 <关键词>"}

    songs = session.get("songs") or []
    if index < 1 or index > len(songs):
        return {"ok": False, "message": f"编号无效，请输入 1-{len(songs)}"}

    current_channel = getattr(music, "_voice_channel_id", None)
    current_area = getattr(music, "_voice_channel_area", None)
    if current_channel != voice_channel or current_area != area:
        result = music.enter_voice_channel(voice_channel, area)
        if not isinstance(result, dict) or result.get("error"):
            detail = result.get("error") if isinstance(result, dict) else "unknown"
            return {"ok": False, "message": f"进入 Oopz 语音频道失败: {detail}"}

    song = songs[index - 1]
    _search_sessions.pop(requester_key, None)
    music.play_song_choice(song, text_channel, area, bot_user)
    return {
        "ok": True,
        "reply_type": "song_selected",
        "message": f"已选择：{song.get('name', '未知歌曲')} - {song.get('artists', '未知歌手')}",
        "song": _qq_song_payload(song),
    }


def _execute_command(command: str, requester_key: str) -> dict:
    with _command_lock:
        music = _music_handler()
        if music is None:
            return {"ok": False, "message": "Oopzbot 音乐模块尚未初始化"}

        area, text_channel, voice_channel, bot_user = _command_config()
        if not all((area, text_channel, voice_channel, bot_user)):
            return {
                "ok": False,
                "message": "QQBot 的 Oopz 域、文字频道或语音频道尚未配置",
            }

        if command in {"状态", "当前播放", "播放状态"}:
            playback = _playback_payload(
                music,
                area=area,
                voice_channel=voice_channel,
                bot_user=bot_user,
            )
            return {
                "ok": True,
                "reply_type": "playback_status",
                "message": _status_summary(
                    music,
                    area=area,
                    voice_channel=voice_channel,
                    bot_user=bot_user,
                    playback=playback,
                ),
                **playback,
            }

        queue_remove = re.fullmatch(
            r"(?:删除(?:队列)?|移除(?:队列)?|队列(?:删除|移除))\s+(.+)",
            command,
        )
        if queue_remove:
            positions = _parse_queue_positions(queue_remove.group(1))
            if positions is None:
                return {"ok": False, "message": "用法：删除 <编号...>，例如“删除 2 5”"}
            return _remove_queue_items(music, area, positions)

        if command in {"队列", "播放队列", "待播", "面板", "队列面板", "播放面板"}:
            return _queue_panel(music, area)

        if command in {"排行榜", "榜单", "QQ音乐排行榜", "QQ排行榜"}:
            return _rank_catalog()

        rank_selection = re.fullmatch(r"(?:榜单点歌|榜单选歌)\s*(\d+)", command)
        if rank_selection:
            return _select_rank_song(
                music,
                index=int(rank_selection.group(1)),
                requester_key=requester_key,
                area=area,
                text_channel=text_channel,
                voice_channel=voice_channel,
                bot_user=bot_user,
            )

        rank_batch = re.fullmatch(
            r"(?:榜单批量|榜单前)\s*(\d+)?\s*(?:首)?\s*(?:加入队列)?",
            command,
        )
        if command == "榜单全部加入" or rank_batch:
            count = int(rank_batch.group(1) or 10) if rank_batch else 10
            return _queue_rank_songs(
                music,
                count=count,
                requester_key=requester_key,
                area=area,
                text_channel=text_channel,
                voice_channel=voice_channel,
                bot_user=bot_user,
            )

        rank_query = ""
        rank_match = re.fullmatch(r"(?:榜单|排行榜)\s+(.+)", command)
        if rank_match:
            rank_query = rank_match.group(1).strip()
        elif command.endswith("榜"):
            rank_query = command
        if rank_query:
            resolved_rank = _resolve_rank(rank_query)
            if resolved_rank:
                rank_id, rank_title = resolved_rank
                return _rank_detail(rank_id, rank_title, requester_key)
            return {
                "ok": False,
                "message": "未找到该榜单，请发送“排行榜”查看可用榜单",
            }

        search_keyword = _search_keyword(command)
        if search_keyword:
            return _search_songs(music, search_keyword, requester_key)

        selection_match = re.fullmatch(r"(?:选歌|选择)\s*(\d+)", command)
        if selection_match:
            return _select_song(
                music,
                index=int(selection_match.group(1)),
                requester_key=requester_key,
                area=area,
                text_channel=text_channel,
                voice_channel=voice_channel,
                bot_user=bot_user,
            )

        keyword = _play_keyword(command)
        if keyword:
            if len(keyword) > 100:
                return {"ok": False, "message": "歌曲关键词过长"}

            current_channel = getattr(music, "_voice_channel_id", None)
            current_area = getattr(music, "_voice_channel_area", None)
            if current_channel != voice_channel or current_area != area:
                result = music.enter_voice_channel(voice_channel, area)
                if not isinstance(result, dict):
                    return {"ok": False, "message": "进入 Oopz 语音频道失败"}
                if result.get("error"):
                    logger.warning("QQBot 触发进入语音频道失败: %s", result.get("error"))
                    return {
                        "ok": False,
                        "message": f"进入 Oopz 语音频道失败: {result['error']}",
                    }

            music.play_song(keyword, "qq", text_channel, area, bot_user)
            return {"ok": True, "message": f"已提交点歌：{keyword}"}

        if command in {"下一首", "切歌", "跳过", "下一个"}:
            music.play_next(text_channel, area, bot_user)
            return {"ok": True, "message": "已执行切歌"}

        if command in {"停止", "停止播放", "停", "关"}:
            music.stop_play(text_channel, area)
            return {"ok": True, "message": "已停止播放"}

        if command in {"暂停", "暂停播放"}:
            queue = music._get_queue(area)
            current = queue.get_current()
            if not current:
                return {"ok": False, "message": "当前没有正在播放的歌曲"}
            play_state = queue.get_play_state() or {}
            if play_state.get("paused"):
                return {"ok": True, "message": "播放已经暂停"}
            voice = getattr(music, "voice", None)
            if not voice or not voice.pause_audio():
                return {"ok": False, "message": "暂停播放失败，请稍后重试"}
            start_time = float(play_state.get("start_time") or time.time())
            pause_elapsed = max(0.0, time.time() - start_time)
            duration = float(play_state.get("duration") or 0)
            if duration:
                pause_elapsed = min(pause_elapsed, duration)
            play_state.update({
                "paused": True,
                "pause_elapsed": pause_elapsed,
                "loading": False,
            })
            queue.set_play_state(play_state)
            return {"ok": True, "message": "已暂停播放"}

        if command in {"继续", "继续播放", "恢复", "恢复播放"}:
            queue = music._get_queue(area)
            current = queue.get_current()
            if not current:
                return {"ok": False, "message": "当前没有可继续播放的歌曲"}
            play_state = queue.get_play_state() or {}
            if not play_state.get("paused"):
                return {"ok": True, "message": "歌曲正在播放"}
            voice = getattr(music, "voice", None)
            if not voice or not voice.resume_audio():
                return {"ok": False, "message": "继续播放失败，请稍后重试"}
            pause_elapsed = max(0.0, float(play_state.get("pause_elapsed") or 0))
            resumed_start_time = time.time() - pause_elapsed
            play_state.update({
                "paused": False,
                "start_time": resumed_start_time,
                "loading": False,
            })
            play_state.pop("pause_elapsed", None)
            queue.set_play_state(play_state)
            music._play_start_time = resumed_start_time
            return {"ok": True, "message": "已继续播放"}

        if command in {
            "频道成员",
            "所有频道",
            "频道列表",
            "所有语音频道",
            "语音频道",
            "在线",
            "在线成员",
        }:
            return {
                "ok": True,
                "message": _all_voice_channels_summary(
                    music,
                    area=area,
                    bot_user=bot_user,
                ),
            }

        if command in {
            "语音成员",
            "谁在频道",
            "谁在听",
            "有谁",
        }:
            return {
                "ok": True,
                "message": _voice_member_summary(
                    music,
                    area=area,
                    voice_channel=voice_channel,
                    bot_user=bot_user,
                ),
            }

        if command in {"帮助", "菜单", "help", "/help"}:
            return {
                "ok": True,
                "message": _HELP_MESSAGE,
            }

        return {
            "ok": False,
            "message": "无法识别命令。请使用：面板、搜歌 <歌名>、点歌 <歌名>、暂停、继续、切歌、停止",
        }


def _bridge_request_allowed(request: Request) -> JSONResponse | None:
    client_host = request.client.host if request.client else ""
    settings = get_settings()
    private_client = False
    try:
        private_client = ipaddress.ip_address(client_host).is_private
    except ValueError:
        pass
    allowed_client = client_host in _LOOPBACK_HOSTS or (
        settings.bridge_private_network and private_client
    )
    if not allowed_client:
        return JSONResponse(
            {"ok": False, "message": "仅允许本机或已启用的容器内网访问"},
            status_code=403,
        )

    supplied_token = request.headers.get("x-qqbot-bridge-token", "")
    if not settings.bridge_token or not secrets.compare_digest(
        supplied_token,
        settings.bridge_token,
    ):
        return JSONResponse(
            {"ok": False, "message": "桥接认证失败"},
            status_code=401,
        )
    return None


def _command_event(command: str, result: dict, source: str) -> None:
    if not result.get("ok"):
        return
    mutating = (
        "点歌",
        "播放",
        "选歌",
        "榜单点歌",
        "榜单批量",
        "删除",
        "移除",
        "暂停",
        "继续",
        "恢复",
        "切歌",
        "下一首",
        "跳过",
        "停止",
    )
    if command.startswith(mutating):
        operations.record_event(
            "command",
            str(result.get("message") or f"已执行：{command}"),
            source=source or "未知用户",
        )


def _service_health(music) -> dict[str, dict]:
    settings = get_settings()
    stored = operations.snapshot().get("components", {})
    runtime = getattr(music, "runtime", None)
    oopz_ready = bool(getattr(runtime, "ready", False))
    components: dict[str, dict] = {
        "bridge": {"status": "online", "message": "内部控制接口正常"},
        "oopz": {
            "status": "online" if oopz_ready else "error",
            "message": "OOPZ SDK 已连接" if oopz_ready else "OOPZ SDK 未就绪",
        },
        "qq_bot": stored.get(
            "qq_bot",
            {"status": "starting", "message": "等待 QQ 网关上线"},
        ),
    }

    if settings.qq_music_enabled:
        try:
            response = requests.get(
                f"{settings.qq_music_base_url.rstrip('/')}/explorer/metadata",
                timeout=3,
            )
            response.raise_for_status()
            components["qq_music"] = {
                "status": "online",
                "message": "QQ 音乐接口可访问",
            }
        except Exception as exc:
            components["qq_music"] = {
                "status": "error",
                "message": f"QQ 音乐接口异常：{type(exc).__name__}",
            }
    else:
        components["qq_music"] = {"status": "disabled", "message": "未启用"}

    jm_enabled = _env("QQBOT_JM_ENABLED").lower() in {"1", "true", "yes", "on"}
    if not jm_enabled:
        components["uploader"] = {"status": "disabled", "message": "JM 未启用"}
    else:
        project_root = Path(__file__).resolve().parents[1]
        uploader = Path(
            _env("QQBOT_JM_UPLOADER")
            or project_root / "tools" / "qqbot-uploader" / "uploader.mjs"
        )
        node = _env("QQBOT_JM_NODE") or "node"
        sdk = uploader.parent / "node_modules" / "@tencent-connect" / "qqbot-nodejs"
        ready = uploader.is_file() and (Path(node).is_file() or shutil.which(node)) and sdk.is_dir()
        components["uploader"] = {
            "status": "online" if ready else "error",
            "message": "QQ 分片上传器就绪" if ready else "上传器或依赖缺失",
        }
    return components


def _panel_snapshot() -> dict:
    with _command_lock:
        music = _music_handler()
        if music is None:
            raise RuntimeError("Oopzbot 音乐模块尚未初始化")
        area, _text_channel, voice_channel, bot_user = _command_config()
        if not all((area, voice_channel, bot_user)):
            raise RuntimeError("Oopz 域或语音频道尚未配置")
        playback = _playback_payload(music, area, voice_channel, bot_user)
        queue = _queue_panel(music, area)
        channel_error = ""
        try:
            channels = _voice_channels_payload(music, area, voice_channel, bot_user)
        except Exception as exc:
            logger.warning("面板读取语音频道失败: %s", exc)
            channels = []
            channel_error = str(exc)[:300]
    records = operations.snapshot()
    return {
        "ok": True,
        "playback": playback,
        "queue": queue.get("queue_all", []),
        "queue_length": queue.get("queue_length", 0),
        "channels": channels,
        "channel_error": channel_error,
        "health": _service_health(music),
        "events": records.get("events", []),
        "jm_jobs": records.get("jm_jobs", []),
        "jm_enabled": _env("QQBOT_JM_ENABLED").lower()
        in {"1", "true", "yes", "on"},
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


@router.post("/internal/qqbot/command")
async def qqbot_command(request: Request):
    if rejection := _bridge_request_allowed(request):
        return rejection

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"ok": False, "message": "请求体不是有效 JSON"},
            status_code=400,
        )

    command = str(body.get("command") or "").strip()
    if not command:
        return JSONResponse(
            {"ok": False, "message": "命令不能为空"},
            status_code=400,
        )
    if len(command) > 150:
        return JSONResponse(
            {"ok": False, "message": "命令过长"},
            status_code=400,
        )

    try:
        requester_id = str(body.get("requester_id") or "anonymous").strip()
        requester_name = str(body.get("requester_name") or requester_id).strip()
        group_openid = str(body.get("group_openid") or "unknown-group").strip()
        requester_key = f"{group_openid}:{requester_id}"
        result = await asyncio.to_thread(_execute_command, command, requester_key)
        _command_event(command, result, requester_name)
        return JSONResponse(result)
    except Exception as exc:
        logger.exception("执行 QQBot 桥接命令失败")
        return JSONResponse(
            {"ok": False, "message": f"执行失败: {type(exc).__name__}"},
            status_code=500,
        )


@router.get("/internal/panel/snapshot")
async def panel_snapshot(request: Request):
    if rejection := _bridge_request_allowed(request):
        return rejection
    try:
        return JSONResponse(await asyncio.to_thread(_panel_snapshot))
    except Exception as exc:
        logger.exception("生成面板快照失败")
        return JSONResponse(
            {"ok": False, "message": f"读取状态失败: {type(exc).__name__}"},
            status_code=503,
        )


@router.get("/healthz")
async def healthz():
    return {"ok": True, "music_ready": _music_dependency is not None}
