"""Runtime adapter that embeds the original OOPZ bot core in this service."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .application.playback_monitor_service import PlaybackMonitorService
from .domain.compat import playback_state_from_legacy
from .domain.contracts import (
    CommandError,
    ComponentState,
    ComponentStatus,
    ErrorKind,
    OperationResult,
    PlaybackState,
)

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
        return bool(getattr(self.context.client, "authenticated", False))

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
        BackgroundServiceRunner().start(self.context, start_music_monitor=False)
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


class LegacyOopzRuntimeAdapter:
    """OopzRuntimePort facade over the stable embedded legacy implementation.

    Application services receive structured results and never inspect the
    legacy Context, WebSocket, Agora client, or worker threads.
    """

    implementation_name = "legacy-oopz-runtime-adapter"

    def __init__(self, core: LegacyOopzCore | None = None) -> None:
        self._core = core or LegacyOopzCore()
        self.music: LegacyMusicAdapter | None = None
        self._playback_monitor: PlaybackMonitorService | None = None
        self.command_implementation = "legacy-music-command-fallback"
        self.playback_monitor_implementation = "playback-monitor-service"

    @property
    def ready(self) -> bool:
        return self._core.ready

    @property
    def bot(self) -> Any:
        """Preserve the legacy bot identity used by command configuration.

        Password-login deployments may obtain ``person_uid`` from the
        refreshed legacy credentials instead of defining ``OOPZ_PERSON_UID``
        in ``.env``.  Keeping this compatibility property prevents the
        runtime facade from hiding that identity.
        """

        return self._core.bot

    def start(self) -> OperationResult:
        if self.music is not None:
            if not self._core._closed.is_set():
                return OperationResult(
                    ok=True,
                    message=(
                        "OOPZ 运行时已启动"
                        if self.ready
                        else "OOPZ 运行时已启动，等待 WebSocket 认证"
                    ),
                )
            return self._failure(
                "OOPZ 运行时已停止或失去认证，必须显式重建 Adapter",
                stage="startup",
            )
        try:
            self.music = self._core.start()
            # Health and application code see the facade, not the legacy
            # Context object.  The wrapped gateway keeps its own internals.
            self.music.runtime = self
            self._playback_monitor = PlaybackMonitorService(self.music)
            self._playback_monitor.start()
        except Exception as exc:
            logger.exception("旧版 OOPZ Runtime Adapter 启动失败")
            return self._failure("启动 OOPZ 运行时失败", exc, "startup")
        logger.info("OOPZ runtime implementation=%s", self.implementation_name)
        return OperationResult(ok=True, message="OOPZ 运行时已启动")

    def close(self) -> None:
        if self._playback_monitor is not None:
            self._playback_monitor.stop()
        self._core.close()

    def bind_music_command_handler(self, handler) -> None:
        """Route OOPZ music mentions into the modern command boundary."""

        context = self._core.context
        if context is None:
            raise RuntimeError("OOPZ 运行时尚未启动")
        context.handler.bind_external_music_command(handler)
        self.command_implementation = "shared-command-service"
        logger.info("OOPZ 音乐命令已接入现代 CommandService")

    def status(self) -> ComponentState:
        if self.ready:
            return ComponentState(
                "oopz_runtime",
                ComponentStatus.OK,
                f"implementation={self.implementation_name}",
            )
        if self.music is not None:
            return ComponentState(
                "oopz_runtime",
                ComponentStatus.DEGRADED,
                "OOPZ WebSocket 尚未认证或连接已断开",
            )
        return ComponentState(
            "oopz_runtime",
            ComponentStatus.OFFLINE,
            "OOPZ 运行时尚未启动",
        )

    def component_status(self, component: str) -> ComponentState:
        """Expose bounded adapter-owned status without leaking Context."""

        if component == "websocket":
            context = self._core.context
            client = getattr(context, "client", None) if context is not None else None
            if bool(getattr(client, "authenticated", False)):
                return ComponentState("oopz_websocket", ComponentStatus.OK)
            if bool(getattr(client, "connected", False)):
                return ComponentState(
                    "oopz_websocket",
                    ComponentStatus.STARTING,
                    "OOPZ WebSocket 已连接，等待认证",
                )
            status = ComponentStatus.OFFLINE if self._core._closed.is_set() else ComponentStatus.DEGRADED
            return ComponentState("oopz_websocket", status, "OOPZ WebSocket 未连接")
        if component == "voice":
            music = self.music
            if music is not None and getattr(music, "_voice_channel_id", None):
                return ComponentState("oopz_voice", ComponentStatus.OK)
            if not self.ready:
                return ComponentState(
                    "oopz_voice",
                    ComponentStatus.UNKNOWN,
                    "尚未建立 OOPZ 语音会话",
                )
            return ComponentState(
                "oopz_voice",
                ComponentStatus.UNKNOWN,
                "尚未加入语音频道",
            )
        return self.status()

    def send_text(self, text: str, *, channel: str, area: str) -> OperationResult:
        try:
            music = self._require_music()
            ok = bool(music.notify_message(text=text, channel=channel, area=area))
        except Exception as exc:
            return self._failure("OOPZ 文字消息发送失败", exc, "notification")
        if not ok:
            return self._failure("OOPZ 文字消息发送失败", stage="notification")
        return OperationResult(ok=True)

    def enter_voice(self, *, area: str, channel: str) -> OperationResult:
        try:
            music = self._require_music()
            result = music.enter_voice_channel(channel, area)
        except Exception as exc:
            return self._failure("进入 OOPZ 语音频道失败", exc, "joining")
        if not isinstance(result, dict) or result.get("error"):
            detail = result.get("error") if isinstance(result, dict) else "unknown"
            return self._failure(f"进入 OOPZ 语音频道失败: {detail}", stage="joining")
        return OperationResult(ok=True)

    def play(self, url: str, *, area: str, channel: str) -> OperationResult:
        del area, channel
        if not url:
            return self._failure("播放地址为空", kind=ErrorKind.NOT_FOUND, stage="resolving")
        try:
            music = self._require_music()
            voice = getattr(music, "voice", None)
            if not voice or not voice.available:
                return self._failure("OOPZ 语音服务不可用", stage="playing")
            voice.play_audio(url)
        except Exception as exc:
            return self._failure("OOPZ 音频播放失败", exc, "playing")
        return OperationResult(ok=True)

    def pause(self) -> OperationResult:
        return self._voice_boolean("pause_audio", "暂停播放失败")

    def resume(self) -> OperationResult:
        return self._voice_boolean("resume_audio", "继续播放失败")

    def stop(self) -> OperationResult:
        try:
            music = self._require_music()
            voice = getattr(music, "voice", None)
            if voice and voice.available:
                voice.stop_audio()
        except Exception as exc:
            return self._failure("停止播放失败", exc, "stopping")
        return OperationResult(ok=True)

    def current_state(self) -> PlaybackState:
        if self.music is None:
            return PlaybackState()
        music = self.music
        area = str(getattr(music, "_voice_channel_area", "") or "")
        if not area:
            return PlaybackState()
        queue = music._get_queue(area)
        current = queue.get_current()
        song_id = str((current or {}).get("song_id") or (current or {}).get("id") or "")
        return playback_state_from_legacy(
            queue.get_play_state() or {},
            current_song_id=song_id or None,
        ) or PlaybackState()

    def _require_music(self) -> LegacyMusicAdapter:
        if self.music is None:
            raise RuntimeError("OOPZ 运行时尚未启动")
        return self.music

    def _voice_boolean(self, method: str, message: str) -> OperationResult:
        try:
            music = self._require_music()
            voice = getattr(music, "voice", None)
            ok = bool(voice and getattr(voice, method)())
        except Exception as exc:
            return self._failure(message, exc, method)
        return OperationResult(ok=ok, message="" if ok else message)

    @staticmethod
    def _failure(
        message: str,
        exception: Exception | None = None,
        stage: str = "",
        *,
        kind: ErrorKind = ErrorKind.DEPENDENCY,
    ) -> OperationResult:
        detail = f": {exception}" if exception else ""
        full_message = f"{message}{detail}"
        return OperationResult(
            ok=False,
            message=full_message,
            error=CommandError(kind=kind, message=full_message, stage=stage),
        )
