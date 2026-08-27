"""Verify and explicitly restore an OOPZ backup archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

try:
    from .backup import create_backup
except ImportError:  # Executed directly as ``python scripts/restore.py``.
    from backup import create_backup

ARCHIVE_FORMAT_VERSION = 1


def _safe_archive_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "" in path.parts:
        raise ValueError(f"归档包含不安全路径：{name}")
    return path


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_archive(archive_path: str | Path) -> tuple[Path, dict, dict[str, bytes]]:
    path = Path(archive_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"归档文件不存在：{path}")
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("备份归档不是有效 ZIP 文件") from exc
    with archive:
        entries = archive.infolist()
        names: set[str] = set()
        payload: dict[str, bytes] = {}
        for entry in entries:
            safe_name = _safe_archive_name(entry.filename)
            name = safe_name.as_posix()
            if name in names:
                raise ValueError(f"归档包含重复路径：{name}")
            names.add(name)
            mode = (entry.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise ValueError(f"归档不允许符号链接：{name}")
            if name in {"manifest.json", "checksums.sha256"} or name.startswith("data/") or name == "redis/dump.rdb":
                if entry.is_dir():
                    continue
                try:
                    payload[name] = archive.read(entry)
                except (OSError, zipfile.BadZipFile) as exc:
                    raise ValueError(f"归档内容无法读取：{name}") from exc
            else:
                raise ValueError(f"归档包含未知文件：{name}")

    if "manifest.json" not in payload or "checksums.sha256" not in payload:
        raise ValueError("归档缺少 manifest.json 或 checksums.sha256")
    try:
        manifest = json.loads(payload["manifest.json"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("manifest.json 不是有效 JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("format_version") != ARCHIVE_FORMAT_VERSION:
        raise ValueError("不支持的备份归档版本")
    if manifest.get("env_included") is True:
        raise ValueError("拒绝恢复包含 .env 的归档，请先人工审查")

    checksums: dict[str, str] = {}
    for line in payload["checksums.sha256"].decode("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            digest, name = line.split("  ", 1)
        except ValueError as exc:
            raise ValueError("校验清单格式无效") from exc
        safe_name = _safe_archive_name(name)
        if safe_name.as_posix() in checksums or len(digest) != 64:
            raise ValueError("校验清单包含无效或重复条目")
        checksums[safe_name.as_posix()] = digest
    if _sha256_bytes(payload["checksums.sha256"]) != manifest.get("checksums_sha256"):
        raise ValueError("校验清单自身的 SHA-256 不匹配")
    manifest_files = {
        str(item.get("path")): str(item.get("sha256"))
        for item in (manifest.get("files") or [])
        if isinstance(item, dict)
    }
    if checksums != manifest_files:
        raise ValueError("manifest 与校验清单不一致")
    actual_payload_names = set(payload) - {"manifest.json", "checksums.sha256"}
    if actual_payload_names != set(checksums):
        raise ValueError("归档存在未列入校验清单的文件")
    for name, expected in checksums.items():
        if _sha256_bytes(payload[name]) != expected:
            raise ValueError(f"校验和不匹配：{name}")
    return path, manifest, payload


def _validate_target(data_dir: str | Path) -> Path:
    target = Path(data_dir).expanduser().resolve()
    if target == Path(target.anchor) or target == Path.cwd().resolve():
        raise ValueError("拒绝把恢复目标设为文件系统根目录或当前工作区根目录")
    return target


def _restore_data(data_dir: Path, payload: dict[str, bytes]) -> None:
    data_files = {
        name.removeprefix("data/"): content
        for name, content in payload.items()
        if name.startswith("data/")
    }
    with tempfile.TemporaryDirectory(prefix=".oopz-restore-", dir=data_dir.parent) as name:
        stage = Path(name) / "data"
        for relative, content in data_files.items():
            safe = _safe_archive_name(relative)
            destination = stage / Path(*safe.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        data_dir.mkdir(parents=True, exist_ok=True)
        for child in data_dir.iterdir():
            if child.name == ".env" or child.name.startswith(".env."):
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        for child in stage.iterdir() if stage.exists() else ():
            shutil.move(str(child), str(data_dir / child.name))


def _restore_redis(
    payload: dict[str, bytes],
    compose_file: str | Path,
    redis_service: str,
) -> None:
    if "redis/dump.rdb" not in payload:
        raise ValueError("归档不包含 Redis 快照")
    compose_path = Path(compose_file).expanduser().resolve()
    try:
        stop = subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "stop", redis_service],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if stop.returncode != 0:
            raise RuntimeError("无法停止 Redis，拒绝写入恢复快照")
        # Redis 7 with appendonly enabled prefers the AOF over dump.rdb.  The
        # one-shot Compose container below uses the same named volume while the
        # service is stopped, and removes only Redis persistence files.
        write = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_path),
                "run",
                "--rm",
                "--no-deps",
                "-T",
                redis_service,
                "sh",
                "-c",
                "rm -f /data/dump.rdb /data/appendonly.aof; "
                "if [ -d /data/appendonlydir ]; then "
                "find /data/appendonlydir -mindepth 1 -maxdepth 1 -delete; fi; "
                "cat > /data/dump.rdb",
            ],
            input=payload["redis/dump.rdb"],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if write.returncode != 0:
            raise RuntimeError("Redis 快照写入失败，请确认 Redis 数据卷可写")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("无法通过 Docker 写入 Redis 快照") from exc
    finally:
        try:
            start = subprocess.run(
                ["docker", "compose", "-f", str(compose_path), "start", redis_service],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Redis 恢复后无法重新启动 Redis") from exc
        if start.returncode != 0:
            raise RuntimeError("Redis 恢复后无法重新启动 Redis")


def restore_backup(
    archive_path: str | Path,
    data_dir: str | Path,
    *,
    component: str = "all",
    compose_file: str | Path = "compose.yaml",
    redis_service: str = "redis",
) -> Path:
    archive, _manifest, payload = validate_archive(archive_path)
    target = _validate_target(data_dir)
    recovery_dir = target.parent / "oopz-backups"
    recovery_dir.mkdir(parents=True, exist_ok=True)
    recovery = recovery_dir / f"pre-restore-{archive.stem}.zip"
    create_backup(
        target,
        recovery,
        compose_file=compose_file,
        redis_service=redis_service,
        include_redis=component in {"redis", "all"},
        force=True,
        purpose="pre-restore-recovery",
    )
    if component in {"redis", "all"}:
        _restore_redis(payload, compose_file, redis_service)
    if component in {"data", "all"}:
        _restore_data(target, payload)
    return recovery


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验并恢复 OOPZ 备份")
    parser.add_argument("archive")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--component", choices=("data", "redis", "all"), default="all")
    parser.add_argument("--compose-file", default="compose.yaml")
    parser.add_argument("--redis-service", default="redis")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="确认 Bot 已停止、当前数据会先自动备份且本次恢复是破坏性操作",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.confirm:
        print("拒绝恢复：必须提供 --confirm，并先停止 Bot。")
        return 2
    try:
        recovery = restore_backup(
            args.archive,
            args.data_dir,
            component=args.component,
            compose_file=args.compose_file,
            redis_service=args.redis_service,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"恢复失败：{exc}")
        return 1
    print(f"恢复完成：{args.component}")
    print(f"恢复前自动备份：{recovery}")
    print(".env 默认不在备份中，也不会由本工具恢复。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
