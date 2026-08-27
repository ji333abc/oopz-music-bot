"""Runtime adapter that embeds the original OOPZ bot core in this service."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _resolve_legacy_root() -> Path:
    """Locate the copied legacy tree in both a checkout and an installed image."""
    configured = str(os.getenv("OOPZ_LEGACY_SOURCE_ROOT") or "").strip()
    candidates = [
        Path(configured) if configured else None,
        _PROJECT_ROOT / "legacy_oopzbot",
        Path("/app/legacy_oopzbot"),
        Path.cwd() / "legacy_oopzbot",
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "src").is_dir():
            return candidate
    return _PROJECT_ROOT / "legacy_oopzbot"


_LEGACY_ROOT = _resolve_legacy_root()
_LEGACY_SRC = _LEGACY_ROOT / "src"


def _install_legacy_import_paths() -> None:
    for path in (str(_LEGACY_SRC), str(_LEGACY_ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)


class LegacyMusicAdapter:
    """Expose the legacy MusicGateway through the interface used by the bridge."""

    def __init__(self, music: Any, runtime: LegacyOopzCore):
        self._music = music
        self.runtime = runtime

    def __getattr__(self, name: str) -> Any:
        return getattr(self._music, name)


class LegacyOopzCore:
    def __init__(self) -> None:
        self.context: Any = None
        self.music: LegacyMusicAdapter | None = None
        self.bot: Any = None
        self._netease_runtime: Any = None
        self._shutdown: Any = None
        self._closed = threading.Event()

    @property
    def ready(self) -> bool:
        if self.context is None or self._closed.is_set():
            return False
        thread = getattr(self.context.client, "_thread", None)
        return bool(thread and thread.is_alive())

    def start(self) -> LegacyMusicAdapter:
        if self.music is not None:
            return self.music
        if not _LEGACY_SRC.is_dir():
            raise RuntimeError(f"旧版 OOPZ 核心不存在: {_LEGACY_SRC}")

        _install_legacy_import_paths()
        data_dir = Path(
            os.getenv("OOPZ_LEGACY_DATA_DIR") or _PROJECT_ROOT / "data" / "legacy"
        )
        data_dir.mkdir(parents=True, exist_ok=True)
        data_dir.chmod(0o700)
        for credential_file in (
            data_dir / "oopz_credentials.json",
            data_dir / "private_key.pem",
        ):
            if credential_file.is_file():
                credential_file.chmod(0o600)

        import config as legacy_config
        from app.lifecycle import (
            AppContextBuilder,
            BackgroundServiceRunner,
            NeteaseApiRuntime,
            ShutdownCoordinator,
            StartupResourceBuilder,
            VoiceRuntimeBuilder,
        )
        from app.runtime import apply_runtime_overrides
        from oopz.oopz_password_login import (
            OopzPasswordLoginError,
            refresh_credentials_from_config_password,
        )

        apply_runtime_overrides()
        self._netease_runtime = NeteaseApiRuntime()
        self._netease_runtime.start()

        try:
            credentials = refresh_credentials_from_config_password(save=True)
        except OopzPasswordLoginError as exc:
            logger.warning("旧版 OOPZ 账号密码刷新失败，尝试使用已有凭据: %s", exc)
        except Exception:
            logger.warning("旧版 OOPZ 账号密码刷新异常，尝试使用已有凭据", exc_info=True)
        else:
            if credentials:
                logger.info("旧版 OOPZ 核心已刷新并持久化登录凭据")

        if not str(legacy_config.OOPZ_CONFIG.get("agora_app_id") or "").strip():
            raise RuntimeError(
                "启用旧版 OOPZ 核心必须配置 OOPZ_AGORA_APP_ID；"
                "请从旧 config.py 的 OOPZ_CONFIG.agora_app_id 迁移"
            )

        resources = StartupResourceBuilder().build()
        voice = VoiceRuntimeBuilder().build()
        if voice is None:
            raise RuntimeError("旧版 OOPZ Agora 语音客户端初始化失败")

        self.context = AppContextBuilder().build(resources.sender, voice=voice)
        BackgroundServiceRunner().start(self.context)
        self.context.client.start_async()
        self._shutdown = ShutdownCoordinator()

        gateway = self.context.handler.infrastructure.music
        self.music = LegacyMusicAdapter(gateway, self)
        self.bot = SimpleNamespace(
            config=SimpleNamespace(
                person_uid=str(legacy_config.OOPZ_CONFIG.get("person_uid") or "")
            )
        )
        logger.info("旧版 OOPZ 核心已启动：消息、语音、Redis 队列和命令系统已接管")
        return self.music

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if self.context is not None:
            try:
                self.context.client.stop()
            except Exception:
                logger.warning("停止旧版 OOPZ WebSocket 失败", exc_info=True)
        if self._shutdown is not None and self._netease_runtime is not None:
            self._shutdown.stop(self.context, self._netease_runtime)
