"""Unified CLI and process supervisor."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import threading
import time
from importlib import resources
from pathlib import Path

from dotenv import load_dotenv


def _load_environment(path: str | None = None) -> Path:
    env_path = Path(path or os.getenv("OOPZBOT_ENV_FILE", ".env")).resolve()
    if env_path.is_file():
        env_path.chmod(0o600)
    load_dotenv(env_path, override=False)
    return env_path


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )


def check_config(env_file: str | None = None) -> int:
    _load_environment(env_file)
    from .config import clear_settings_cache, get_settings

    clear_settings_cache()
    settings = get_settings()
    errors = settings.validate()
    if settings.qq_music_enabled and settings.qq_music_managed:
        from .qqmusic_service import managed_installation_errors, service_directory

        errors.extend(
            managed_installation_errors(service_directory(settings.qq_music_service_dir))
        )
    if errors:
        print("配置检查失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print("配置检查通过")
    print(f"- 内部桥接：http://{settings.bridge_host}:{settings.bridge_port}")
    music_mode = "固定版本自动托管" if settings.qq_music_managed else "外部服务"
    print(f"- 音乐接口：{settings.qq_music_base_url}（{music_mode}）")
    print(f"- OOPZ 域：{settings.oopz_area_id}")
    return 0


def init_config(env_file: str | None = None) -> int:
    env_path = Path(env_file or ".env").resolve()
    if env_path.exists():
        print(f"配置文件已存在，未覆盖：{env_path}")
    else:
        example = Path(__file__).resolve().parents[1] / ".env.example"
        if example.exists():
            shutil.copyfile(example, env_path)
        else:
            template = resources.files("oopzbot").joinpath("env.example").read_text(
                encoding="utf-8"
            )
            env_path.write_text(template, encoding="utf-8", newline="\n")
    env_path.chmod(0o600)
    print(f"配置文件已就绪：{env_path}")
    load_dotenv(env_path, override=True)
    from .config import ensure_bridge_token

    ensure_bridge_token(env_path)
    print("已生成内部桥接 Token。请编辑其余必填项。")
    return 0


def discover_channels(
    env_file: str | None = None,
    *,
    area_id: str = "",
    areas_only: bool = False,
    json_output: bool = False,
) -> int:
    _load_environment(env_file)
    from .discovery import discovery_payload, print_discovery
    from .runtime import OopzRuntime

    runtime = OopzRuntime()
    try:
        runtime.start()
        payload = discovery_payload(runtime, area_id=area_id, areas_only=areas_only)
        if json_output:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print_discovery(payload)
        return 0 if payload["areas"] else 1
    finally:
        runtime.close()


def run(env_file: str | None = None) -> int:
    _load_environment(env_file)
    from .bridge import router, set_music_handler
    from .config import clear_settings_cache, get_settings
    from .controller import MusicController
    from .qqmusic_service import ManagedQQMusicService
    from .runtime import OopzRuntime

    clear_settings_cache()
    settings = get_settings()
    _configure_logging(settings.log_level)
    errors = settings.validate()
    if errors:
        raise SystemExit("配置无效：\n- " + "\n- ".join(errors))

    music_service = ManagedQQMusicService(settings)
    music_service.start()
    legacy_core_enabled = str(
        os.getenv("OOPZBOT_USE_LEGACY_CORE") or ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    if legacy_core_enabled:
        from .legacy_runtime import LegacyOopzCore

        runtime = LegacyOopzCore()
        try:
            controller = runtime.start()
        except Exception:
            runtime.close()
            music_service.close()
            raise
    else:
        runtime = OopzRuntime()
        try:
            runtime.start()
        except Exception:
            music_service.close()
            raise
        controller = MusicController(settings, runtime)
    set_music_handler(controller)

    import uvicorn
    from fastapi import FastAPI

    api = FastAPI(title="OOPZ Music Bot Internal API", docs_url=None, redoc_url=None)
    api.include_router(router)
    server = uvicorn.Server(
        uvicorn.Config(
            api,
            host=settings.bridge_host,
            port=settings.bridge_port,
            log_level=settings.log_level.lower(),
            access_log=False,
        )
    )

    def shutdown(*_args) -> None:
        server.should_exit = True
        if legacy_core_enabled:
            runtime.close()
        else:
            controller.close()
            runtime.close()
        music_service.close()

    api_thread = threading.Thread(target=server.run, name="internal-api", daemon=True)
    api_thread.start()
    deadline = time.monotonic() + 10
    while not server.started and api_thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        shutdown()
        raise RuntimeError(
            f"内部命令桥接启动失败：http://{settings.bridge_host}:{settings.bridge_port}"
        )

    signal.signal(signal.SIGTERM, shutdown)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, shutdown)

    from .qqbot import main as run_qqbot

    try:
        run_qqbot()
    finally:
        shutdown()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oopzbot", description="OOPZ Music Bot")
    parser.add_argument("--env-file", help="环境变量文件，默认读取 .env")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("start", help="启动机器人（默认命令）")
    subcommands.add_parser("init", help="生成 .env 和随机内部 Token")
    subcommands.add_parser("check", help="仅检查配置，不连接外部服务")
    discover = subcommands.add_parser("discover", help="登录 OOPZ 并列出域和频道 ID")
    discover.add_argument("--area-id", help="只查询指定域的频道")
    discover.add_argument("--areas-only", action="store_true", help="只列出已加入的域")
    discover.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "init":
        raise SystemExit(init_config(args.env_file))
    if args.command == "check":
        raise SystemExit(check_config(args.env_file))
    if args.command == "discover":
        raise SystemExit(
            discover_channels(
                args.env_file,
                area_id=args.area_id or "",
                areas_only=args.areas_only,
                json_output=args.json,
            )
        )
    raise SystemExit(run(args.env_file))


if __name__ == "__main__":
    main()
