#!/usr/bin/env sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$project_dir"

with_jm=0
with_jm_set=0
with_qqmusic_login=1
qqmusic_login_set=0
skip_browser=0
browser_set=0
non_interactive=0
managed_music=1
music_mode_set=0
python_cmd=${PYTHON:-python3}

usage() {
  cat <<'EOF'
用法：sh install.sh [选项]

选项：
  --with-jm       安装可选 JM 文件任务及 Node.js 上传器
  --without-jm    仅安装音乐机器人
  --without-qqmusic-login  不安装扫码登录和自动 Cookie 续期组件
  --skip-browser  跳过 Playwright Chromium 下载
  --external-music-api  不安装固定版本 QQ 音乐 API，使用已有服务
  --managed-music-api   安装并自动托管固定版本 QQ 音乐 API（默认）
  --non-interactive  使用默认选项，不启动配置向导
  --python PATH   指定 Python 可执行文件
  -h, --help      显示帮助
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --with-jm)
      with_jm=1
      with_jm_set=1
      ;;
    --without-jm)
      with_jm=0
      with_jm_set=1
      ;;
    --without-qqmusic-login)
      with_qqmusic_login=0
      qqmusic_login_set=1
      ;;
    --skip-browser)
      skip_browser=1
      browser_set=1
      ;;
    --external-music-api)
      managed_music=0
      music_mode_set=1
      ;;
    --managed-music-api)
      managed_music=1
      music_mode_set=1
      ;;
    --non-interactive)
      non_interactive=1
      ;;
    --python)
      shift
      [ "$#" -gt 0 ] || { printf '%s\n' '错误：--python 需要一个路径。' >&2; exit 2; }
      python_cmd=$1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf '错误：未知选项 %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

confirm() {
  question=$1
  default=$2
  while true; do
    if [ "$default" = "yes" ]; then
      printf '%s [Y/n]：' "$question"
    else
      printf '%s [y/N]：' "$question"
    fi
    IFS= read -r answer || answer=
    case "$answer" in
      y|Y|yes|YES|是) return 0 ;;
      n|N|no|NO|否) return 1 ;;
      '') [ "$default" = "yes" ] && return 0 || return 1 ;;
      *) printf '%s\n' '请输入 y 或 n。' ;;
    esac
  done
}

interactive=0
if [ "$non_interactive" -eq 0 ] && [ -t 0 ]; then
  interactive=1
  printf '\n%s\n' '=== OOPZ Music Bot 安装向导 ==='
  if [ "$with_jm_set" -eq 0 ]; then
    if confirm '是否安装可选 JM 文件任务？' no; then
      with_jm=1
    fi
  fi
  if [ "$qqmusic_login_set" -eq 0 ]; then
    if ! confirm '是否安装 QQ 音乐扫码登录与自动 Cookie 续期组件？' yes; then
      with_qqmusic_login=0
    fi
  fi
  if [ "$browser_set" -eq 0 ]; then
    if ! confirm '是否下载机器人语音播放所需的 Chromium？' yes; then
      skip_browser=1
    fi
  fi
  if [ "$music_mode_set" -eq 0 ]; then
    if ! confirm '是否安装与本项目适配的固定版本 QQ 音乐 API？' yes; then
      managed_music=0
    fi
  fi
  printf '\n%s\n' '即将开始安装。现有 .env 和虚拟环境会被保留。'
fi

if ! command -v "$python_cmd" >/dev/null 2>&1; then
  printf '错误：找不到 Python：%s\n' "$python_cmd" >&2
  printf '%s\n' '请安装 Python 3.11+，或使用 --python 指定路径。' >&2
  exit 1
fi

if ! "$python_cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  version=$("$python_cmd" --version 2>&1 || true)
  printf '错误：需要 Python 3.11+，当前为 %s\n' "$version" >&2
  exit 1
fi

