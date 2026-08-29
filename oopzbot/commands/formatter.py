"""Transport-neutral command text formatting."""

from __future__ import annotations

from collections.abc import Sequence

from oopzbot.domain.contracts import QueueSnapshot, SongCandidate


def format_seconds(value: object) -> str:
    try:
        seconds = max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        seconds = 0
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


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
