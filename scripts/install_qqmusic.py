"""Install the exact QQ Music API revision supported by this bot."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from oopzbot.qqmusic_service import (  # noqa: E402
    QQMUSIC_BRANCH,
    QQMUSIC_COMMIT,
    QQMUSIC_MARKER,
    QQMUSIC_REPOSITORY,
    install_marker,
    managed_installation_errors,
)


def run(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
    )
    return result.stdout.strip()


def try_run(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def require_tool(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"找不到 {name}，请先安装后重试")
    return executable


def require_node_18(node: str) -> None:
    version = run([node, "-p", "process.versions.node"])
    try:
        major = int(version.split(".", 1)[0])
    except ValueError as exc:
        raise RuntimeError(f"无法识别 Node.js 版本：{version}") from exc
    if major < 18:
        raise RuntimeError(f"QQ 音乐 API 需要 Node.js 18+，当前为 {version}")


def install(directory: Path) -> None:
    git = require_tool("git")
    node = require_tool("node")
    npm = require_tool("npm")
    require_node_18(node)

    directory = directory.resolve()
    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.mkdir()
        print(f"正在下载固定版本 QQ 音乐 API：{QQMUSIC_COMMIT[:12]}")
        run([git, "init"], cwd=directory)
        run([git, "remote", "add", "origin", QQMUSIC_REPOSITORY], cwd=directory)
    elif not (directory / ".git").is_dir():
        raise RuntimeError(f"目录已存在但不是 QQ 音乐 API Git 仓库：{directory}")

    remote = try_run([git, "remote", "get-url", "origin"], cwd=directory)
    if remote != QQMUSIC_REPOSITORY:
        raise RuntimeError(f"音乐 API 仓库来源不匹配：{remote or '未配置 origin'}")

    current = try_run([git, "rev-parse", "--verify", "HEAD"], cwd=directory)
    if current and current != QQMUSIC_COMMIT:
        raise RuntimeError(
            f"现有音乐 API 版本为 {current[:12]}，预期 {QQMUSIC_COMMIT[:12]}。"
            f"请备份后移除目录再重试：{directory}"
        )
    if not current:
        run(
            [git, "fetch", "--depth", "1", "origin", QQMUSIC_COMMIT],
            cwd=directory,
        )
        run([git, "checkout", "--detach", "FETCH_HEAD"], cwd=directory)
        current = run([git, "rev-parse", "HEAD"], cwd=directory)
    if current != QQMUSIC_COMMIT:
        raise RuntimeError(f"下载后的音乐 API 提交不匹配：{current}")
    changed = run([git, "status", "--porcelain", "--untracked-files=no"], cwd=directory)
    if changed:
        raise RuntimeError(f"音乐 API 源码存在本地修改，请先备份或还原：{directory}")

    print("正在安装 QQ 音乐 API 依赖……")
    run(
        [npm, "ci", "--include=dev", "--no-audit", "--no-fund"],
        cwd=directory,
    )
    marker_path = directory / QQMUSIC_MARKER
    marker_path.write_text(
        json.dumps(install_marker(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    errors = managed_installation_errors(directory)
    if errors:
        raise RuntimeError("\n".join(errors))
    print(f"QQ 音乐 API 安装完成：{directory}")


def main() -> None:
    parser = argparse.ArgumentParser(description="安装 Bot 固定使用的 QQ 音乐 API")
    parser.add_argument("--directory", default=".services/qqmusic-api")
    args = parser.parse_args()
    try:
        install(Path(args.directory))
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"QQ 音乐 API 安装失败：{exc}") from exc


if __name__ == "__main__":
    main()
