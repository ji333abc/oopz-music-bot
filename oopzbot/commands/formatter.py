"""Transport-neutral command text formatting."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from oopzbot.domain.contracts import QueueSnapshot, SongCandidate


def format_seconds(value: object) -> str:
    try:
        seconds = max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        seconds = 0
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _song_field(song: Mapping | SongCandidate, key: str, default=""):
    return song.get(key, default) if isinstance(song, Mapping) else getattr(song, key, default)


def bilibili_identity(song: Mapping | SongCandidate) -> str:
    """Only validated BV/page identity may appear in public Bilibili messages."""
    identifier = str(_song_field(song, "song_id") or _song_field(song, "id"))
    match = re.fullmatch(r"(BV[0-9A-Za-z]{10})(?:\?p=([0-9]{1,5}))?", identifier)
    if not match:
        return "音源"
    page = int(match[2] or 1)
    if not 1 <= page <= 10000:
        return "音源"
    return f"{match[1]} · 第{page}P"


def public_song_label(song: Mapping | SongCandidate) -> str:
    if _song_field(song, "platform") == "bilibili":
        return f"B站 {bilibili_identity(song)}"
    return (
        f"{_song_field(song, 'name') or '未知歌曲'} - {_song_field(song, 'artists') or '未知歌手'}"
    )


def public_song_attachments(song: Mapping) -> list:
    return [] if song.get("platform") == "bilibili" else list(song.get("attachments") or [])


def _bilibili_duration(song: Mapping) -> str:
    duration = song.get("durationText") or song.get("duration")
    if isinstance(duration, str) and re.fullmatch(
        r"[0-9]{1,5}:[0-5][0-9](?::[0-5][0-9])?", duration
    ):
        seconds = 0
        for part in duration.split(":"):
            seconds = seconds * 60 + int(part)
    else:
        try:
            seconds = (
                float(
                    song.get("duration_ms")
                    or (duration if not isinstance(duration, str) else 0)
                    or 0
                )
                / 1000
            )
        except (TypeError, ValueError):
            seconds = 0
    return format_seconds(seconds)


def public_song_metadata(song: Mapping) -> dict:
    """Copy display fields without changing persisted or Panel metadata."""
    result = dict(song)
    if song.get("platform") == "bilibili":
        duration = _bilibili_duration(song)
        result.update(
            name=public_song_label(song),
            artists="",
            album="",
            cover="",
            attachments=[],
            duration=duration,
            durationText=duration,
        )
    return result


def format_bilibili_request(
    song: Mapping, *, queue_position: int | None = None, status: str = "已提交播放"
) -> str:
    if queue_position is not None:
        status = "已加入队列"
    text = (
        f"📺 B站音源 · {status}\n\n"
        f"视频：{bilibili_identity(song)}\n"
        f"时长：{_bilibili_duration(song)}"
    )
    if queue_position is not None:
        text += f"\n队列位置：{queue_position}"
    return text


def format_queue(snapshot: QueueSnapshot) -> str:
    lines = ["Oopz 播放队列"]
    if snapshot.current:
        song = snapshot.current.song
        lines.append(f"├─ 正在播放：{public_song_label(song)}")
    else:
        lines.append("├─ 正在播放：无")
    if not snapshot.pending:
        lines.append("└─ 待播：空")
        return "\n".join(lines)
    lines.append(f"└─ 待播（{snapshot.queue_length}首）")
    shown = snapshot.pending[:10]
    for index, item in enumerate(shown, 1):
        branch = "└─" if index == len(shown) and snapshot.queue_length <= 10 else "├─"
        lines.append(f"   {branch} {index}. {public_song_label(item.song)}")
    if snapshot.queue_length > 10:
        lines.append(f"   └─ ……另有 {snapshot.queue_length - 10} 首")
    lines.append("发送：删除 <编号...>，例如“删除 2 5”")
    return "\n".join(lines)


def format_search(keyword: str, songs: Sequence[SongCandidate]) -> str:
    lines = [f"搜歌“{keyword}”", "├─ 候选歌曲"]
    for index, song in enumerate(songs, 1):
        branch = "└─" if index == len(songs) else "├─"
        suffix = f" [{song.duration_text}]" if song.duration_text else ""
        if song.platform == "bilibili":
            suffix = f" [{format_seconds(song.duration_ms / 1000)}]"
        lines.append(f"│  {branch} {index}. {public_song_label(song)}{suffix}")
    lines.append("└─ 5分钟内发送：选歌 <编号>")
    return "\n".join(lines)
