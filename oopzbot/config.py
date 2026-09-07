"""Environment-based application configuration."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


def _text(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _integer(name: str, default: int) -> int:
    raw = _text(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数，当前值为 {raw!r}") from exc


def _boolean(name: str, default: bool = False) -> bool:
    raw = _text(name, "true" if default else "false").lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true 或 false，当前值为 {raw!r}")


def _bounded_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = _text(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s 不是整数，回退到 %d", name, default)
        return default
    if not minimum <= value <= maximum:
        logger.warning(
            "%s 超出允许范围 %d-%d，回退到 %d",
            name,
            minimum,
            maximum,
            default,
        )
        return default
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    qqbot_app_id: str
    qqbot_app_secret: str
    bridge_token: str
    bridge_host: str
    bridge_port: int

    oopz_area_id: str
    oopz_text_channel_id: str
    oopz_voice_channel_id: str
    oopz_person_uid: str

    qq_music_enabled: bool
    qq_music_managed: bool
    qq_music_base_url: str
    qq_music_service_dir: str
    qq_music_cookie: str
    qq_music_quality: str
    qq_music_fallback_quality: str

    log_level: str
    bridge_private_network: bool = False
    qq_music_credential_file: str = "data/qqmusic-credential.json"
    qq_music_auto_refresh: bool = True
    qq_music_refresh_min_hours: int = 6
    qq_music_refresh_max_hours: int = 24
    qq_music_cookie_api_url: str = ""
    search_cache_enabled: bool = True
    search_cache_ttl_seconds: int = 60
    search_cache_max_entries: int = 256
    search_negative_cache_ttl_seconds: int = 10
    metrics_window_size: int = 200
    playback_history_limit: int = 50
    failure_history_limit: int = 100
    command_history_limit: int = 200
    panel_sse_enabled: bool = True
    panel_sse_heartbeat_seconds: int = 20
    album_request_enabled: bool = False
    album_request_max_tracks: int = 30
    album_request_session_ttl_seconds: int = 300

    @classmethod
    def from_env(cls) -> Settings:
        music_base_url = _text("QQ_MUSIC_BASE_URL", "http://127.0.0.1:3200")
        managed_default = music_base_url.rstrip("/") == "http://127.0.0.1:3200"
        return cls(
            qqbot_app_id=_text("QQBOT_APP_ID"),
            qqbot_app_secret=_text("QQBOT_APP_SECRET"),
            bridge_token=_text("QQBOT_BRIDGE_TOKEN"),
            bridge_host=_text("OOPZBOT_BRIDGE_HOST", "127.0.0.1"),
            bridge_port=_integer("OOPZBOT_BRIDGE_PORT", 18080),
            oopz_area_id=_text("QQBOT_OOPZ_AREA_ID"),
            oopz_text_channel_id=_text("QQBOT_OOPZ_TEXT_CHANNEL_ID"),
            oopz_voice_channel_id=_text("QQBOT_OOPZ_VOICE_CHANNEL_ID"),
            oopz_person_uid=_text("OOPZ_PERSON_UID"),
            qq_music_enabled=_boolean("QQ_MUSIC_ENABLED", True),
            qq_music_managed=_boolean("QQ_MUSIC_MANAGED", managed_default),
            qq_music_base_url=music_base_url,
            qq_music_service_dir=_text(
                "QQ_MUSIC_SERVICE_DIR",
                ".services/qqmusic-api",
            ),
            qq_music_cookie=_text("QQ_MUSIC_COOKIE"),
            qq_music_quality=_text("QQ_MUSIC_QUALITY", "320"),
            qq_music_fallback_quality=_text("QQ_MUSIC_FALLBACK_QUALITY", "128"),
            qq_music_credential_file=_text(
                "QQ_MUSIC_CREDENTIAL_FILE",
                "data/qqmusic-credential.json",
            ),
            qq_music_auto_refresh=_boolean("QQ_MUSIC_AUTO_REFRESH", True),
            qq_music_refresh_min_hours=_integer("QQ_MUSIC_REFRESH_MIN_HOURS", 6),
            qq_music_refresh_max_hours=_integer("QQ_MUSIC_REFRESH_MAX_HOURS", 24),
            qq_music_cookie_api_url=_text("QQ_MUSIC_COOKIE_API_URL"),
            search_cache_enabled=_boolean("OOPZ_SEARCH_CACHE_ENABLED", True),
            search_cache_ttl_seconds=_bounded_integer(
                "OOPZ_SEARCH_CACHE_TTL_SECONDS", 60, 5, 600
            ),
            search_cache_max_entries=_bounded_integer(
                "OOPZ_SEARCH_CACHE_MAX_ENTRIES", 256, 16, 2048
            ),
            search_negative_cache_ttl_seconds=_bounded_integer(
                "OOPZ_SEARCH_NEGATIVE_CACHE_TTL_SECONDS", 10, 1, 60
            ),
            metrics_window_size=_bounded_integer(
                "OOPZ_METRICS_WINDOW_SIZE", 200, 10, 2000
            ),
            playback_history_limit=_bounded_integer(
                "OOPZ_PLAYBACK_HISTORY_LIMIT", 50, 10, 500
            ),
            failure_history_limit=_bounded_integer(
                "OOPZ_FAILURE_HISTORY_LIMIT", 100, 10, 1000
            ),
            command_history_limit=_bounded_integer(
                "OOPZ_COMMAND_HISTORY_LIMIT", 200, 10, 2000
            ),
            panel_sse_enabled=_boolean("OOPZ_PANEL_SSE_ENABLED", True),
            panel_sse_heartbeat_seconds=_bounded_integer(
                "OOPZ_PANEL_SSE_HEARTBEAT_SECONDS", 20, 15, 25
            ),
            album_request_enabled=_boolean("OOPZ_ALBUM_REQUEST_ENABLED", False),
            album_request_max_tracks=_bounded_integer(
                "OOPZ_ALBUM_REQUEST_MAX_TRACKS", 30, 1, 100
            ),
            album_request_session_ttl_seconds=_bounded_integer(
                "OOPZ_ALBUM_REQUEST_SESSION_TTL_SECONDS", 300, 60, 1800
            ),
            log_level=_text("LOG_LEVEL", "INFO").upper(),
            bridge_private_network=_boolean(
                "OOPZBOT_BRIDGE_PRIVATE_NETWORK",
                False,
            ),
        )

    @property
    def bridge_url(self) -> str:
        host = (
            "127.0.0.1"
            if self.bridge_host in {"0.0.0.0", "::"}
            else self.bridge_host
        )
        return f"http://{host}:{self.bridge_port}/internal/qqbot/command"

    def validate(self) -> list[str]:
        errors: list[str] = []
        required = {
            "QQBOT_APP_ID": self.qqbot_app_id,
            "QQBOT_APP_SECRET": self.qqbot_app_secret,
            "QQBOT_BRIDGE_TOKEN": self.bridge_token,
            "QQBOT_OOPZ_AREA_ID": self.oopz_area_id,
            "QQBOT_OOPZ_TEXT_CHANNEL_ID": self.oopz_text_channel_id,
            "QQBOT_OOPZ_VOICE_CHANNEL_ID": self.oopz_voice_channel_id,
        }
        errors.extend(f"缺少 {name}" for name, value in required.items() if not value)

        has_credentials = all(
            _text(name)
            for name in ("OOPZ_DEVICE_ID", "OOPZ_PERSON_UID", "OOPZ_JWT_TOKEN")
        )
        has_password = bool(_text("OOPZ_LOGIN_PHONE") and os.getenv("OOPZ_LOGIN_PASSWORD"))
        if not has_credentials and not has_password:
            errors.append(
                "OOPZ 登录未配置：填写 DEVICE_ID/PERSON_UID/JWT_TOKEN，"
                "或 LOGIN_PHONE/LOGIN_PASSWORD"
            )
        if _boolean("OOPZBOT_USE_LEGACY_CORE", False) and not _text(
            "OOPZ_AGORA_APP_ID"
        ):
            errors.append(
                "启用旧版 OOPZ 核心时必须配置 OOPZ_AGORA_APP_ID"
            )
        loopback_hosts = {"127.0.0.1", "localhost", "::1"}
        docker_bind_hosts = {"0.0.0.0", "::"}
        if self.bridge_host not in loopback_hosts:
            if not (
                self.bridge_private_network
                and self.bridge_host in docker_bind_hosts
            ):
                errors.append(
                    "OOPZBOT_BRIDGE_HOST 必须是回环地址；仅 Docker 内网模式可绑定通配地址"
                )
        if not 1 <= self.bridge_port <= 65535:
            errors.append("OOPZBOT_BRIDGE_PORT 必须在 1-65535 之间")
        if self.qq_music_enabled and not self.qq_music_base_url:
            errors.append("启用 QQ 音乐时必须配置 QQ_MUSIC_BASE_URL")
        if self.qq_music_enabled and self.qq_music_managed:
            from .qqmusic_service import managed_url_error

            if error := managed_url_error(self.qq_music_base_url):
                errors.append(error)
            if not self.qq_music_service_dir:
                errors.append("托管 QQ 音乐 API 时必须配置 QQ_MUSIC_SERVICE_DIR")
        if self.qq_music_quality not in {"m4a", "128", "320", "ape", "flac"}:
            errors.append("QQ_MUSIC_QUALITY 不是支持的音质")
        if self.qq_music_refresh_min_hours < 1 or self.qq_music_refresh_max_hours < 1:
            errors.append("QQ_MUSIC_REFRESH_MIN/MAX_HOURS 必须 >= 1")
        elif self.qq_music_refresh_min_hours > self.qq_music_refresh_max_hours:
            errors.append("QQ_MUSIC_REFRESH_MIN_HOURS 不能大于 MAX_HOURS")
        return errors


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def ensure_bridge_token(env_path: Path) -> str:
    """Append a generated bridge token when a local env file omitted it."""
    current = _text("QQBOT_BRIDGE_TOKEN")
    if current:
        if env_path.exists():
            env_path.chmod(0o600)
        return current
    token = secrets.token_urlsafe(32)
    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    line = f"QQBOT_BRIDGE_TOKEN={token}"
    if "QQBOT_BRIDGE_TOKEN=" in content:
        content = content.replace("QQBOT_BRIDGE_TOKEN=", line, 1)
    else:
        content = content.rstrip() + f"\n{line}\n"
    env_path.write_text(content, encoding="utf-8", newline="\n")
    env_path.chmod(0o600)
    os.environ["QQBOT_BRIDGE_TOKEN"] = token
    clear_settings_cache()
    return token
