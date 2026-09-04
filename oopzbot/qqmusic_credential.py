"""QQ 音乐登录凭证存储、Cookie 分发与自动续期。

凭证文件（默认 ``data/qqmusic-credential.json``）保存完整的
``qqmusic_api.Credential``（含 ``refresh_key``/``refresh_token``），
是 Cookie 的唯一事实源；刷新成功后派生出无敏感续期字段、可被旧核心
等轻量组件直接读取的 Cookie 状态文件。``QQ_MUSIC_COOKIE`` 环境变量
降级为手动兜底：有凭证文件时以文件为准。QQ Music API 的虚拟设备身份
保存在凭证文件同目录，所有登录与续期请求都必须复用它。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("QQMusicCredential")

DEFAULT_CREDENTIAL_FILE = "data/qqmusic-credential.json"
DEFAULT_DEVICE_FILE = "qqmusic-device.json"
DEFAULT_REFRESH_MIN_HOURS = 6
DEFAULT_REFRESH_MAX_HOURS = 24
# 刷新凭据彻底失效（需要重新扫码）后的重试间隔。
RELOGIN_RETRY_SECONDS = 24 * 3600
# 网络类失败的退避序列。
NETWORK_BACKOFF_SECONDS = (600, 1800, 7200)
NO_CREDENTIAL_POLL_SECONDS = 3600

_CREDENTIAL_CACHE_TIME = 15
_LOCK = threading.Lock()


def default_credential_file() -> Path:
    configured = os.getenv("QQ_MUSIC_CREDENTIAL_FILE", "").strip()
    return Path(configured) if configured else Path(DEFAULT_CREDENTIAL_FILE)


def cookie_state_path(credential_path: Path | None = None) -> Path:
    configured = os.getenv("QQ_MUSIC_COOKIE_STATE_FILE", "").strip()
    if configured:
        return Path(configured)
    path = Path(credential_path or default_credential_file())
    return path.parent / "qqmusic-cookie.json"


def device_state_path(credential_path: Path | None = None) -> Path:
    """Return the persistent QQ Music virtual-device file beside the credential."""
    path = Path(credential_path or default_credential_file())
    return path.parent / DEFAULT_DEVICE_FILE


@asynccontextmanager
async def open_qqmusic_client(
    qqmusic_api: Any,
    *,
    device_path: Path,
) -> AsyncIterator[Any]:
    """Open a client that consistently reuses one persisted virtual device."""
    path = Path(device_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with qqmusic_api.Client(device_path=str(path)) as client:
            yield client
    finally:
        if path.is_file():
            try:
                path.chmod(0o600)
            except OSError:
                pass


def cookie_string(credential: dict[str, Any]) -> str:
    musicid = str(credential.get("musicid") or "").strip()
    musickey = str(credential.get("musickey") or "").strip()
    return f"uin={musicid}; qm_keyst={musickey}; qqmusic_key={musickey}"


def extract_uin(credential: dict[str, Any]) -> str:
    return str(credential.get("musicid") or "").strip()


def expiry_timestamp(credential: dict[str, Any]) -> float:
    created = float(credential.get("musickey_create_time") or 0)
    expires_in = float(credential.get("key_expires_in") or 0)
    if created <= 0 or expires_in <= 0:
        return 0.0
    return created + expires_in


def compute_refresh_interval(
    credential: dict[str, Any],
    *,
    min_hours: float = DEFAULT_REFRESH_MIN_HOURS,
    max_hours: float = DEFAULT_REFRESH_MAX_HOURS,
) -> float:
    """自适应刷新间隔：刷新窗口与有效期取小后折半，再夹到配置区间。"""
    windows = [
        float(credential.get(name) or 0)
        for name in ("need_refresh_key_in", "key_expires_in")
    ]
    positive = [value for value in windows if value > 0]
    target = min(positive) / 2 if positive else 12 * 3600
    return max(min_hours * 3600, min(target, max_hours * 3600))


def mask_uin(uin: str) -> str:
    uin = str(uin or "").strip()
    if not uin:
        return ""
    return uin if len(uin) <= 4 else f"{uin[:4]}****"


class CredentialStore:
    """读写凭证文件与派生的 Cookie 状态文件。"""

    def __init__(self, credential_path: Path | None = None):
        self.path = Path(credential_path or default_credential_file())
        self.state_path = cookie_state_path(self.path)
        self.device_path = device_state_path(self.path)

    def load(self) -> tuple[dict[str, Any], float] | None:
        """返回 ``(credential_dict, saved_at)``；文件缺失或损坏时返回 None。"""
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            credential = payload["credential"]
            saved_at = float(payload.get("saved_at") or 0)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("QQ 音乐凭证文件损坏，忽略：%s (%s)", self.path, exc)
            return None
        if not isinstance(credential, dict) or not credential.get("musickey"):
            return None
        return credential, saved_at

    def save(self, credential: Any, *, source: str) -> dict[str, Any]:
        """持久化凭证并发布 Cookie 状态文件，返回发布的元数据。"""
        data = _credential_to_dict(credential)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"saved_at": time.time(), "credential": data}
        _atomic_write_json(self.path, payload)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return publish_cookie(
            cookie_string(data),
            uin=extract_uin(data),
            expires_at=expiry_timestamp(data),
            source=source,
            state_path=self.state_path,
        )

    def summary(self) -> dict[str, Any]:
        """无敏感字段的状态摘要，供 CLI 与面板展示。"""
        loaded = self.load()
        if loaded is None:
            return {
                "state": "missing",
                "has_credential": False,
                "credential_file": str(self.path),
            }
        credential, saved_at = loaded
        expires_at = expiry_timestamp(credential)
        now = time.time()
        if expires_at and expires_at <= now:
            state = "expired"
        elif expires_at and expires_at - now < 7 * 86400:
            state = "expiring"
        else:
            state = "ok"
        return {
            "state": state,
            "has_credential": True,
            "credential_file": str(self.path),
            "uin": mask_uin(extract_uin(credential)),
            "has_refresh_key": bool(credential.get("refresh_key")),
            "saved_at": saved_at,
            "expires_at": expires_at,
            "login_type": credential.get("login_type"),
        }


def _credential_to_dict(credential: Any) -> dict[str, Any]:
    if isinstance(credential, dict):
        return credential
    dump = getattr(credential, "model_dump_json", None)
    if callable(dump):
        return json.loads(dump())
    raise TypeError(f"不支持的凭证类型: {type(credential)!r}")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


@dataclass
class _CookieCache:
    path: Path | None = None
    mtime: float | None = None
    cookie: str = ""
    uin: str = ""
    expires_at: float = 0.0
    updated_at: float = 0.0
    source: str = ""


_cookie_cache = _CookieCache()


def publish_cookie(
    cookie: str,
    *,
    uin: str = "",
    expires_at: float = 0.0,
    source: str = "manual",
    state_path: Path | None = None,
) -> dict[str, Any]:
    """写入 Cookie 状态文件并刷新进程内缓存，让所有消费方立即拿到新值。"""
    cookie = str(cookie or "").strip()
    published_at = time.time()
    payload = {
        "cookie": cookie,
        "uin": uin,
        "expires_at": expires_at,
        "updated_at": published_at,
        "source": source,
    }
    path = Path(state_path or cookie_state_path())
    if cookie:
        _atomic_write_json(path, payload)
    with _LOCK:
        _cookie_cache.path = path
        _cookie_cache.mtime = _state_mtime(_cookie_cache.path)
        _cookie_cache.cookie = cookie
        _cookie_cache.uin = uin
        _cookie_cache.expires_at = expires_at
        _cookie_cache.updated_at = published_at
        _cookie_cache.source = source
    return payload


def _state_mtime(path: Path | None) -> float | None:
    try:
        return path.stat().st_mtime if path else None
    except OSError:
        return None


def current_cookie(fallback: str = "") -> str:
    """返回当前生效的 Cookie；状态文件优先，其次传入的兜底值。

    每次调用只做一次 ``stat``，文件未变化时直接命中缓存，适合放进
    每个音乐请求的头部构造里。
    """
    path = cookie_state_path()
    mtime = _state_mtime(path)
    with _LOCK:
        cache = _cookie_cache
        if cache.path == path and cache.mtime is not None and cache.mtime == mtime:
            return cache.cookie or fallback
        if mtime is None:
            cache.path, cache.mtime, cache.cookie = path, None, ""
            return fallback
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cookie = str(payload.get("cookie") or "").strip()
            cache.uin = str(payload.get("uin") or "")
            cache.expires_at = float(payload.get("expires_at") or 0)
            cache.updated_at = float(payload.get("updated_at") or 0)
            cache.source = str(payload.get("source") or "")
        except (OSError, ValueError, TypeError):
            cookie = ""
        cache.path, cache.mtime = path, mtime
        cache.cookie = cookie
        return cookie or fallback


def credential_status() -> dict[str, Any]:
    """面板/健康接口用的凭证状态（不含任何密钥字段）。"""
    store = CredentialStore()
    summary = store.summary()
    path = cookie_state_path(store.path)
    mtime = _state_mtime(path)
    with _LOCK:
        cookie_present = bool(_cookie_cache.cookie) or mtime is not None
        updated_at = _cookie_cache.updated_at
        source = _cookie_cache.source
        uin = _cookie_cache.uin
    status = {
        "cookie_state_file": str(path),
        "has_cookie": cookie_present,
        "cookie_updated_at": updated_at,
        "cookie_source": source,
        "uin": mask_uin(uin) or summary.get("uin", ""),
        "auto_refresh_env": _auto_refresh_enabled(),
    }
    status.update(summary)
    return status


def _auto_refresh_enabled() -> bool:
    raw = os.getenv("QQ_MUSIC_AUTO_REFRESH", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# 刷新与登录（依赖可选的 qqmusic-api-python，全部延迟导入）
# ---------------------------------------------------------------------------


def require_qqmusic_api():
    try:
        import qqmusic_api  # noqa: F401
    except ImportError as exc:  # pragma: no cover - 环境差异
        raise RuntimeError(
            "需要安装 QQ 音乐 API 库：pip install \"oopzbot[qqmusic-login]\""
        ) from exc
    import qqmusic_api

    return qqmusic_api


@dataclass
class RefreshOutcome:
    ok: bool
    kind: str  # refreshed / missing / disabled / network_error / refresh_expired / error
    message: str
    next_check_seconds: float
    credential: dict[str, Any] | None = None


class CredentialRefresher:
    """执行一次凭证刷新；独立出来便于测试替换网络调用。"""

    def __init__(self, *, device_path: Path | None = None):
        self.device_path = Path(device_path or device_state_path())

    async def refresh(self, credential: dict[str, Any]) -> dict[str, Any]:
        qqmusic_api = require_qqmusic_api()
        cred = qqmusic_api.Credential.model_validate(credential)
        async with open_qqmusic_client(
            qqmusic_api,
            device_path=self.device_path,
        ) as client:
            new_cred = await client.login.refresh_credential(cred)
        return _credential_to_dict(new_cred)


@dataclass
class _LoopState:
    backoff_index: int = 0
    needs_relogin: bool = False


class CookieRefreshService:
    """后台线程里的凭证续期循环；发布后通过回调分发新 Cookie。"""

    def __init__(
        self,
        *,
        store: CredentialStore | None = None,
        refresher: CredentialRefresher | None = None,
        min_hours: float = DEFAULT_REFRESH_MIN_HOURS,
        max_hours: float = DEFAULT_REFRESH_MAX_HOURS,
        on_publish: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.store = store or CredentialStore()
        self.refresher = refresher or CredentialRefresher(device_path=self.store.device_path)
        self.min_hours = min_hours
        self.max_hours = max_hours
        self.on_publish = on_publish
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = _LoopState()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._thread_main,
            name="qqmusic-credential-refresh",
            daemon=True,
        )
        self._thread.start()
        logger.info("QQ 音乐凭证自动续期已启动：%s", self.store.path)

    def stop(self) -> None:
        self._stop.set()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # pragma: no cover - 兜底，避免线程静默死亡
            logger.error("QQ 音乐凭证续期线程异常退出: %s", exc)

    async def _run(self) -> None:
        while not self._stop.is_set():
            delay = await self._tick()
            await asyncio.to_thread(self._stop.wait, delay)

    async def _tick(self) -> float:
        if not _auto_refresh_enabled():
            return NO_CREDENTIAL_POLL_SECONDS
        loaded = self.store.load()
        if loaded is None:
            self._state.needs_relogin = False
            return NO_CREDENTIAL_POLL_SECONDS
        credential, saved_at = loaded
        expires_at = expiry_timestamp(credential)
        due = max(saved_at, float(credential.get("musickey_create_time") or 0)) + (
            compute_refresh_interval(
                credential,
                min_hours=self.min_hours,
                max_hours=self.max_hours,
            )
        )
        now = time.time()
        if now < due and not (expires_at and expires_at <= now):
            return min(due - now, 1800)

        outcome = await self._refresh_once(credential, saved_at)
        if outcome.credential is not None:
            interval = compute_refresh_interval(
                outcome.credential,
                min_hours=self.min_hours,
                max_hours=self.max_hours,
            )
            return interval
        return outcome.next_check_seconds

    async def _refresh_once(
        self,
        credential: dict[str, Any],
        saved_at: float,
    ) -> RefreshOutcome:
        try:
            new_credential = await self.refresher.refresh(credential)
        except Exception as exc:
            return self._classify_refresh_error(exc, saved_at)

        meta = self.store.save(new_credential, source="refresh")
        self._state.backoff_index = 0
        self._state.needs_relogin = False
        logger.info(
            "QQ 音乐凭证已刷新：uin=%s 有效期至 %s",
            mask_uin(meta.get("uin") or ""),
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta.get("expires_at") or 0)),
        )
        if self.on_publish is not None:
            try:
                self.on_publish(meta)
            except Exception as exc:
                logger.warning("QQ 音乐 Cookie 分发失败（不影响凭证更新）: %s", exc)
        return RefreshOutcome(
            ok=True,
            kind="refreshed",
            message="QQ 音乐凭证已刷新",
            next_check_seconds=NO_CREDENTIAL_POLL_SECONDS,
            credential=new_credential,
        )

    def _classify_refresh_error(
        self,
        exc: Exception,
        saved_at: float,
    ) -> RefreshOutcome:
        qqmusic_api = None
        try:
            qqmusic_api = require_qqmusic_api()
        except RuntimeError:
            pass
        refresh_error = getattr(qqmusic_api, "CredentialRefreshError", ())
        network_error = getattr(qqmusic_api, "NetworkError", ())
        code = getattr(exc, "code", "")
        detail = str(getattr(exc, "message", None) or exc)
        if refresh_error and isinstance(exc, refresh_error):
            self._state.needs_relogin = True
            logger.error(
                "QQ 音乐刷新凭据已失效(code=%s)，需要重新扫码：%s",
                code or "?",
                detail,
            )
            return RefreshOutcome(
                ok=False,
                kind="refresh_expired",
                message=f"刷新凭据已失效(code={code or '?'})，需要重新扫码登录",
                next_check_seconds=RELOGIN_RETRY_SECONDS,
            )
        index = min(self._state.backoff_index, len(NETWORK_BACKOFF_SECONDS) - 1)
        self._state.backoff_index += 1
        if network_error and isinstance(exc, network_error):
            message = f"网络错误，稍后重试: {type(exc).__name__}"
        else:
            message = f"刷新异常，稍后重试: {type(exc).__name__}"
        logger.warning("QQ 音乐凭证刷新失败: %s", message)
        return RefreshOutcome(
            ok=False,
            kind="network_error",
            message=message,
            next_check_seconds=NETWORK_BACKOFF_SECONDS[index],
        )


async def refresh_and_publish(
    *,
    store: CredentialStore | None = None,
    refresher: CredentialRefresher | None = None,
    on_publish: Callable[[dict[str, Any]], None] | None = None,
) -> RefreshOutcome:
    """CLI ``qqmusic-login refresh`` 用的单次刷新入口。"""
    service_store = store or CredentialStore()
    loaded = service_store.load()
    if loaded is None:
        return RefreshOutcome(
            ok=False,
            kind="missing",
            message="当前没有凭证，请先运行: oopzbot qqmusic-login login",
            next_check_seconds=0,
        )
    credential, saved_at = loaded
    service = CookieRefreshService(
        store=service_store,
        refresher=refresher,
        on_publish=on_publish,
    )
    return await service._refresh_once(credential, saved_at)


def propagate_cookie(settings: Any, *, managed_service: Any = None) -> dict[str, Any]:
    """把当前 Cookie 分发到各消费方（launcher 热更新端点 / 本地托管子进程重启）。

    进程内消费方（新旧核心）读取状态文件，天然拿到最新值，无需处理。
    """
    cookie = current_cookie(getattr(settings, "qq_music_cookie", "") or "")
    result: dict[str, Any] = {
        "has_cookie": bool(cookie),
        "launcher": "skipped",
        "managed": "skipped",
    }
    if not cookie:
        return result

    # 端点优先级：显式配置（Compose 跨容器）> 本地托管服务自带的端点。
    endpoint = str(getattr(settings, "qq_music_cookie_api_url", "") or "").strip()
    owns_child = (
        managed_service is not None
        and getattr(managed_service, "enabled", False)
        and getattr(managed_service, "process", None) is not None
    )
    if not endpoint and owns_child:
        endpoint = str(getattr(managed_service, "cookie_api_url", "") or "")

    if endpoint:
        result["launcher"] = _post_cookie_to_launcher(settings, endpoint, cookie)

    # 热更新失败且子进程是我们拉起的，重启兜底。
    launcher_failed = str(result["launcher"]).startswith(("http_", "error"))
    if owns_child and (launcher_failed or not endpoint):
        try:
            managed_service.restart(cookie)
            result["managed"] = "restarted"
        except Exception as exc:
            logger.warning("重启本地 QQ 音乐 API 失败: %s", exc)
            result["managed"] = f"error: {type(exc).__name__}"
    return result


def _post_cookie_to_launcher(settings: Any, endpoint: str, cookie: str) -> str:
    token = getattr(settings, "bridge_token", "") or ""
    try:
        response = requests.post(
            f"{endpoint.rstrip('/')}/internal/cookie",
            json={"cookie": cookie},
            headers={"x-qqbot-bridge-token": token} if token else {},
            timeout=5,
        )
        if response.status_code == 200:
            logger.info("已推送新 Cookie 到 QQ 音乐 API 热更新端点")
            return "updated"
        logger.warning(
            "QQ 音乐 API 热更新端点返回 HTTP %s，可能需要重启 qqmusic 容器",
            response.status_code,
        )
        return f"http_{response.status_code}"
    except requests.RequestException as exc:
        logger.warning(
            "推送 Cookie 到 %s 失败（%s）；如为 Compose 部署请重启 qqmusic 容器",
            endpoint,
            type(exc).__name__,
        )
        return f"error: {type(exc).__name__}"
