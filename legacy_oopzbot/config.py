"""Environment-backed compatibility configuration for the legacy OOPZ core.

This file deliberately contains no account credentials. Password-login results are
loaded from the persistent data volume and override stale values from ``.env``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _text(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _bool(name: str, default: bool = False) -> bool:
    raw = _text(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(_text(name, str(default)))
    except ValueError:
        return default


def _csv(name: str) -> list[str]:
    return [item.strip() for item in _text(name).split(",") if item.strip()]


LEGACY_DATA_DIR = Path(_text("OOPZ_LEGACY_DATA_DIR", "/app/data/legacy"))
_CREDENTIALS_PATH = LEGACY_DATA_DIR / "oopz_credentials.json"


def _persisted_credentials() -> dict:
    try:
        data = json.loads(_CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_saved = _persisted_credentials()

OOPZ_CONFIG = {
    "app_version": str(_saved.get("app_version") or _text("OOPZ_APP_VERSION", "73817")),
    "channel": "Web",
    "platform": "windows",
    "web": True,
    "base_url": _text("OOPZ_BASE_URL", "https://gateway.oopz.cn"),
    "login_phone": _text("OOPZ_LOGIN_PHONE", _text("OOPZ_PHONE")),
    "login_password": os.getenv("OOPZ_LOGIN_PASSWORD") or os.getenv("OOPZ_PASSWORD") or "",
    "device_id": str(_saved.get("device_id") or _text("OOPZ_DEVICE_ID")),
    "person_uid": str(_saved.get("person_uid") or _text("OOPZ_PERSON_UID")),
    "jwt_token": str(_saved.get("jwt_token") or _text("OOPZ_JWT_TOKEN")),
    "default_area": _text("QQBOT_OOPZ_AREA_ID", _text("OOPZ_AREA_ID")),
    "default_channel": _text("QQBOT_OOPZ_TEXT_CHANNEL_ID", _text("OOPZ_CHANNEL_ID")),
    "use_announcement_style": _bool("OOPZ_USE_ANNOUNCEMENT_STYLE", False),
    "agora_app_id": _text("OOPZ_AGORA_APP_ID"),
    "agora_init_timeout": _int("OOPZ_AGORA_INIT_TIMEOUT", 180),
    "proxy": _text("BOT_OOPZ_PROXY"),
}

PROXY_ALIAS_CONFIG = {
    "host": _text("OOPZ_PROXY_HOST", "127.0.0.1"),
    "http_port": _int("OOPZ_PROXY_HTTP_PORT", 7890),
    "socks_port": _int("OOPZ_PROXY_SOCKS_PORT", 7891),
}

DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Content-Type": "application/json;charset=utf-8",
    "Origin": "https://web.oopz.cn",
    "Pragma": "no-cache",
    "Priority": "u=1, i",
    "Sec-Ch-Ua": '"Chromium";v="140", "Not=A?Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Linux"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": _text(
        "OOPZ_USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    ),
}

REDIS_CONFIG = {
    "host": _text("BOT_REDIS_HOST", "redis"),
    "port": _int("BOT_REDIS_PORT", 6379),
    "password": os.getenv("BOT_REDIS_PASSWORD") or None,
    "db": _int("BOT_REDIS_DB", 0),
    "decode_responses": True,
}

NETEASE_CLOUD = {
    "base_url": _text("BOT_NETEASE_BASE_URL", "http://netease:3000"),
    "cookie": _text("NETEASE_MUSIC_COOKIE"),
    "auto_start_path": "",
    "audio_download_timeout": _int("NETEASE_AUDIO_DOWNLOAD_TIMEOUT", 120),
    "audio_download_retries": _int("NETEASE_AUDIO_DOWNLOAD_RETRIES", 2),
    "audio_quality": _text("NETEASE_AUDIO_QUALITY", "standard"),
}

DOUBAO_CONFIG = {
    "enabled": _bool("DOUBAO_ENABLED", False),
    "base_url": _text("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
    "api_key": _text("DOUBAO_API_KEY"),
    "model": _text("DOUBAO_MODEL", "doubao-1-5-pro-32k-250115"),
    "system_prompt": _text("DOUBAO_SYSTEM_PROMPT", "你是 OOPZ Bot，请简洁友好地回复。"),
    "max_tokens": _int("DOUBAO_MAX_TOKENS", 1000),
    "temperature": 0.7,
    "context_max_rounds": 10,
    "context_ttl_seconds": 1800,
}

DOUBAO_IMAGE_CONFIG = {
    "enabled": _bool("DOUBAO_IMAGE_ENABLED", False),
    "base_url": _text("DOUBAO_IMAGE_BASE_URL", DOUBAO_CONFIG["base_url"]),
    "api_key": _text("DOUBAO_IMAGE_API_KEY", DOUBAO_CONFIG["api_key"]),
    "model": _text("DOUBAO_IMAGE_MODEL", "doubao-seedream-4-5-251128"),
    "size": _text("DOUBAO_IMAGE_SIZE", "1920x1920"),
    "watermark": _bool("DOUBAO_IMAGE_WATERMARK", False),
}

QQ_MUSIC_CONFIG = {
    "enabled": _bool("QQ_MUSIC_ENABLED", True),
    "base_url": _text("QQ_MUSIC_BASE_URL", "http://qqmusic:3200"),
    "cookie": _text("QQ_MUSIC_COOKIE"),
}

BILIBILI_MUSIC_CONFIG = {
    "enabled": _bool("BILIBILI_MUSIC_ENABLED", False),
    "cookie": _text("BILIBILI_MUSIC_COOKIE"),
}

PROFANITY_CONFIG = {
    "enabled": _bool("OOPZ_PROFANITY_ENABLED", False),
    "mute_duration": _int("OOPZ_PROFANITY_MUTE_MINUTES", 5),
    "recall_message": True,
    "skip_admins": True,
    "warn_before_mute": False,
    "context_detection": True,
    "context_window": 30,
    "context_max_messages": 10,
    "ai_detection": _bool("OOPZ_PROFANITY_AI_ENABLED", False),
    "ai_min_length": 2,
    "keywords": _csv("OOPZ_PROFANITY_KEYWORDS"),
}

WEB_PLAYER_CONFIG = {
    "url": _text("OOPZ_LEGACY_WEB_PUBLIC_URL"),
    "host": _text("OOPZ_LEGACY_WEB_HOST", "0.0.0.0"),
    "port": _int("OOPZ_LEGACY_WEB_PORT", 18081),
    "token_ttl_seconds": _int("OOPZ_LEGACY_WEB_TOKEN_TTL", 86400),
    "cookie_max_age_seconds": _int("OOPZ_LEGACY_WEB_COOKIE_MAX_AGE", 86400),
    "cookie_secure": _bool("OOPZ_LEGACY_WEB_COOKIE_SECURE", False),
    "send_link_enabled": _bool("OOPZ_LEGACY_WEB_SEND_LINK", False),
    "link_idle_release_seconds": _int("OOPZ_LEGACY_WEB_IDLE_RELEASE", 0),
    "admin_enabled": _bool("OOPZ_LEGACY_ADMIN_ENABLED", False),
    "admin_password": os.getenv("OOPZ_LEGACY_ADMIN_PASSWORD") or "",
    "admin_session_ttl_seconds": 43200,
    "admin_cookie_secure": _bool("OOPZ_LEGACY_ADMIN_COOKIE_SECURE", False),
}

ONEBOT_V11_CONFIG = {
    "enabled": _bool("ONEBOT_V11_ENABLED", False),
    "host": _text("ONEBOT_V11_HOST", "0.0.0.0"),
    "port": _int("ONEBOT_V11_PORT", 6700),
    "access_token": _text("ONEBOT_V11_ACCESS_TOKEN"),
    "secret": _text("ONEBOT_V11_SECRET"),
    "db_path": _text("ONEBOT_V11_DB_PATH", "/app/data/legacy/onebot_v11.sqlite3"),
    "enable_http": True,
    "enable_ws": True,
    "enable_http_post": False,
    "http_post_urls": [],
    "http_post_timeout": 5.0,
    "enable_ws_reverse": False,
    "ws_reverse_url": "",
    "ws_reverse_api_url": "",
    "ws_reverse_event_url": "",
    "ws_reverse_reconnect_interval": 3.0,
    "send_connect_event": True,
    "heartbeat_enabled": True,
    "heartbeat_interval": 15.0,
    "member_list_max": 5000,
    "enable_area_scoped_group_ban": False,
    "enable_set_group_kick_as_area_kick": False,
    "enable_set_group_leave_as_area_leave": False,
    "enable_set_group_admin_as_area_role": False,
    "group_admin_role_id": _text("ONEBOT_V11_GROUP_ADMIN_ROLE_ID"),
}

AUTO_RECALL_CONFIG = {
    "enabled": _bool("OOPZ_AUTO_RECALL_ENABLED", False),
    "delay": _int("OOPZ_AUTO_RECALL_DELAY", 30),
    "max_pending": _int("OOPZ_AUTO_RECALL_MAX_PENDING", 1000),
    "exclude_commands": ["ai_chat", "ai_image"],
}

AREA_JOIN_NOTIFY = {
    "enabled": _bool("OOPZ_AREA_JOIN_NOTIFY_ENABLED", False),
    "event_source": _text("OOPZ_AREA_JOIN_EVENT_SOURCE", "operate_logs"),
    "message_template": _text("OOPZ_AREA_JOIN_MESSAGE", "欢迎 {name} 加入！"),
    "message_template_leave": _text("OOPZ_AREA_LEAVE_MESSAGE", "{name} 已离开。"),
    "poll_interval_seconds": _int("OOPZ_AREA_JOIN_POLL_SECONDS", 2),
    "auto_assign_role_id": _text("OOPZ_AREA_JOIN_ROLE_ID"),
    "auto_assign_role_name": _text("OOPZ_AREA_JOIN_ROLE_NAME"),
    "member_fetch_max": 5000,
}

CHAT_CONFIG = {
    "enabled": _bool("OOPZ_CHAT_ENABLED", True),
    "keyword_replies": {"你好": "你好，我是 OOPZ Bot。", "帮助": "发送 /help 查看命令。", "ping": "pong!"},
}
SCHEDULER_CONFIG = {"enabled": _bool("OOPZ_SCHEDULER_ENABLED", True), "check_interval_seconds": 30}
REMINDER_CONFIG = {
    "enabled": _bool("OOPZ_REMINDER_ENABLED", True),
    "max_per_user": 5,
    "max_delay_hours": 72,
    "check_interval_seconds": 15,
}
MESSAGE_STATS_CONFIG = {"enabled": _bool("OOPZ_MESSAGE_STATS_ENABLED", True)}
MUSIC_CONFIG = {"auto_play_enabled": True, "default_volume": _int("OOPZ_DEFAULT_VOLUME", 50)}
COMMAND_COOLDOWN_CONFIG = {
    "enabled": _bool("OOPZ_COMMAND_COOLDOWN_ENABLED", False),
    "default_seconds": _int("OOPZ_COMMAND_COOLDOWN_SECONDS", 3),
    "exempt_admins": True,
}

AREA_CONFIGS: list[dict] = []
ADMIN_UIDS = set(_csv("OOPZ_ADMIN_UIDS"))
NAME_MAP = {"users": {}, "channels": {}, "areas": {}}
