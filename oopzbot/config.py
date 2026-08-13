"""Environment-based application configuration."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


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
            log_level=_text("LOG_LEVEL", "INFO").upper(),
        )

    @property
    def bridge_url(self) -> str:
        return f"http://{self.bridge_host}:{self.bridge_port}/internal/qqbot/command"

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
        if self.bridge_host not in {"127.0.0.1", "localhost", "::1"}:
            errors.append("OOPZBOT_BRIDGE_HOST 必须是回环地址，禁止公开内部桥接接口")
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
        return current
    token = secrets.token_urlsafe(32)
    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    line = f"QQBOT_BRIDGE_TOKEN={token}"
    if "QQBOT_BRIDGE_TOKEN=" in content:
        content = content.replace("QQBOT_BRIDGE_TOKEN=", line, 1)
    else:
        content = content.rstrip() + f"\n{line}\n"
    env_path.write_text(content, encoding="utf-8", newline="\n")
    os.environ["QQBOT_BRIDGE_TOKEN"] = token
    clear_settings_cache()
    return token
