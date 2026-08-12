#!/usr/bin/env sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH:-"$project_dir/.playwright"}
export PLAYWRIGHT_BROWSERS_PATH

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "."
python -m playwright install chromium

if [ "${OOPZBOT_INSTALL_JM:-0}" = "1" ]; then
  python -m pip install ".[jm]"
  npm ci --omit=dev --prefix tools/qqbot-uploader
fi

if [ ! -f .env ]; then
  oopzbot init
fi

printf '%s\n' '安装完成。编辑 .env 后执行：'
printf '%s\n' '  .venv/bin/oopzbot check'
printf '%s\n' '  .venv/bin/oopzbot start'
