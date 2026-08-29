"""QQ 音乐扫码登录 / 凭证续期的 CLI 实现（``oopzbot qqmusic-login``）。

基于 qqmusic-api-python（https://github.com/L-1124/QQMusicApi）。
凭证保存到 ``QQ_MUSIC_CREDENTIAL_FILE``（默认 ``data/qqmusic-credential.json``），
登录和刷新成功后都会同步派生 Cookie 状态文件并推送到各消费方。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, TextIO

from .qqmusic_credential import (
    CredentialStore,
    cookie_string,
    expiry_timestamp,
    extract_uin,
    publish_cookie,
    require_qqmusic_api,
)

LOGIN_TYPES = {"qq": "QQ", "wx": "WX", "mobile": "MOBILE"}


def _finder_score(matrix: list[list[bool]], left: int, top: int) -> tuple[int, int]:
    """Return matches/total for one standard 7x7 QR finder pattern."""
    matches = 0
    total = 0
    for y in range(7):
        for x in range(7):
            expected = x in {0, 6} or y in {0, 6} or (2 <= x <= 4 and 2 <= y <= 4)
            matches += matrix[top + y][left + x] == expected
            total += 1
    return matches, total


def _decode_qr_modules(data: bytes | BinaryIO) -> list[list[bool]]:
    """Recover the logical module grid from a rendered QR image.

    QQMusicApi exposes the QR code as image bytes. Sampling candidate QR sizes and
    checking their three finder patterns avoids printing hundreds of source-image
    pixels in the terminal while keeping every logical module intact.
    """
    from PIL import Image

    source = BytesIO(data) if isinstance(data, bytes) else data
    with Image.open(source) as opened:
        image = opened.convert("L")

    low, high = image.getextrema()
    if low == high:
        raise ValueError("二维码图片没有黑白对比")
    threshold = (low + high) // 2
    dark_mask = image.point(lambda value: 255 if value < threshold else 0)
    bbox = dark_mask.getbbox()
    if bbox is None:
        raise ValueError("二维码图片中没有深色像素")

    left, top, right, bottom = bbox
    width = right - left
    height = bottom - top
    candidates: list[tuple[float, list[list[bool]]]] = []

    # QR versions 1..40 contain 21, 25, ... 177 modules per side.
    for size in range(21, 178, 4):
        matrix = []
        for y in range(size):
            py = min(bottom - 1, top + int((y + 0.5) * height / size))
            row = []
            for x in range(size):
                px = min(right - 1, left + int((x + 0.5) * width / size))
                row.append(image.getpixel((px, py)) < threshold)
            matrix.append(row)

        matched = total = 0
        for finder_left, finder_top in ((0, 0), (size - 7, 0), (0, size - 7)):
            finder_matched, finder_total = _finder_score(matrix, finder_left, finder_top)
            matched += finder_matched
            total += finder_total

        # Timing patterns provide a useful tie-breaker for high-version codes.
        for offset in range(8, size - 8):
            expected = offset % 2 == 0
            matched += matrix[6][offset] == expected
            matched += matrix[offset][6] == expected
            total += 2
        candidates.append((matched / total, matrix))

    score, matrix = max(candidates, key=lambda candidate: candidate[0])
    if score < 0.85:
        raise ValueError("无法识别二维码模块网格")
    return matrix


def _print_terminal_qr(data: bytes, stream: TextIO | None = None) -> None:
    """Print a scan-friendly QR code with terminal background colours."""
    output = stream or sys.stdout
    matrix = _decode_qr_modules(data)
    quiet_zone = 4
    white = "\x1b[107m"
    black = "\x1b[40m"
    reset = "\x1b[0m"
    padded_width = len(matrix) + quiet_zone * 2

    print("\n终端二维码（请直接扫码）：", file=output)
    white_row = f"{white}{'  ' * padded_width}{reset}"
    for _ in range(quiet_zone):
        print(white_row, file=output)
    for row in matrix:
        parts = [white, "  " * quiet_zone]
        active_colour = white
        for dark in row:
            colour = black if dark else white
            if colour != active_colour:
                parts.append(colour)
                active_colour = colour
            parts.append("  ")
        if active_colour != white:
            parts.append(white)
        parts.extend(("  " * quiet_zone, reset))
        print("".join(parts), file=output)
    for _ in range(quiet_zone):
        print(white_row, file=output)
    print(file=output)


def _fmt_ts(ts: float) -> str:
    if not ts or ts <= 0:
        return "未知"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _print_summary(summary: dict) -> None:
    labels = {
        "ok": "登录态有效",
        "expiring": "即将到期，等待自动刷新",
        "expired": "musickey 已过期，等待刷新校验",
        "missing": "没有凭证",
    }
    print(f"  凭证文件   : {summary.get('credential_file')}")
    if not summary.get("has_credential"):
        print(f"  状态       : {labels.get(summary.get('state'), summary.get('state'))}")
        return
    print(f"  uin        : {summary.get('uin') or '未知'}")
    print(f"  登录类型   : {summary.get('login_type')} (1=微信 2=QQ 6=APP扫码)")
    print(f"  key 过期   : {_fmt_ts(summary.get('expires_at'))}")
    print(f"  保存时间   : {_fmt_ts(summary.get('saved_at'))}")
    print(f"  refresh_key: {'有' if summary.get('has_refresh_key') else '无'}")
    print(f"  状态       : {labels.get(summary.get('state'), summary.get('state'))}")


def _print_cookie_line(cookie: str) -> None:
    print("-" * 60)
    print("Cookie:")
    print(cookie)
    print("-" * 60)


def _credential_dict(credential) -> dict:
    return json.loads(credential.model_dump_json())


def cmd_login(args: argparse.Namespace, store: CredentialStore) -> int:
    qqmusic_api = require_qqmusic_api()
    from qqmusic_api.models.login import QRLoginType
    from qqmusic_api.modules.login_utils import QRCodeLoginSession

    login_type = getattr(QRLoginType, LOGIN_TYPES[args.type])

    if not args.force:
        loaded = store.load()
        if loaded is not None:
            credential, _ = loaded
            expires_at = expiry_timestamp(credential)
            if not expires_at or expires_at > time.time():
                print("已存在未过期的本地凭证。如需重新扫码请加 --force。")
                _print_summary(store.summary())
                return 0

    print(f"正在获取 {args.type.upper()} 登录二维码...")
    timeout_hint = args.timeout

    async def _login() -> dict:
        async with qqmusic_api.Client() as client:
            session = QRCodeLoginSession(
                client.login,
                login_type,
                timeout_seconds=float(args.timeout),
            )
            qr = await session.get_qrcode()
            qr_dir = store.path.parent / "qrcode"
            qr_path = qr.save(qr_dir)
            if qr_path is None:
                raise RuntimeError("获取二维码失败")
            print(f"二维码已保存: {qr_path.resolve()}")
            if not args.no_terminal_qr:
                try:
                    _print_terminal_qr(qr.data)
                except Exception as exc:
                    print(f"[警告] 终端二维码渲染失败({type(exc).__name__})，请打开已保存的图片扫码。")
            if not args.no_open and hasattr(os, "startfile"):
                os.startfile(qr_path)  # noqa: S606 - 用系统图片查看器打开
                print("已尝试用系统图片查看器打开二维码。")
            print(
                ">>> 请使用"
                f"{ {'qq': '手机QQ', 'wx': '微信', 'mobile': 'QQ音乐APP'}[args.type] }"
                f"扫码并确认(限时 {timeout_hint} 秒)"
            )
            credential = await session.wait_qrcode_login()
            return _credential_dict(credential)

    try:
        credential = asyncio.run(_login())
    except Exception as exc:
        message = getattr(exc, "message", None) or exc
        print(f"[失败] {message}，请重新运行 login。")
        return 2

    meta = store.save(credential, source="login")
    print("登录成功，凭证已保存。")
    _print_summary(store.summary())
    _print_cookie_line(meta.get("cookie") or cookie_string(credential))
    return 0


def cmd_status(args: argparse.Namespace, store: CredentialStore) -> int:
    del args
    summary = store.summary()
    print("QQ 音乐凭证状态:")
    _print_summary(summary)
    if not summary.get("has_credential"):
        print("请先运行: oopzbot qqmusic-login login")
        return 1

    qqmusic_api = require_qqmusic_api()
    loaded = store.load()
    assert loaded is not None
    credential, _ = loaded

    async def _check():
        cred = qqmusic_api.Credential.model_validate(credential)
        async with qqmusic_api.Client() as client:
            return await client.login.check_expired(cred)

    print("正在请求服务端校验登录态...")
    try:
        expired = asyncio.run(_check())
    except Exception as exc:
        print(f"[警告] 服务端校验失败({type(exc).__name__})，请稍后重试。")
        return 2
    if expired:
        print("服务端校验结果 : 已失效，请重新扫码 (oopzbot qqmusic-login login --force)")
        return 1
    print("服务端校验结果 : 登录态有效")
    return 0


def cmd_refresh(args: argparse.Namespace, store: CredentialStore) -> int:
    del args
    from .qqmusic_credential import refresh_and_publish

    result = asyncio.run(refresh_and_publish(store=store))
    print(result.message)
    if result.ok:
        _print_summary(store.summary())
        return 0
    if result.kind == "refresh_expired":
        print("请重新扫码: oopzbot qqmusic-login login --force")
        return 1
    if result.kind == "missing":
        return 1
    return 2


def cmd_cookie(args: argparse.Namespace, store: CredentialStore) -> int:
    del args
    loaded = store.load()
    if loaded is None:
        print("当前没有凭证，请先运行: oopzbot qqmusic-login login")
        return 1
    credential, _ = loaded
    cookie = cookie_string(credential)
    _print_cookie_line(cookie)
    _print_summary(store.summary())
    # 保持状态文件与凭证文件一致（例如手工放置 credential.json 的场景）。
    publish_cookie(
        cookie,
        uin=extract_uin(credential),
        expires_at=expiry_timestamp(credential),
        source="cli",
        state_path=store.state_path,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oopzbot qqmusic-login", description="QQ 音乐扫码登录 / Cookie 续期")
    add_actions(parser)
    return parser


def add_actions(parser: argparse.ArgumentParser) -> None:
    """Register the nested qqmusic-login actions on *parser*."""
    sub = parser.add_subparsers(dest="action", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--cred", default=None, help="凭证文件路径，默认读取 QQ_MUSIC_CREDENTIAL_FILE")

    p_login = sub.add_parser("login", help="扫码登录")
    p_login.add_argument("--type", choices=list(LOGIN_TYPES), default="qq")
    p_login.add_argument("--force", action="store_true", help="忽略已有有效凭证，强制重新扫码")
    p_login.add_argument("--no-open", action="store_true", help="不自动打开二维码图片")
    p_login.add_argument("--no-terminal-qr", action="store_true", help="不在终端显示二维码")
    p_login.add_argument("--timeout", type=int, default=180, help="扫码超时秒数")
    add_common(p_login)

    p_status = sub.add_parser("status", help="查看凭证状态")
    add_common(p_status)

    p_refresh = sub.add_parser("refresh", help="刷新凭证（可挂定时任务）")
    add_common(p_refresh)

    p_cookie = sub.add_parser("cookie", help="输出当前 Cookie 字符串")
    add_common(p_cookie)

def main(args: argparse.Namespace, env_file: str | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv(env_file or os.getenv("OOPZBOT_ENV_FILE", ".env"), override=False)
    store = CredentialStore(Path(args.cred) if getattr(args, "cred", None) else None)
    handlers = {
        "login": cmd_login,
        "status": cmd_status,
        "refresh": cmd_refresh,
        "cookie": cmd_cookie,
    }
    return handlers[args.action](args, store)