if [ "$with_jm" -eq 1 ] || [ "$managed_music" -eq 1 ]; then
  command -v node >/dev/null 2>&1 || { printf '%s\n' '错误：默认音乐 API 和 JM 扩展需要 Node.js 18+。' >&2; exit 1; }
  command -v npm >/dev/null 2>&1 || { printf '%s\n' '错误：默认音乐 API 和 JM 扩展需要 npm。' >&2; exit 1; }
  if ! node -e 'const major=Number(process.versions.node.split(".")[0]); process.exit(major >= 18 ? 0 : 1)'; then
    printf '错误：需要 Node.js 18+，当前为 %s。\n' "$(node --version)" >&2
    exit 1
  fi
fi
if [ "$managed_music" -eq 1 ]; then
  command -v git >/dev/null 2>&1 || { printf '%s\n' '错误：安装固定版本 QQ 音乐 API 需要 Git。' >&2; exit 1; }
fi

printf '%s\n' '[1/6] 创建 Python 虚拟环境'
if [ ! -x .venv/bin/python ]; then
  "$python_cmd" -m venv .venv
fi
venv_python="$project_dir/.venv/bin/python"
venv_oopzbot="$project_dir/.venv/bin/oopzbot"

printf '%s\n' '[2/6] 安装 Python 依赖'
"$venv_python" -m pip install --upgrade pip
extras=
[ "$with_jm" -eq 1 ] && extras=jm
if [ "$with_qqmusic_login" -eq 1 ]; then
  extras=${extras:+$extras,}qqmusic-login
fi
if [ -n "$extras" ]; then
  "$venv_python" -m pip install ".[$extras]"
else
  "$venv_python" -m pip install "."
fi

PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH:-"$project_dir/.playwright"}
export PLAYWRIGHT_BROWSERS_PATH
if [ "$skip_browser" -eq 0 ]; then
  printf '%s\n' '[3/6] 安装 Chromium'
  "$venv_python" -m playwright install chromium
else
  printf '%s\n' '[3/6] 已跳过 Chromium 安装'
fi

if [ "$managed_music" -eq 1 ]; then
  printf '%s\n' '[4/6] 安装固定版本 QQ 音乐 API'
  "$venv_python" scripts/install_qqmusic.py
else
  printf '%s\n' '[4/6] 使用外部音乐 API'
fi

if [ "$with_jm" -eq 1 ]; then
  printf '%s\n' '[5/6] 安装 JM 上传器依赖'
  npm ci --omit=dev --prefix tools/qqbot-uploader
else
  printf '%s\n' '[5/6] 未启用 JM 扩展'
fi

printf '%s\n' '[6/6] 配置机器人'
if [ "$managed_music" -eq 1 ]; then
  music_mode=managed
else
  music_mode=external
fi
if [ "$interactive" -eq 1 ]; then
  if [ "$with_jm" -eq 1 ]; then
    "$venv_python" scripts/configure.py --with-jm --music-mode "$music_mode"
  else
    "$venv_python" scripts/configure.py --music-mode "$music_mode"
  fi
  printf '%s\n' '正在检查配置……'
  if ! "$venv_oopzbot" check; then
    printf '%s\n' '配置尚未完整。补充 .env 后重新运行 oopzbot check 即可。'
  fi
elif [ ! -f .env ]; then
  "$venv_oopzbot" init
  "$venv_python" scripts/configure.py --music-mode "$music_mode" --set-music-mode-only
else
  printf '%s\n' '保留现有 .env。'
  "$venv_python" scripts/configure.py --music-mode "$music_mode" --set-music-mode-only
fi

cat <<EOF

安装完成。

1. 编辑配置：$project_dir/.env
2. 检查配置：$venv_oopzbot check
3. 查询频道：$venv_oopzbot discover
4. 启动机器人：$venv_oopzbot start
EOF
if [ "$with_jm" -eq 1 ]; then
  printf '%s\n' "5. 独立启动 JM worker：$project_dir/.venv/bin/oopzbot-jm-service（需要 Redis）"
fi
