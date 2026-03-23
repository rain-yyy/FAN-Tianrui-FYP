#!/bin/bash
# 保存为 ~/fly-rsync-helper.sh
set -euo pipefail
MACHINE="$1"
shift
CMD=$(printf " %q" "$@")
fly ssh console --quiet --machine "$MACHINE" -C "$CMD"
