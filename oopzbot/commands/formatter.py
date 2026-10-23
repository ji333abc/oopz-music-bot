"""Transport-neutral command text formatting."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import parse_qs

from oopzbot.domain.contracts import QueueSnapshot, SongCandidate


def format_seconds(value: object) -> str:
    try:
        seconds = max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        seconds = 0
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def format_bilibili_request(song: Mapping, *, queue_position: int | None = None) -> str:
    identifier = str(song.get("song_id") or song.get("id") or "")
    bvid, _, query = identifier.partition("?")
    page = parse_qs(query).get("p", ["1"])[0]
    duration = song.get("durationText") or song.get("duration")
    if not isinstance(duration, str) or ":" not in duration:
        try:
            seconds = float(song.get("duration_ms") or duration or 0) / 1000
        except (TypeError, ValueError):
            seconds = 0
        duration = format_seconds(seconds)
    status = "已提交播放" if queue_position is None else "已加入队列"
    text = (
        f"📺 B站音源 · {status}\n\n"
        f"标题：{song.get('name') or '未知视频'}\n"
        f"UP主：{song.get('artists') or '未知UP主'}\n"
        f"时长：{duration}\n"
        f"视频：{bvid} · 第{page}P"
    )
    if queue_position is not None:
        text += f"\n队列位置：{queue_position}"
    return text


def format_queue(snapshot: QueueSnapshot) -> str:
    lines = ["Oopz 播放队列"]
    if snapshot.current:
        song = snapshot.current.song
        lines.append(f"├─ 正在播放：{song.name} - {song.artists}")
    else:
        lines.append("├─ 正在播放：无")
    if not snapshot.pending:
        lines.append("└─ 待播：空")
        return "\n".join(lines)
    lines.append(f"└─ 待播（{snapshot.queue_length}首）")
    shown = snapshot.pending[:10]
    for index, item in enumerate(shown, 1):
        branch = "└─" if index == len(shown) and snapshot.queue_length <= 10 else "├─"
        lines.append(f"   {branch} {index}. {item.song.name} - {item.song.artists}")
    if snapshot.queue_length > 10:
        lines.append(f"   └─ ……另有 {snapshot.queue_length - 10} 首")
    lines.append("发送：删除 <编号...>，例如“删除 2 5”")
    return "\n".join(lines)


def format_search(keyword: str, songs: Sequence[SongCandidate]) -> str:
    lines = [f'搜歌“{keyword}”', "├─ 候选歌曲"]
    for index, song in enumerate(songs, 1):
        branch = "└─" if index == len(songs) else "├─"
        suffix = f" [{song.duration_text}]" if song.duration_text else ""
        lines.append(f"│  {branch} {index}. {song.name} - {song.artists}{suffix}")
    lines.append("└─ 5分钟内发送：选歌 <编号>")
    return "\n".join(lines)
