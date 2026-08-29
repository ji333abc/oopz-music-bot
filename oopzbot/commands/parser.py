"""Pure parsing rules shared by command transports and handlers."""

from __future__ import annotations

import re

_PLAY_PREFIXES = ("播放歌曲", "点播歌曲", "来一首", "放一首", "点歌", "播放", "点播")
_SEARCH_PREFIXES = ("搜索歌曲", "搜歌")


def _argument_after_prefix(value: str, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix) :].strip()
    return ""


def play_keyword(command: str) -> str:
    return _argument_after_prefix(command, _PLAY_PREFIXES)


def search_keyword(command: str) -> str:
    return _argument_after_prefix(command, _SEARCH_PREFIXES)


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
