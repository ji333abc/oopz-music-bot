"""Pure parsing rules shared by command transports and handlers."""

from __future__ import annotations

import re

_PLAY_PREFIXES = (
    "播放歌曲",
    "点播歌曲",
    "来一首",
    "放一首",
    "点歌",
    "播放",
    "点播",
    "放",
    "唱",
)
_SEARCH_PREFIXES = ("搜索歌曲", "搜歌")
_PLATFORM_PREFIXES = (
    ("qq:", "qq"),
    ("qq：", "qq"),
    ("b站:", "bilibili"),
    ("b站：", "bilibili"),
    ("bili:", "bilibili"),
    ("bili：", "bilibili"),
    ("bilibili:", "bilibili"),
    ("bilibili：", "bilibili"),
    ("网易:", "netease"),
    ("网易：", "netease"),
    ("netease:", "netease"),
    ("netease：", "netease"),
)


def _argument_after_prefix(value: str, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix) :].strip()
    return ""


def play_keyword(command: str) -> str:
    return _argument_after_prefix(command, _PLAY_PREFIXES)


def matches_play_command(command: str) -> bool:
    return any(str(command or "").startswith(prefix) for prefix in _PLAY_PREFIXES)


def search_keyword(command: str) -> str:
    return _argument_after_prefix(command, _SEARCH_PREFIXES)


def matches_search_command(command: str) -> bool:
    return any(str(command or "").startswith(prefix) for prefix in _SEARCH_PREFIXES)


def parse_platform_keyword(keyword: str, *, default: str = "qq") -> tuple[str, str]:
    """Parse legacy-compatible music prefixes without importing legacy code."""

    value = str(keyword or "").strip()
    lowered = value.lower()
    for prefix, platform in _PLATFORM_PREFIXES:
        if lowered.startswith(prefix):
            return platform, value[len(prefix) :].strip()
    return default, value


def parse_queue_positions(value: str, *, maximum: int = 10) -> list[int] | None:
    tokens = [token for token in re.split(r"[\s,，]+", value.strip()) if token]
    if (
        not tokens
        or len(tokens) > maximum
        or any(not token.isdigit() for token in tokens)
    ):
        return None
    positions = [int(token) for token in tokens]
    return positions if all(position > 0 for position in positions) else None
