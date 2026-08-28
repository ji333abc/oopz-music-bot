"""Create a verified OOPZ data and Redis backup archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ARCHIVE_FORMAT_VERSION = 1


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else "unknown"


def _compose_digest(compose_file: Path) -> str:
    try:
        return sha256_file(compose_file)
    except OSError:
        return "unavailable"


def _image_manifest(compose_file: Path, redis_service: str) -> list[dict[str, str]]:
    """Best-effort image IDs; backups remain usable without Docker installed."""
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "images",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    images: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        images.append(
            {
                "service": str(item.get("Service") or item.get("service") or ""),
                "image": str(item.get("Image") or item.get("image") or ""),
                "id": str(item.get("ID") or item.get("id") or ""),
            }
        )
    del redis_service
    return images


_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-shm", "-wal")


def _is_sqlite_database(path: Path) -> bool:
    if path.suffix.lower() not in _SQLITE_SUFFIXES:
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _copy_sqlite_database(source: Path, destination: Path) -> None:
    """Create a transactionally consistent copy of a live SQLite database."""
    source_uri = source.resolve().as_uri() + "?mode=ro"
    # sqlite3.Connection's context manager only controls transactions; it does
    # not close file handles. Explicit closing is required for Windows backups.
    with closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as source_db:
        with closing(sqlite3.connect(destination, timeout=10)) as destination_db:
            source_db.backup(destination_db)
    shutil.copystat(source, destination)


def _is_sqlite_sidecar(path: Path) -> bool:
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        if path.name.endswith(suffix):
            database = path.with_name(path.name[: -len(suffix)])
            return _is_sqlite_database(database)
    return False


def _copy_data(data_dir: Path, stage: Path, excluded: Path) -> list[tuple[str, str]]:
    if not data_dir.exists():
        return []
    if not data_dir.is_dir():
        raise ValueError(f"数据目录不是目录：{data_dir}")
    target = stage / "data"
    files: list[tuple[str, str]] = []
    for source in sorted(data_dir.rglob("*")):
        if source.is_symlink():
            raise ValueError(f"数据目录不允许符号链接：{source.relative_to(data_dir)}")
        if not source.is_file() or source.resolve() == excluded:
            continue
        relative = source.relative_to(data_dir)
        if any(part == ".env" or part.startswith(".env.") for part in relative.parts):
            continue
        if _is_sqlite_sidecar(source):
            # The online SQLite backup below already includes committed WAL data.
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if _is_sqlite_database(source):
            _copy_sqlite_database(source, destination)
        else:
            shutil.copy2(source, destination)
        archive_name = (Path("data") / relative).as_posix()
        files.append((archive_name, sha256_file(destination)))
    return files


def _docker_redis_dump(compose_file: Path, service: str) -> bytes:
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "exec",
                "-T",
                service,
                "redis-cli",
                "--rdb",
                "-",
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("无法通过 Docker 获取 Redis 快照") from exc
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("Redis 快照命令失败，请确认 redis 服务正在运行")
    return result.stdout


def create_backup(
    data_dir: str | Path,
    output: str | Path,
    *,
    compose_file: str | Path = "compose.yaml",
    redis_service: str = "redis",
    include_redis: bool = True,
    redis_snapshot: str | Path | None = None,
    force: bool = False,
    purpose: str = "manual",
) -> Path:
    data_path = Path(data_dir).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    compose_path = Path(compose_file).expanduser().resolve()
    if output_path.exists() and not force:
        raise FileExistsError(f"备份文件已存在：{output_path}，如需覆盖请使用 --force")
    if output_path == data_path or data_path in output_path.parents:
        raise ValueError("备份输出不能位于 data/ 目录内")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".oopz-backup-", dir=output_path.parent) as name:
        stage = Path(name)
        files = _copy_data(data_path, stage, output_path)
        redis_status = "excluded"
        if include_redis:
            if redis_snapshot is not None:
                redis_path = Path(redis_snapshot).expanduser().resolve()
                if not redis_path.is_file():
                    raise ValueError(f"Redis 快照文件不存在：{redis_path}")
                redis_bytes = redis_path.read_bytes()
                redis_status = "provided-file"
            else:
                redis_bytes = _docker_redis_dump(compose_path, redis_service)
                redis_status = "docker-compose-exec"
            redis_target = stage / "redis" / "dump.rdb"
            redis_target.parent.mkdir(parents=True, exist_ok=True)
            redis_target.write_bytes(redis_bytes)
            files.append(("redis/dump.rdb", sha256_bytes(redis_bytes)))

        checksum_text = "".join(f"{digest}  {path}\n" for path, digest in sorted(files))
        checksum_path = stage / "checksums.sha256"
        checksum_path.write_text(checksum_text, encoding="utf-8", newline="\n")
        manifest = {
            "format_version": ARCHIVE_FORMAT_VERSION,
            "purpose": purpose,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "git_commit": _git_value("rev-parse", "HEAD"),
            "compose_file": compose_path.name,
            "compose_sha256": _compose_digest(compose_path),
            "image_ids": _image_manifest(compose_path, redis_service),
            "data_dir": data_path.name,
            "env_included": False,
            "redis": {"status": redis_status, "path": "redis/dump.rdb" if include_redis else None},
            "files": [{"path": path, "sha256": digest} for path, digest in sorted(files)],
            "checksums_sha256": sha256_file(checksum_path),
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        archive_fd, archive_name = tempfile.mkstemp(
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_path.parent,
        )
        os.close(archive_fd)
        temporary_archive = Path(archive_name)
        os.chmod(temporary_archive, 0o600)
        try:
            with zipfile.ZipFile(
                temporary_archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                for source in sorted(stage.rglob("*")):
                    if source.is_file():
                        archive.write(source, source.relative_to(stage).as_posix())
            os.replace(temporary_archive, output_path)
        finally:
            temporary_archive.unlink(missing_ok=True)
    os.chmod(output_path, 0o600)
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="备份 OOPZ data/ 和 Redis 持久化快照")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output", required=True, help="输出 .zip 路径")
    parser.add_argument("--compose-file", default="compose.yaml")
    parser.add_argument("--redis-service", default="redis")
    parser.add_argument("--redis-snapshot", help="仅测试或已由受控流程导出的 RDB 文件")
    parser.add_argument("--skip-redis", action="store_true", help="明确跳过 Redis，不代表完整备份")
    parser.add_argument("--force", action="store_true", help="明确允许覆盖同名备份")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        output = create_backup(
            args.data_dir,
            args.output,
            compose_file=args.compose_file,
            redis_service=args.redis_service,
            include_redis=not args.skip_redis,
            redis_snapshot=args.redis_snapshot,
            force=args.force,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"备份失败：{exc}")
        return 1
    print(f"备份已创建：{output}")
    print("范围：data/" + ("、Redis 快照" if not args.skip_redis else "（未包含 Redis）"))
    print("默认不包含 .env；归档文件权限已限制为当前用户可读写。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
