"""Lifecycle management for the pinned QQ Music API service."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .config import Settings

logger = logging.getLogger("QQMusicService")

QQMUSIC_REPOSITORY = "https://github.com/Rain120/qq-music-api.git"
QQMUSIC_COMMIT = "d05420bf098bd2769866eba81cfd48a6d0c6f50c"
QQMUSIC_BRANCH = "next"
QQMUSIC_LICENSE = "MIT"
QQMUSIC_MARKER = ".oopzbot-managed.json"
QQMUSIC_REQUIRED_ENDPOINTS = {
    "getSearchByKey",
    "getMusicPlay",
    "getSongInfo",
    "getLyric",
}


def service_directory(value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def managed_url_error(base_url: str) -> str:
    """Return an error when a managed API URL is not a loopback HTTP endpoint."""
    try:
        parsed = urlsplit(base_url)
        port = parsed.port or 80
    except ValueError:
        return "QQ_MUSIC_BASE_URL 不是有效地址"
    if parsed.scheme != "http":
        return "托管音乐 API 的 QQ_MUSIC_BASE_URL 必须使用 http"
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return "托管音乐 API 的 QQ_MUSIC_BASE_URL 必须使用回环地址"
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return "托管音乐 API 的 QQ_MUSIC_BASE_URL 不能包含路径、查询参数或片段"
    if not 1 <= port <= 65535:
        return "托管音乐 API 端口必须在 1-65535 之间"
    return ""


def managed_installation_errors(directory: Path) -> list[str]:
    errors: list[str] = []
    marker_path = directory / QQMUSIC_MARKER
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        marker = {}
    if marker.get("repository") != QQMUSIC_REPOSITORY:
        errors.append(f"缺少音乐 API 安装标记：{marker_path}")
    if marker.get("commit") != QQMUSIC_COMMIT:
        errors.append(f"音乐 API 不是受支持的固定版本 {QQMUSIC_COMMIT[:12]}")
    required_paths = (
        directory / "package.json",
        directory / "src" / "app.ts",
        directory / "node_modules" / "ts-node" / "register" / "transpile-only.js",
    )
    if not all(path.is_file() for path in required_paths):
        errors.append(f"音乐 API 安装不完整，请重新运行安装脚本：{directory}")
    return errors


def _minimal_child_environment() -> dict[str, str]:
    allowed = {
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "USERPROFILE",
        "WINDIR",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


class ManagedQQMusicService:
    """Start the pinned API as a child process and stop only processes we own."""

    def __init__(self, settings: Settings, *, timeout: float = 30.0):
        self.enabled = settings.qq_music_enabled and settings.qq_music_managed
        self.base_url = settings.qq_music_base_url.rstrip("/")
        self.directory = service_directory(settings.qq_music_service_dir)
        self.timeout = timeout
        self.process: subprocess.Popen | None = None

    def _compatible_service_is_ready(self) -> bool:
        try:
            response = requests.get(
                f"{self.base_url}/explorer/metadata",
                timeout=1.0,
            )
            response.raise_for_status()
            metadata = json.dumps(response.json(), ensure_ascii=False)
        except (requests.RequestException, ValueError):
            return False
        return all(endpoint in metadata for endpoint in QQMUSIC_REQUIRED_ENDPOINTS)

    def start(self) -> None:
        if not self.enabled:
            return
        url_error = managed_url_error(self.base_url)
        if url_error:
            raise RuntimeError(url_error)
        if self._compatible_service_is_ready():
            logger.info("检测到已运行的兼容 QQ 音乐 API：%s", self.base_url)
            return
        errors = managed_installation_errors(self.directory)
        if errors:
            raise RuntimeError("\n".join(errors))
        node = shutil.which("node")
        if not node:
            raise RuntimeError("找不到 Node.js 18+，请重新运行安装脚本")

        parsed = urlsplit(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        launcher = Path(__file__).with_name("qqmusic_launcher.cjs")
        child_env = _minimal_child_environment()
        child_env.update(
            PORT=str(port),
            QQ_MUSIC_HOST=host,
        )
        self.process = subprocess.Popen(
            [node, str(launcher), str(self.directory)],
            cwd=self.directory,
            env=child_env,
        )

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                code = self.process.returncode
                self.process = None
                raise RuntimeError(f"QQ 音乐 API 启动失败，退出码 {code}")
            if self._compatible_service_is_ready():
                logger.info(
                    "已启动固定版本 QQ 音乐 API：%s (%s)",
                    self.base_url,
                    QQMUSIC_COMMIT[:12],
                )
                return
            time.sleep(0.25)

        self.close()
        raise RuntimeError(f"QQ 音乐 API 在 {self.timeout:g} 秒内未就绪：{self.base_url}")

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def install_marker() -> dict[str, str]:
    return {
        "repository": QQMUSIC_REPOSITORY,
        "branch": QQMUSIC_BRANCH,
        "commit": QQMUSIC_COMMIT,
        "license": QQMUSIC_LICENSE,
    }
