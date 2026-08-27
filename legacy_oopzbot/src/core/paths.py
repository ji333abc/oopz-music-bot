"""
项目路径的唯一来源。
"""

from __future__ import annotations

import os
from pathlib import Path

# core/paths.py 位于 <root>/src/core/paths.py：parents[0]=core, [1]=src, [2]=<root>
PROJECT_ROOT_PATH: Path = Path(__file__).resolve().parents[2]

# 字符串形式（项目内多数调用使用 os.path.join，习惯用 str）
PROJECT_ROOT: str = str(PROJECT_ROOT_PATH)

SRC_DIR: str = str(PROJECT_ROOT_PATH / "src")
RUNTIME_DATA_PATH: Path = Path(
    os.getenv("OOPZ_LEGACY_DATA_DIR") or PROJECT_ROOT_PATH / "data"
)
DATA_DIR: str = str(RUNTIME_DATA_PATH)
LOGS_DIR: str = str(RUNTIME_DATA_PATH / "logs")
PLUGINS_DIR: str = str(RUNTIME_DATA_PATH / "plugins")
CONFIG_PLUGINS_DIR: str = str(RUNTIME_DATA_PATH / "config" / "plugins")
WEB_ASSETS_DIR: str = str(PROJECT_ROOT_PATH / "src" / "web" / "assets")
LOG_FILE: str = str(RUNTIME_DATA_PATH / "logs" / "oopz_bot.log")


def project_path(*parts: str) -> str:
    """以项目根目录为基准拼接路径，返回字符串。"""
    return str(PROJECT_ROOT_PATH.joinpath(*parts))
