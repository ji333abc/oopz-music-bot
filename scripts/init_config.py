"""Create a local .env from the public template without third-party packages."""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=".env")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    source = root / ".env.example"
    output = (root / args.output).resolve()
    if output.exists():
        raise SystemExit(f"配置文件已存在，未覆盖：{output}")

    token = secrets.token_urlsafe(32)
    content = source.read_text(encoding="utf-8").replace(
        "QQBOT_BRIDGE_TOKEN=",
        f"QQBOT_BRIDGE_TOKEN={token}",
        1,
    )
    output.write_text(content, encoding="utf-8", newline="\n")
    print(f"已创建 {output}")
    print("已生成随机内部 Token，请继续填写其他必填项。")


if __name__ == "__main__":
    main()
