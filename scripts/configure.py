"""Interactive configuration wizard for OOPZ Music Bot."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path


def read_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        values[key.strip()] = value
    return values


def encode_value(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@+,-]*", value):
        return value
    return json.dumps(value, ensure_ascii=False)


def write_values(template: Path, output: Path, values: dict[str, str]) -> None:
    source = output if output.exists() else template
    lines = source.read_text(encoding="utf-8").splitlines()
    written: set[str] = set()
    result: list[str] = []
    for line in lines:
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in values:
                result.append(f"{key}={encode_value(values[key])}")
                written.add(key)
                continue
        result.append(line)
    missing = [key for key in values if key not in written]
    if missing:
        result.extend(["", "# ---------- 安装向导补充 ----------"])
        result.extend(f"{key}={encode_value(values[key])}" for key in missing)
    output.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8", newline="\n")


def choose(question: str, options: list[str], default: int = 1) -> int:
    print(f"\n{question}")
    for index, option in enumerate(options, 1):
        suffix = "（默认）" if index == default else ""
        print(f"  {index}. {option}{suffix}")
    while True:
        answer = input(f"请选择 [默认 {default}]：").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer)
        print(f"请输入 1-{len(options)}。")


def confirm(question: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{question} [{hint}]：").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes", "是"}:
            return True
        if answer in {"n", "no", "否"}:
            return False
        print("请输入 y 或 n。")


def ask_text(
    label: str,
    values: dict[str, str],
    key: str,
    *,
    default: str = "",
    required: bool = False,
    hint: str = "",
) -> str:
    current = values.get(key, "") or default
    while True:
        suffix = f" [{current}]" if current else ""
        if hint:
            print(f"  提示：{hint}")
        answer = input(f"{label}{suffix}：").strip()
        result = answer or current
        if result or not required:
            values[key] = result
            return result
        print("此项不能为空。")


def ask_secret(
    label: str,
    values: dict[str, str],
    key: str,
    *,
    required: bool = False,
) -> str:
    current = values.get(key, "")
    while True:
        suffix = " [已配置，留空保留]" if current else ""
        answer = getpass.getpass(f"{label}{suffix}：").strip()
        result = answer or current
        if result or not required:
            values[key] = result
            return result
        print("此项不能为空。")


def find_oopzbot(root: Path) -> Path | None:
    candidates = [
        root / ".venv" / "Scripts" / "oopzbot.exe",
        root / ".venv" / "bin" / "oopzbot",
    ]
    return next((path for path in candidates if path.exists()), None)


def query_oopz(
    root: Path,
    env_path: Path,
    *,
    area_id: str = "",
    areas_only: bool = False,
) -> dict | None:
    executable = find_oopzbot(root)
    if executable is None:
        print("未找到虚拟环境中的 oopzbot，安装完成后可手动运行 discover。")
        return None
    clean_env = os.environ.copy()
    for key in (
        "OOPZ_LOGIN_METHOD",
        "OOPZ_LOGIN_PHONE",
        "OOPZ_LOGIN_PASSWORD",
        "OOPZ_DEVICE_ID",
        "OOPZ_PERSON_UID",
        "OOPZ_JWT_TOKEN",
    ):
        clean_env.pop(key, None)
    command = [str(executable), "--env-file", str(env_path), "discover", "--json"]
    if area_id:
        command.extend(["--area-id", area_id])
    if areas_only:
        command.append("--areas-only")
    result = subprocess.run(
        command,
        check=False,
        env=clean_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        if detail:
            print(f"OOPZ 查询失败：{detail[-1]}")
        return None
    for line in reversed(result.stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("areas"), list):
            return payload
    print("OOPZ 查询返回了无法识别的数据。")
    return None


def select_identifier(
    question: str,
    items: list[dict],
    *,
    current: str = "",
    kind: str = "",
) -> str:
    def item_id(item: dict) -> str:
        return str(
            item.get("id")
            or item.get("area_id")
            or item.get("areaId")
            or item.get("channel_id")
            or item.get("channelId")
            or ""
        ).strip()

    usable = [item for item in items if item_id(item)]
    if not usable:
        return input(f"{question}：").strip() or current
    options = []
    default = 1
    for index, item in enumerate(usable, 1):
        identifier = item_id(item)
        name = str(item.get("name") or "未命名").strip()
        item_type = str(item.get("type") or item.get("channelType") or kind).upper()
        type_text = f" · {item_type}" if item_type else ""
        options.append(f"{name}{type_text} · {identifier}")
        if identifier == current:
            default = index
    options.append("手动输入 ID")
    selected = choose(question, options, default=default)
    if selected == len(options):
        return input(f"请输入{question}：").strip() or current
    return item_id(usable[selected - 1])


def flatten_channels(groups: list[dict]) -> list[dict]:
    channels: list[dict] = []
    for group in groups:
        group_name = str(group.get("name") or "").strip()
        for channel in group.get("channels") or []:
            if not isinstance(channel, dict):
                continue
            item = dict(channel)
            if group_name and item.get("name"):
                item["name"] = f"{group_name} / {item['name']}"
            channels.append(item)
    return channels


def configure_oopz_targets(root: Path, env_path: Path, values: dict[str, str]) -> None:
    print("\n=== OOPZ 目标频道 ===")
    payload = query_oopz(root, env_path, areas_only=True)
    areas = payload.get("areas", []) if payload else []
    if areas:
        area_id = select_identifier(
            "选择 OOPZ 域",
            areas,
            current=values.get("QQBOT_OOPZ_AREA_ID", ""),
            kind="AREA",
        )
    else:
        print("无法自动读取域列表，请手动填写域 ID。")
        area_id = ask_text("OOPZ 域 ID", values, "QQBOT_OOPZ_AREA_ID")
    values["QQBOT_OOPZ_AREA_ID"] = area_id
    write_values(root / ".env.example", env_path, values)

    channel_payload = query_oopz(root, env_path, area_id=area_id) if area_id else None
    selected_area = (channel_payload.get("areas") or [{}])[0] if channel_payload else {}
    channels = flatten_channels(selected_area.get("groups") or [])
    voice_types = {"VOICE", "AUDIO"}
    voice_channels = [
        channel
        for channel in channels
        if str(channel.get("type") or channel.get("channelType") or "").upper()
        in voice_types
    ]
    text_channels = [
        channel
        for channel in channels
        if str(channel.get("type") or channel.get("channelType") or "").upper()
        not in voice_types
    ]
    if channels:
        values["QQBOT_OOPZ_TEXT_CHANNEL_ID"] = select_identifier(
            "选择播放通知文字频道",
            text_channels,
            current=values.get("QQBOT_OOPZ_TEXT_CHANNEL_ID", ""),
            kind="TEXT",
        )
        values["QQBOT_OOPZ_VOICE_CHANNEL_ID"] = select_identifier(
            "选择音频推送语音频道",
            voice_channels,
            current=values.get("QQBOT_OOPZ_VOICE_CHANNEL_ID", ""),
            kind="VOICE",
        )
    else:
        print("无法自动读取该域的频道，请手动填写频道 ID。")
        ask_text("播放通知文字频道 ID", values, "QQBOT_OOPZ_TEXT_CHANNEL_ID")
        ask_text("音频推送语音频道 ID", values, "QQBOT_OOPZ_VOICE_CHANNEL_ID")


def apply_music_mode(values: dict[str, str], mode: str) -> None:
    if mode == "managed":
        values.update(
            QQ_MUSIC_ENABLED="true",
            QQ_MUSIC_MANAGED="true",
            QQ_MUSIC_BASE_URL="http://127.0.0.1:3200",
            QQ_MUSIC_SERVICE_DIR=".services/qqmusic-api",
        )
    else:
        values.update(QQ_MUSIC_ENABLED="true", QQ_MUSIC_MANAGED="false")


def configure(
    root: Path,
    env_path: Path,
    *,
    with_jm: bool,
    music_mode: str | None = None,
) -> int:
    template = root / ".env.example"
    if not template.exists():
        raise SystemExit(f"找不到配置模板：{template}")

    values = read_values(env_path)
    if env_path.exists():
        action = choose(
            f"检测到已有配置：{env_path}",
            ["保留现有配置并结束", "逐项检查和修改配置"],
            default=1,
        )
        if action == 1:
            if music_mode:
                apply_music_mode(values, music_mode)
                write_values(template, env_path, values)
            print("已保留现有配置。")
            return 0

    print("\n=== QQ 官方机器人 ===")
    ask_text("QQ Bot App ID", values, "QQBOT_APP_ID", required=True)
    ask_secret("QQ Bot App Secret", values, "QQBOT_APP_SECRET", required=True)
    ask_text(
        "允许使用机器人的群 OpenID（多个用逗号分隔，可留空）",
        values,
        "QQBOT_ALLOWED_GROUP_OPENIDS",
    )
    if not values.get("QQBOT_BRIDGE_TOKEN"):
        values["QQBOT_BRIDGE_TOKEN"] = secrets.token_urlsafe(32)

    print("\n=== OOPZ 登录 ===")
    credential_default = 2 if all(
        values.get(key) for key in ("OOPZ_DEVICE_ID", "OOPZ_PERSON_UID", "OOPZ_JWT_TOKEN")
    ) else 1
    login_method = choose(
        "选择 OOPZ 登录方式",
        ["手机号和密码", "已有 Device ID、Person UID 和 JWT Token"],
        default=credential_default,
    )
    if login_method == 1:
        values["OOPZ_LOGIN_METHOD"] = "auto"
        ask_text("OOPZ 登录手机号", values, "OOPZ_LOGIN_PHONE", required=True)
        ask_secret("OOPZ 登录密码", values, "OOPZ_LOGIN_PASSWORD", required=True)
        values.update(OOPZ_DEVICE_ID="", OOPZ_PERSON_UID="", OOPZ_JWT_TOKEN="")
    else:
        ask_text("OOPZ Device ID", values, "OOPZ_DEVICE_ID", required=True)
        ask_text("OOPZ Person UID", values, "OOPZ_PERSON_UID", required=True)
        ask_secret("OOPZ JWT Token", values, "OOPZ_JWT_TOKEN", required=True)
        values.update(OOPZ_LOGIN_PHONE="", OOPZ_LOGIN_PASSWORD="")

    write_values(template, env_path, values)
    if confirm("现在连接 OOPZ 查询域和频道 ID 吗？", default=True):
        print("\n正在连接 OOPZ 并查询域与频道……")
        configure_oopz_targets(root, env_path, values)
    else:
        print("\n=== OOPZ 目标频道 ===")
        ask_text("OOPZ 域 ID", values, "QQBOT_OOPZ_AREA_ID")
        ask_text("播放通知文字频道 ID", values, "QQBOT_OOPZ_TEXT_CHANNEL_ID")
        ask_text("音频推送语音频道 ID", values, "QQBOT_OOPZ_VOICE_CHANNEL_ID")

    print("\n=== 旧版 OOPZ 语音核心 ===")
    ask_text(
        "OOPZ Agora App ID（旧 config.py 中的 agora_app_id）",
        values,
        "OOPZ_AGORA_APP_ID",
        required=True,
    )

    print("\n=== 音乐接口 ===")
    if music_mode is None:
        managed_default = 1 if values.get("QQ_MUSIC_MANAGED", "true") == "true" else 2
        selected = choose(
            "选择音乐 API",
            ["安装脚本提供的固定兼容版本", "已有的外部音乐 API"],
            default=managed_default,
        )
        music_mode = "managed" if selected == 1 else "external"
    apply_music_mode(values, music_mode)
    if music_mode == "managed":
        print("使用安装脚本提供的固定版本 QQ 音乐 API（本机 127.0.0.1:3200）。")
    else:
        ask_text(
            "外部音乐 API 地址",
            values,
            "QQ_MUSIC_BASE_URL",
            required=True,
            hint="必须兼容 getSearchByKey/getMusicPlay/getSongInfo/getLyric",
        )
    ask_secret("音乐 Cookie（可留空）", values, "QQ_MUSIC_COOKIE")
    qualities = ["320", "128", "flac", "ape", "m4a"]
    current_quality = values.get("QQ_MUSIC_QUALITY", "320").lower()
    quality_default = qualities.index(current_quality) + 1 if current_quality in qualities else 1
    quality = choose(
        "选择默认音质",
        ["320 kbps", "128 kbps", "FLAC", "APE", "M4A"],
        default=quality_default,
    )
    values["QQ_MUSIC_QUALITY"] = qualities[quality - 1]
    values.setdefault("QQ_MUSIC_FALLBACK_QUALITY", "128")

    values["QQBOT_JM_ENABLED"] = "true" if with_jm else "false"
    if with_jm:
        print("\n=== JM 文件任务 ===")
        ask_text(
            "允许使用 JM 任务的用户 OpenID（多个用逗号分隔）",
            values,
            "QQBOT_JM_ALLOWED_USER_OPENIDS",
        )

    write_values(template, env_path, values)
    os.chmod(env_path, 0o600)

    missing_channels = [
        key
        for key in (
            "QQBOT_OOPZ_AREA_ID",
            "QQBOT_OOPZ_TEXT_CHANNEL_ID",
            "QQBOT_OOPZ_VOICE_CHANNEL_ID",
            "OOPZ_AGORA_APP_ID",
        )
        if not values.get(key)
    ]
    print(f"\n配置已保存：{env_path}")
    if missing_channels:
        print("尚未填写完整的频道 ID。补充后运行 oopzbot check。")
    else:
        print("配置项已填写完成，安装程序将执行离线检查。")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="OOPZ Music Bot 交互式配置向导")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--with-jm", action="store_true")
    parser.add_argument("--music-mode", choices=("managed", "external"))
    parser.add_argument("--set-music-mode-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = root / env_path
    env_path = env_path.resolve()
    if args.set_music_mode_only:
        if not args.music_mode:
            raise SystemExit("--set-music-mode-only 需要 --music-mode")
        values = read_values(env_path)
        apply_music_mode(values, args.music_mode)
        write_values(root / ".env.example", env_path, values)
        raise SystemExit(0)
    if not sys.stdin.isatty():
        raise SystemExit("配置向导需要交互式终端。自动化部署请直接提供 .env。")
    raise SystemExit(
        configure(
            root,
            env_path,
            with_jm=args.with_jm,
            music_mode=args.music_mode,
        )
    )


if __name__ == "__main__":
    main()
