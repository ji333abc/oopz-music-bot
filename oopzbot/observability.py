"""Command correlation and safe logging helpers."""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import re
import secrets
from collections.abc import Iterator

_COMMAND_ID = contextvars.ContextVar("oopzbot_command_id", default="")
COMMAND_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")

SECRET_ENV_NAMES = (
    "QQBOT_APP_SECRET",
    "QQBOT_BRIDGE_TOKEN",
    "OOPZ_LOGIN_PASSWORD",
    "OOPZ_PASSWORD",
    "OOPZ_JWT_TOKEN",
    "QQ_MUSIC_COOKIE",
    "JM_ZIP_PASSWORD",
    "QQBOT_JM_ZIP_PASSWORD",
    "RACKNERD_API_KEY",
    "RACKNERD_API_HASH",
    "OOPZ_RSA_PRIVATE_KEY",
    "OOPZ_PRIVATE_KEY",
    "OOPZ_LEGACY_ADMIN_PASSWORD",
    "BOT_REDIS_PASSWORD",
    "ONEBOT_V11_ACCESS_TOKEN",
    "ONEBOT_V11_SECRET",
    "NETEASE_MUSIC_COOKIE",
    "BILIBILI_MUSIC_COOKIE",
    "DOUBAO_API_KEY",
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(qqbot_app_secret|qqbot_bridge_token|oopz(?:_login)?_password|"
    r"oopz_jwt_token|qq_music_cookie|(?:qqbot_)?jm_zip_password|"
    r"oopz_legacy_admin_password|bot_redis_password|onebot_v11_(?:access_token|secret)|"
    r"(?:netease|bilibili)_music_cookie|doubao_api_key|"
    r"racknerd_api_(?:key|hash))\s*[:=]\s*([^\s,;]+)"
)
_AUTHORIZATION = re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+")
_COOKIE = re.compile(r"(?i)(cookie\s*[:=]\s*)[^\r\n]+")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)


def new_command_id() -> str:
    return secrets.token_hex(8)


def is_valid_command_id(value: object) -> bool:
    return bool(COMMAND_ID_PATTERN.fullmatch(str(value or "").strip()))


def ensure_command_id(value: object = None) -> str:
    candidate = str(value or "").strip()
    return candidate if is_valid_command_id(candidate) else new_command_id()


def current_command_id() -> str:
    return _COMMAND_ID.get() or "-"


@contextlib.contextmanager
def command_context(command_id: object = None) -> Iterator[str]:
    value = ensure_command_id(command_id)
    token = _COMMAND_ID.set(value)
    try:
        yield value
    finally:
        _COMMAND_ID.reset(token)


def _configured_secrets(extra: Iterator[object] | None = None) -> list[str]:
    values = [os.getenv(name, "") for name in SECRET_ENV_NAMES]
    if extra is not None:
        values.extend(str(value or "") for value in extra)
    return sorted(
        {value for value in values if len(value) >= 3},
        key=len,
        reverse=True,
    )


def redact_secrets(value: object, *, max_length: int = 500, extra_secrets=()) -> str:
    """Remove configured credentials and unsafe formatting from log text."""
    text = str(value or "")
    text = "".join(character if character.isprintable() else " " for character in text)
    for secret in _configured_secrets(iter(extra_secrets)):
        text = text.replace(secret, "***")
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=***", text)
    text = _AUTHORIZATION.sub(r"\1***", text)
    text = _COOKIE.sub(r"\1***", text)
    text = _PRIVATE_KEY.sub("***PRIVATE_KEY_REDACTED***", text)
    return text[:max(1, int(max_length))]


class RedactionFilter(logging.Filter):
    """Keep records from older handlers safe, including the legacy file logger."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.command_id = getattr(record, "command_id", None) or current_command_id()
        try:
            record.msg = redact_secrets(record.getMessage())
            record.args = ()
        except Exception:
            record.msg = "日志内容无法安全格式化"
            record.args = ()
        return True


_original_record_factory = logging.getLogRecordFactory()
_record_factory_installed = False


def install_log_record_factory() -> None:
    global _record_factory_installed
    if _record_factory_installed:
        return

    def record_factory(*args, **kwargs):
        record = _original_record_factory(*args, **kwargs)
        record.command_id = current_command_id()
        try:
            record.msg = redact_secrets(record.getMessage())
            record.args = ()
        except Exception:
            record.msg = "日志内容无法安全格式化"
            record.args = ()
        return record

    logging.setLogRecordFactory(record_factory)
    _record_factory_installed = True
