#!/usr/bin/env sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "${OOPZBOT_INSTALL_JM:-0}" = "1" ]; then
  exec sh "$project_dir/install.sh" --with-jm "$@"
fi
exec sh "$project_dir/install.sh" "$@"
