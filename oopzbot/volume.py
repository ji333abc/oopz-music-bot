"""Persisted playback volume shared by the player and control panel."""

import os

VOLUME_KEY = "music:volume"


def normalize_volume(value, default=30) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return default


def default_volume() -> int:
    return normalize_volume(os.getenv("OOPZ_DEFAULT_VOLUME", "30"))


def saved_volume(redis) -> int:
    if redis is None:
        return default_volume()
    return normalize_volume(redis.get(VOLUME_KEY), default_volume())


def set_music_volume(music, value: int) -> dict:
    if not 0 <= value <= 100:
        return {"ok": False, "message": "音量必须在 0～100 之间"}
    voice = getattr(music, "voice", None)
    redis = getattr(music.queue, "redis", None)
    if redis is None or not callable(getattr(voice, "set_volume", None)):
        return {"ok": False, "message": "当前播放核心不支持音量调整"}
    redis.set(VOLUME_KEY, str(value))
    if voice and voice.available:
        if not voice.set_volume(value):
            return {"ok": False, "volume": value, "message": "音量已保存，但当前应用失败；重新连接语音后生效"}
    return {"ok": True, "volume": value, "message": f"音量已设为 {value}%，重启后保留"}
