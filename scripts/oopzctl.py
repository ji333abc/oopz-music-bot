"""Safe operator entry point for diagnose, backup, upgrade and rollback."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

try:
    from .backup import create_backup
    from .restore import restore_backup, validate_archive
except ImportError:
    from backup import create_backup
    from restore import restore_backup, validate_archive

ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = ROOT / "oopz-releases"
SERVICE_READY_TIMEOUT_SECONDS = 240

# Running ``python scripts/oopzctl.py`` puts only ``scripts/`` on sys.path.
# Keep the repository package importable for redaction and other lazy imports.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(
    args: list[str],
    *,
    timeout: int = 30,
    check: bool = False,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, **(environment or {})},
    )
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "命令失败").strip()[-1000:])
    return result


def _git(*args: str, check: bool = False) -> str:
    try:
        result = _run(["git", *args], timeout=30, check=check)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _redact(value: object) -> str:
    from oopzbot.observability import redact_secrets

    return redact_secrets(str(value or ""), max_length=2000)


def _sanitize_object(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            _redact(key)[:120]: _sanitize_object(item, depth=depth + 1)
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, list):
        return [_sanitize_object(item, depth=depth + 1) for item in value[:30]]
    if isinstance(value, str):
        return _redact(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact(value)


def _docker_info(profile: str | None = None) -> dict[str, Any]:
    args = ["docker", "compose"]
    if profile:
        args += ["--profile", profile]
    args += ["ps", "--format", "json"]
    try:
        result = _run(args, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": type(exc).__name__}
    return {
        "available": result.returncode == 0,
        "services": [_redact(line) for line in result.stdout.splitlines() if line.strip()],
        "error": _redact(result.stderr) if result.returncode else "",
    }


def _compose_exec_json(path: str) -> dict[str, Any]:
    code = (
        "import json,os,urllib.request;"
        f"u='http://127.0.0.1:18080{path}';"
        "r=urllib.request.Request(u);"
        "t=os.getenv('QQBOT_BRIDGE_TOKEN','');"
        "r.add_header('x-qqbot-bridge-token',t) if t else None;"
        "print(urllib.request.urlopen(r,timeout=8).read().decode())"
    )
    try:
        result = _run(
            ["docker", "compose", "exec", "-T", "bot", "python", "-c", code],
            timeout=20,
        )
        value = json.loads(result.stdout) if result.returncode == 0 else {}
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def _diagnostic_snapshot() -> dict[str, Any]:
    snapshot = _compose_exec_json("/internal/panel/snapshot")
    selected = {
        key: snapshot.get(key)
        for key in (
            "schema_version",
            "queue_version",
            "health",
            "qqmusic_credential",
            "qqmusic_diagnostics",
            "search_cache",
            "external_metrics",
            "failure_history",
        )
        if key in snapshot
    }
    # Run the same central redactor again before a bundle is written.
    return _sanitize_object(selected)


def diagnose(output: Path | None = None, *, profile: str | None = None) -> dict[str, Any]:
    usage = shutil.disk_usage(ROOT)
    docker = _docker_info(profile)
    try:
        compose_profiles = _run(
            ["docker", "compose", "config", "--profiles"], timeout=20
        ).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        compose_profiles = []
    try:
        nginx = _run(["nginx", "-t"], timeout=20)
        nginx_status = "ok" if nginx.returncode == 0 else _redact(nginx.stderr)
    except (OSError, subprocess.TimeoutExpired):
        nginx_status = "unavailable"
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": {
            "commit": _git("rev-parse", "HEAD"),
            "branch": _git("branch", "--show-current"),
            "dirty_count": len(_git("status", "--porcelain").splitlines()),
        },
        "profile": profile or "default",
        "runtime": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "compose_profiles": compose_profiles,
        },
        "docker": docker,
        "readyz": _compose_exec_json("/readyz") if docker.get("available") else {},
        "panel_diagnostics": _diagnostic_snapshot() if docker.get("available") else {},
        "nginx_config_test": nginx_status,
        "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
        "files": {
            "compose": (ROOT / "compose.yaml").is_file(),
            "env_present": (ROOT / ".env").is_file(),
            "panel_state": (ROOT / "data" / "panel-state.json").is_file(),
        },
        "excluded": [
            ".env",
            "credential JSON",
            "Cookie state",
            "Redis dump",
            "raw logs",
        ],
    }
    if output is not None:
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".oopz-diagnose-", dir=output.parent) as name:
            payload = Path(name) / "diagnose.json"
            payload.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(payload, "diagnose.json")
    return report


def dependency_manifest(output: Path | None = None) -> dict[str, Any]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    base = list(project.get("dependencies") or [])
    extras = project.get("optional-dependencies") or {}
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "core": {
            "python": sorted(base + list(extras.get("legacy") or []) + list(extras.get("qqmusic-login") or [])),
            "system": ["chromium", "chromium-driver", "ca-certificates", "gosu"],
            "node_uploader": False,
        },
        "jm-worker": {
            "python": sorted(base + list(extras.get("jm") or [])),
            "system": ["chromium", "chromium-driver", "ca-certificates", "gosu", "nodejs", "npm"],
            "node_uploader": True,
        },
    }
    if output:
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return manifest


def _require_clean_worktree() -> None:
    status = _git("status", "--porcelain")
    if status == "unavailable" or status:
        raise RuntimeError("工作目录存在未提交或未知修改，拒绝升级")


def _required_environment(profile: str | None) -> dict[str, str]:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        raise RuntimeError("缺少 .env")
    if os.name != "nt" and stat.S_IMODE(env_path.stat().st_mode) & 0o077:
        raise RuntimeError(".env 权限过宽；请设置为 0600")
    text = env_path.read_text(encoding="utf-8", errors="replace")
    values = {
        key.strip(): value.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }
    for key in ("QQBOT_BRIDGE_TOKEN", "OOPZ_PANEL_PASSWORD"):
        if not values.get(key):
            raise RuntimeError(f".env 缺少必填项 {key}")
    jm_enabled = values.get("QQBOT_JM_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if jm_enabled != (profile == "jm"):
        raise RuntimeError("QQBOT_JM_ENABLED 与 Compose Profile 不匹配")
    return values


def _validate_ref(ref: str) -> str:
    value = str(ref or "").strip()
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", value)
        or ".." in value
        or value.endswith(("/", "."))
    ):
        raise ValueError("Git ref 格式无效")
    return value


_IMAGE_ENV = {
    "bot": "OOPZ_BOT_IMAGE",
    "panel": "OOPZ_PANEL_IMAGE",
    "qqmusic": "OOPZ_QQMUSIC_IMAGE",
    "jm-worker": "OOPZ_JM_IMAGE",
}


def _running_image_environment(profile: str | None) -> dict[str, str]:
    """Capture immutable image references from the currently running release."""
    compose = ["docker", "compose"] + (["--profile", profile] if profile else [])
    result: dict[str, str] = {}
    for service, variable in _IMAGE_ENV.items():
        container = _run([*compose, "ps", "-q", service]).stdout.strip()
        if not container:
            continue
        image = _run(
            ["docker", "inspect", "--format", "{{.Config.Image}}", container]
        ).stdout.strip()
        if image:
            result[variable] = image
    return result


def _release_image_environment(sha: str) -> dict[str, str]:
    tag = sha[:12]
    return {
        "OOPZ_BOT_IMAGE": f"oopz-bot:{tag}",
        "OOPZ_PANEL_IMAGE": f"oopz-panel:{tag}",
        "OOPZ_QQMUSIC_IMAGE": f"oopz-qqmusic:{tag}",
        "OOPZ_JM_IMAGE": f"oopz-jm-worker:{tag}",
    }


def _wait_for_command(
    args: list[str],
    *,
    environment: dict[str, str],
    label: str,
    timeout: int = SERVICE_READY_TIMEOUT_SECONDS,
    interval: float = 3,
) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while True:
        remaining = max(1, int(deadline - time.monotonic()))
        try:
            result = _run(
                args,
                timeout=min(20, remaining),
                environment=environment,
            )
            if result.returncode == 0:
                return
            last_error = (result.stderr or result.stdout or "命令失败").strip()[-1000:]
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            detail = f": {_redact(last_error)}" if last_error else ""
            raise RuntimeError(f"等待{label}超时{detail}")
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


def _wait_for_url(
    url: str,
    *,
    label: str,
    timeout: int = SERVICE_READY_TIMEOUT_SECONDS,
    interval: float = 3,
) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while True:
        remaining = max(1, int(deadline - time.monotonic()))
        try:
            with urlopen(url, timeout=min(15, remaining)) as response:  # noqa: S310
                if response.status == 200:
                    return
                last_error = f"HTTP {response.status}"
        except (OSError, URLError) as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            detail = f": {_redact(last_error)}" if last_error else ""
            raise RuntimeError(f"等待{label}超时{detail}")
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


def _verify_services(
    compose: list[str],
    environment: dict[str, str],
    *,
    public_url: str = "",
) -> None:
    _wait_for_command(
        [
            *compose,
            "exec",
            "-T",
            "bot",
            "python",
            "-c",
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18080/readyz', timeout=10).read()",
        ],
        environment=environment,
        label="Bot readyz",
    )
    _wait_for_command(
        [
            *compose,
            "exec",
            "-T",
            "panel",
            "node",
            "-e",
            "fetch('http://127.0.0.1:3000/api/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))",
        ],
        environment=environment,
        label="Panel health",
    )
    if public_url:
        parsed = urlsplit(public_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("OOPZ_PANEL_PUBLIC_URL 格式无效")
        health_url = public_url.rstrip("/") + "/api/health"
        _wait_for_url(health_url, label="公网 Panel health")


def upgrade(ref: str, *, profile: str | None, dry_run: bool) -> dict[str, Any]:
    ref = _validate_ref(ref)
    _require_clean_worktree()
    env_values = _required_environment(profile)
    if shutil.disk_usage(ROOT).free < 1024 * 1024 * 1024:
        raise RuntimeError("可用磁盘不足 1 GiB，拒绝升级")
    if shutil.which("docker") is None:
        raise RuntimeError("Docker/Compose 不可用")
    compose = ["docker", "compose"] + (["--profile", profile] if profile else [])
    _run(["docker", "compose", "version"], check=True)
    _run([*compose, "config", "--quiet"], check=True)
    old_sha = _git("rev-parse", "HEAD", check=True)
    old_images = _docker_info(profile)
    old_image_environment = _running_image_environment(profile)
    plan = {
        "old_sha": old_sha,
        "ref": ref,
        "profile": profile or "default",
        "dry_run": dry_run,
        "steps": ["backup", "fetch", "build", "switch", "health", "manifest"],
    }
    if dry_run:
        _run(["docker", "compose", "config", "--quiet"], check=True)
        return plan

    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    release_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = RELEASE_DIR / f"pre-upgrade-{release_id}.zip"
    create_backup(ROOT / "data", backup, compose_file=ROOT / "compose.yaml")
    validate_archive(backup)
    _run(["git", "fetch", "--no-tags", "origin", ref], timeout=120, check=True)
    new_sha = _git("rev-parse", "FETCH_HEAD", check=True)
    new_image_environment = _release_image_environment(new_sha)
    try:
        _run(["git", "switch", "--detach", new_sha], check=True)
        _run(
            [*compose, "build"],
            timeout=3600,
            check=True,
            environment=new_image_environment,
        )
        _run(
            [*compose, "up", "-d"],
            timeout=300,
            check=True,
            environment=new_image_environment,
        )
        _verify_services(
            compose,
            new_image_environment,
            public_url=env_values.get("OOPZ_PANEL_PUBLIC_URL", ""),
        )
        health = "ok"
    except Exception as exc:
        rollback_health = "failed"
        try:
            _run(["git", "switch", "--detach", old_sha], check=True)
            _run(
                [*compose, "up", "-d", "--no-build"],
                timeout=300,
                check=True,
                environment=old_image_environment,
            )
            _verify_services(
                compose,
                old_image_environment,
                public_url=env_values.get("OOPZ_PANEL_PUBLIC_URL", ""),
            )
            rollback_health = "ok"
        finally:
            failure_manifest = {
                **plan,
                "release_id": release_id,
                "new_sha": new_sha,
                "backup": str(backup),
                "health": "failed",
                "failure": _redact(exc),
                "rollback_health": rollback_health,
                "old_images": old_images,
                "old_image_environment": old_image_environment,
                "new_image_environment": new_image_environment,
                "data_restored": False,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            (RELEASE_DIR / f"{release_id}-failed.json").write_text(
                json.dumps(failure_manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        raise
    manifest = {
        **plan,
        "release_id": release_id,
        "new_sha": new_sha,
        "backup": str(backup),
        "health": health,
        "old_images": old_images,
        "old_image_environment": old_image_environment,
        "new_image_environment": new_image_environment,
        "new_images": _docker_info(profile),
        "data_schema": 2,
        "rollback_data_compatible": True,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (RELEASE_DIR / f"{release_id}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def rollback(release_id: str) -> dict[str, Any]:
    path = (RELEASE_DIR / f"{release_id}.json").resolve()
    if path.parent != RELEASE_DIR.resolve() or not path.is_file():
        raise ValueError("回滚记录不存在")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("rollback_data_compatible") is False:
        raise RuntimeError("该版本声明数据 schema 不兼容，拒绝自动回滚；请显式恢复备份")
    target = str(manifest.get("old_sha") or "")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", target):
        raise ValueError("回滚记录缺少旧提交")
    profile = manifest.get("profile")
    if profile not in {None, "default", "jm"}:
        raise ValueError("回滚记录包含未知 Compose Profile")
    profile_name = str(profile) if profile and profile != "default" else None
    _require_clean_worktree()
    env_values = _required_environment(profile_name)
    if shutil.which("docker") is None:
        raise RuntimeError("Docker/Compose 不可用")
    _run(["git", "switch", "--detach", target], check=True)
    compose = ["docker", "compose"] + (
        ["--profile", profile_name] if profile_name else []
    )
    old_image_environment = {
        str(key): str(value)
        for key, value in (manifest.get("old_image_environment") or {}).items()
        if key in _IMAGE_ENV.values()
    }
    _run(
        [*compose, "up", "-d", "--no-build"],
        timeout=300,
        check=True,
        environment=old_image_environment,
    )
    _verify_services(
        compose,
        old_image_environment,
        public_url=env_values.get("OOPZ_PANEL_PUBLIC_URL", ""),
    )
    return {"ok": True, "release_id": release_id, "restored_sha": target, "data_restored": False}


def prune_releases(*, keep: int) -> dict[str, Any]:
    """Delete explicitly requested old rollback points, never the newest two."""
    keep = int(keep)
    if keep < 2:
        raise ValueError("至少保留最近两个成功回滚点")
    root = RELEASE_DIR.resolve()
    records = sorted(
        (
            path
            for path in RELEASE_DIR.glob("*.json")
            if not path.name.endswith("-failed.json")
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    retained = records[:keep]
    removed = records[keep:]
    retained_backups: set[Path] = set()
    for path in retained:
        try:
            backup = Path(json.loads(path.read_text(encoding="utf-8")).get("backup", "")).resolve()
        except (OSError, ValueError):
            continue
        if backup.parent == root:
            retained_backups.add(backup)
    deleted: list[str] = []
    for path in removed:
        resolved = path.resolve()
        if resolved.parent != root:
            continue
        try:
            backup = Path(json.loads(path.read_text(encoding="utf-8")).get("backup", "")).resolve()
        except (OSError, ValueError):
            backup = Path()
        resolved.unlink()
        deleted.append(resolved.name)
        if (
            backup.parent == root
            and backup not in retained_backups
            and backup.is_file()
        ):
            backup.unlink()
            deleted.append(backup.name)
    return {"ok": True, "kept": len(retained), "deleted": deleted}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="oopzctl")
    commands = root.add_subparsers(dest="command", required=True)
    diagnose_parser = commands.add_parser("diagnose")
    diagnose_parser.add_argument("--output", type=Path)
    diagnose_parser.add_argument("--profile", choices=["jm"])
    dependency_parser = commands.add_parser("dependencies")
    dependency_parser.add_argument("--output", type=Path)
    backup_parser = commands.add_parser("backup")
    backup_parser.add_argument("backup_action", nargs="?", choices=["verify"])
    backup_parser.add_argument("archive", nargs="?", type=Path)
    backup_parser.add_argument("--output", type=Path)
    backup_parser.add_argument("--skip-redis", action="store_true")
    verify_parser = commands.add_parser("backup-verify", aliases=["verify"])
    verify_parser.add_argument("archive", type=Path)
    upgrade_parser = commands.add_parser("upgrade")
    upgrade_parser.add_argument("--ref", default="main")
    upgrade_parser.add_argument("--profile", choices=["jm"])
    upgrade_parser.add_argument("--dry-run", action="store_true")
    rollback_parser = commands.add_parser("rollback")
    rollback_parser.add_argument("--release", required=True)
    releases_parser = commands.add_parser("releases")
    releases_parser.add_argument("release_action", choices=["prune"])
    releases_parser.add_argument("--keep", type=int, default=5)
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("archive", type=Path)
    restore_parser.add_argument("--component", choices=["data", "redis", "all"], default="all")
    restore_parser.add_argument("--confirm", action="store_true")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "diagnose":
            result = diagnose(args.output, profile=args.profile)
        elif args.command == "dependencies":
            result = dependency_manifest(args.output)
        elif args.command == "backup":
            if args.backup_action == "verify":
                if args.archive is None:
                    raise ValueError("backup verify 需要归档路径")
                archive, manifest, _payload = validate_archive(args.archive)
                result = {"ok": True, "archive": str(archive), "manifest": manifest}
            else:
                output = args.output or ROOT / "oopz-backups" / f"backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.zip"
                result = {"archive": str(create_backup(ROOT / "data", output, compose_file=ROOT / "compose.yaml", include_redis=not args.skip_redis))}
        elif args.command in {"backup-verify", "verify"}:
            archive, manifest, _payload = validate_archive(args.archive)
            result = {"ok": True, "archive": str(archive), "manifest": manifest}
        elif args.command == "upgrade":
            result = upgrade(args.ref, profile=args.profile, dry_run=args.dry_run)
        elif args.command == "rollback":
            result = rollback(args.release)
        elif args.command == "releases":
            result = prune_releases(keep=args.keep)
        else:
            if not args.confirm:
                raise RuntimeError("恢复是破坏性操作，必须显式传入 --confirm")
            result = {"archive": str(restore_backup(args.archive, ROOT / "data", component=args.component, compose_file=ROOT / "compose.yaml"))}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": _redact(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
