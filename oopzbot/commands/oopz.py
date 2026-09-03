"""OOPZ transport normalization for the shared command boundary."""

from __future__ import annotations

import re

from .parser import matches_play_command, matches_search_command
from .registry import CommandKind, exact_command_kind

_MODERN_KINDS = {
    CommandKind.STATUS,
    CommandKind.QUEUE,
    CommandKind.NEXT,
    CommandKind.STOP,
    CommandKind.PAUSE,
    CommandKind.RESUME,
}
_LEGACY_ALIASES = {
    "列表": "队列",
    "播放列表": "队列",
}
_SLASH_PLATFORMS = {
    "qq": "qq",
    "bili": "bilibili",
    "bilibili": "bilibili",
    "b站": "bilibili",
    "netease": "netease",
    "网易": "netease",
}
_LIKED_COMMAND = re.compile(
    r"(?:随机|随机播放|喜欢|随便来一首)(?:\s+\d+)?|"
    r"喜欢列表(?:\s+\d+)?|喜欢点歌\s+\d+"
)
_ALBUM_COMMAND = re.compile(
    r"专辑(?:\s+.+)?|取消专辑|"
    r"(?:专辑选择|选专辑|专辑点歌)\s*\d+|"
    r"专辑曲目(?:\s+\d+)?|专辑加入\s+.+"
)


def _normalize_slash(command: str) -> str | None:
    parts = command.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None
    name = parts[0].lower()
    arguments = parts[1:]
    exact = {
        "/next": "下一首",
        "/queue": "队列",
        "/st": "停止",
        "/stop": "停止",
    }
    if name in exact and not arguments:
        return exact[name]
    if name == "/pick" and len(arguments) == 1 and arguments[0].isdigit():
        return f"选歌 {arguments[0]}"
    if name == "/songsearch":
        return f"搜歌 {' '.join(arguments)}".rstrip()
    if name in {"/bf", "/play"}:
        if not arguments:
            return "播放"
        platform = _SLASH_PLATFORMS.get(arguments[0].lower())
        if platform and len(arguments) > 1:
            return f"播放 {platform}:{' '.join(arguments[1:])}"
        return f"播放 {' '.join(arguments)}"
    if name == "/yun" and len(arguments) > 1 and arguments[0].lower() == "play":
        return f"播放 netease:{' '.join(arguments[1:])}"
    if name == "/like":
        if not arguments:
            return "喜欢"
        subcommand = arguments[0].lower()
        if subcommand == "list" and len(arguments) <= 2:
            page = arguments[1] if len(arguments) == 2 else "1"
            return f"喜欢列表 {page}" if page.isdigit() else "喜欢用法"
        if subcommand == "play" and len(arguments) == 2 and arguments[1].isdigit():
            return f"喜欢点歌 {arguments[1]}"
        if len(arguments) == 1 and arguments[0].isdigit():
            return f"喜欢 {arguments[0]}"
        return "喜欢用法"
    return None


def normalize_oopz_music_command(command: str) -> str | None:
    """Return a canonical command only when the modern boundary owns it."""

    stripped = command.strip()
    normalized = _normalize_slash(stripped)
    if normalized is None:
        normalized = _LEGACY_ALIASES.get(stripped, stripped)
    if exact_command_kind(normalized) in _MODERN_KINDS:
        return normalized
    if matches_play_command(normalized) or matches_search_command(normalized):
        return normalized
    if re.fullmatch(r"(?:选歌|选择)\s*\d+", normalized):
        return normalized
    if re.fullmatch(
        r"(?:删除(?:队列)?|移除(?:队列)?|队列(?:删除|移除))\s+.+",
        normalized,
    ):
        return normalized
    if normalized == "喜欢用法" or _LIKED_COMMAND.fullmatch(normalized):
        return normalized
    if _ALBUM_COMMAND.fullmatch(normalized):
        return normalized
    return None


def backend_notifies(command: str) -> bool:
    """Whether the compatibility media backend already emits the OOPZ reply."""

    return bool(
        matches_play_command(command)
        or re.fullmatch(r"(?:选歌|选择)\s*\d+", command)
        or exact_command_kind(command) in {CommandKind.NEXT, CommandKind.STOP}
        or _LIKED_COMMAND.fullmatch(command)
        or re.fullmatch(r"专辑点歌\s*\d+", command)
    )
