"""Canonical exact command aliases shared by every transport."""

from __future__ import annotations

from enum import StrEnum


class CommandKind(StrEnum):
    STATUS = "status"
    QUEUE = "queue"
    RANK_CATALOG = "rank_catalog"
    NEXT = "next"
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    VOICE_CHANNELS = "voice_channels"
    VOICE_MEMBERS = "voice_members"
    HELP = "help"


COMMAND_ALIASES: dict[CommandKind, frozenset[str]] = {
    CommandKind.STATUS: frozenset({"状态", "当前播放", "播放状态"}),
    CommandKind.QUEUE: frozenset(
        {"队列", "播放队列", "待播", "面板", "队列面板", "播放面板"}
    ),
    CommandKind.RANK_CATALOG: frozenset(
        {"排行榜", "榜单", "QQ音乐排行榜", "QQ排行榜"}
    ),
    CommandKind.NEXT: frozenset({"下一首", "切歌", "跳过", "下一个"}),
    CommandKind.STOP: frozenset({"停止", "停止播放", "停", "关"}),
    CommandKind.PAUSE: frozenset({"暂停", "暂停播放"}),
    CommandKind.RESUME: frozenset({"继续", "继续播放", "恢复", "恢复播放"}),
    CommandKind.VOICE_CHANNELS: frozenset(
        {"频道成员", "所有频道", "频道列表", "所有语音频道", "语音频道", "在线", "在线成员"}
    ),
    CommandKind.VOICE_MEMBERS: frozenset({"语音成员", "谁在频道", "谁在听", "有谁"}),
    CommandKind.HELP: frozenset({"帮助", "菜单", "help", "/help"}),
}

_ALIAS_LOOKUP = {
    alias: kind for kind, aliases in COMMAND_ALIASES.items() for alias in aliases
}


def exact_command_kind(command: str) -> CommandKind | None:
    return _ALIAS_LOOKUP.get(str(command or "").strip())
