"""Bounded component-health aggregation shared by readiness and the Panel."""

from __future__ import annotations

import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

from .config import get_settings
from .observability import redact_secrets
from .operations import operations


def health_entry(status: str, reason: str) -> dict[str, str]:
    safe_reason = redact_secrets(
        " ".join(str(reason or "未知原因").split()),
        max_length=240,
    )
    return {
        "status": status,
        "reason": safe_reason,
        "message": safe_reason,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def runtime_status(runtime) -> tuple[str, str]:
    if runtime is None:
        return "starting", "OOPZ 运行时尚未初始化"
    status_method = getattr(runtime, "status", None)
    if callable(status_method):
        state = status_method()
        return str(state.status), state.reason or "OOPZ 运行时已就绪"
    if getattr(runtime, "_closed", None) is not None and runtime._closed.is_set():
        return "offline", "OOPZ 运行时已停止"
    startup_error = getattr(runtime, "_startup_error", None)
    if startup_error is not None:
        return "error", f"OOPZ 初始化失败：{type(startup_error).__name__}"
    if bool(getattr(runtime, "ready", False)):
        return "ok", "OOPZ 运行时已就绪"
    return "starting", "等待 OOPZ 运行时就绪"


def websocket_status(runtime) -> tuple[str, str]:
    if runtime is None:
        return "starting", "OOPZ WebSocket 尚未初始化"
    component_status = getattr(runtime, "component_status", None)
    if callable(component_status):
        state = component_status("websocket")
        return str(state.status), state.reason or "OOPZ WebSocket 已连接并通过认证"
    context = getattr(runtime, "context", None)
    if context is not None:
        client = getattr(context, "client", None)
        if bool(getattr(client, "authenticated", False)):
            return "ok", "OOPZ WebSocket 已连接并通过认证"
        if bool(getattr(client, "connected", False)):
            return "starting", "OOPZ WebSocket 已连接，等待认证"
        if getattr(runtime, "_closed", None) is not None and runtime._closed.is_set():
            return "offline", "OOPZ WebSocket 已断开"
        thread = getattr(client, "_thread", None)
        if thread is not None and thread.is_alive():
            return "degraded", "OOPZ WebSocket 已断开，正在重连"
        return "degraded", "OOPZ WebSocket 未连接"
    status, reason = runtime_status(runtime)
    return status, "OOPZ WebSocket " + reason.removeprefix("OOPZ ")


def voice_status(music, runtime) -> tuple[str, str]:
    if runtime is None:
        return "starting", "OOPZ 语音运行时尚未初始化"
    component_status = getattr(runtime, "component_status", None)
    if callable(component_status):
        state = component_status("voice")
        return str(state.status), state.reason or "OOPZ 语音频道已加入"
    if not bool(getattr(runtime, "ready", False)):
        return "unknown", "尚未建立 OOPZ 语音会话"
    if getattr(music, "_voice_channel_id", None):
        return "ok", "OOPZ 语音频道已加入"
    if bool(getattr(runtime, "_voice_started", False)):
        return "ok", "OOPZ 语音客户端已启动"
    return "unknown", "尚未加入语音频道"


def redis_status(music) -> tuple[str, str]:
    queue = getattr(music, "queue", None)
    try:
        client = getattr(queue, "redis", None)
    except Exception as exc:
        return "degraded", f"Redis 探测失败：{type(exc).__name__}"
    if client is None:
        client = getattr(queue, "_redis", None)
    if client is None:
        try:
            from core import queue_manager

            client = getattr(queue_manager, "_redis_client", None)
        except (ImportError, AttributeError):
            client = None
    if client is None:
        return "unknown", "Redis 状态尚未探测"
    if client.__class__.__name__ == "_InMemoryRedis":
        return "degraded", "Redis 不可用，当前使用内存队列"
    return "ok", "Redis 已连接"


def _env(name: str) -> str:
    return str(os.getenv(name) or "").strip()


def service_health(music) -> dict[str, dict]:
    """Return one bounded component snapshot for readiness and the Panel."""

    settings = get_settings()
    stored = operations.snapshot().get("components", {})
    runtime = getattr(music, "runtime", None) if music is not None else None
    legacy_status, legacy_reason = runtime_status(runtime)
    websocket_state, websocket_reason = websocket_status(runtime)
    voice_state, voice_reason = voice_status(music, runtime)
    stored_bot = stored.get("qq_bot") or {}
    bot_status = str(stored_bot.get("status") or "starting")
    if bot_status in {"online", "disabled"}:
        bot_status = {"online": "ok", "disabled": "offline"}[bot_status]
    if bot_status not in {"starting", "ok", "degraded", "error", "offline", "unknown"}:
        bot_status = "unknown"

    components: dict[str, dict] = {
        "internal_api": health_entry("ok", "内部控制接口正常"),
        "legacy_core": health_entry(legacy_status, legacy_reason),
        "oopz_websocket": health_entry(websocket_state, websocket_reason),
        "oopz_voice": health_entry(voice_state, voice_reason),
        "redis": (
            health_entry(*redis_status(music))
            if music is not None
            else health_entry("unknown", "Redis 状态尚未探测")
        ),
        "qqmusic": health_entry("offline", "QQ 音乐功能未启用"),
        "qq_bot": health_entry(
            bot_status,
            str(stored_bot.get("reason") or stored_bot.get("message") or "等待 QQ 网关上线"),
        ),
    }

    if settings.qq_music_enabled:
        try:
            response = requests.get(
                f"{settings.qq_music_base_url.rstrip('/')}/explorer/metadata",
                timeout=3,
            )
            response.raise_for_status()
            components["qqmusic"] = health_entry("ok", "QQ 音乐接口可访问")
        except Exception as exc:
            components["qqmusic"] = health_entry(
                "error",
                f"QQ 音乐接口异常：{type(exc).__name__}",
            )
        from .qqmusic_credential import credential_status

        credential = credential_status()
        if not credential.get("has_credential"):
            components["qqmusic_credential"] = health_entry(
                "offline", "未配置扫码凭证，使用手动 Cookie"
            )
        elif credential.get("state") == "expired":
            components["qqmusic_credential"] = health_entry(
                "degraded", "QQ 音乐凭证已过期，请重新扫码登录"
            )
        else:
            remaining = max(0, float(credential.get("expires_at") or 0) - time.time())
            days = max(0, int(remaining // 86400))
            components["qqmusic_credential"] = health_entry(
                "ok", f"QQ 音乐凭证有效，剩余约 {days} 天"
            )
    else:
        components["qqmusic_credential"] = health_entry("offline", "QQ 音乐功能未启用")

    jm_enabled = _env("QQBOT_JM_ENABLED").lower() in {"1", "true", "yes", "on"}
    if not jm_enabled:
        components["uploader"] = health_entry("offline", "JM 未启用")
    else:
        project_root = Path(__file__).resolve().parents[1]
        uploader = Path(
            _env("QQBOT_JM_UPLOADER")
            or project_root / "tools" / "qqbot-uploader" / "uploader.mjs"
        )
        node = _env("QQBOT_JM_NODE") or "node"
        sdk = uploader.parent / "node_modules" / "@tencent-connect" / "qqbot-nodejs"
        ready = (
            uploader.is_file()
            and (Path(node).is_file() or shutil.which(node))
            and sdk.is_dir()
        )
        components["uploader"] = health_entry(
            "ok" if ready else "error",
            "QQ 分片上传器就绪" if ready else "上传器或依赖缺失",
        )
    return components
