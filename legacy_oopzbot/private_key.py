"""Load the legacy OOPZ signing key from environment or persistent storage."""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization


def _private_key_pem() -> bytes:
    inline = str(os.getenv("OOPZ_PRIVATE_KEY") or "").strip()
    if inline:
        return inline.replace("\\n", "\n").encode("utf-8")

    configured = str(os.getenv("OOPZ_PRIVATE_KEY_FILE") or "").strip()
    path = Path(configured) if configured else Path(
        os.getenv("OOPZ_LEGACY_DATA_DIR") or "/app/data/legacy"
    ) / "private_key.pem"
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            "未找到 OOPZ RSA 私钥；请配置账号密码自动登录，或设置 "
            "OOPZ_PRIVATE_KEY_FILE"
        ) from exc


def get_private_key():
    return serialization.load_pem_private_key(
        _private_key_pem(),
        password=None,
        backend=default_backend(),
    )
