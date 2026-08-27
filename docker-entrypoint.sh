#!/bin/sh
set -eu

if [ "$(id -u)" -eq 0 ]; then
  mkdir -p /app/data
  chown -R oopzbot:oopzbot /app/data
  chmod 0750 /app/data
  if ! gosu oopzbot test -w /app/data; then
    printf '%s\n' '错误：oopzbot 用户无法写入 /app/data，请检查宿主机目录权限。' >&2
    exit 1
  fi
  export HOME=/app
  export USER=oopzbot
  exec gosu oopzbot "$@"
fi

exec "$@"
