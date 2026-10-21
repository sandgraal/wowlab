#!/usr/bin/env bash
# Usage: make_sandbox.sh <name> [overlay]   overlay: macos-70058 | macos-70425 | macos-70450
# Builds a synthetic install from the real scrubbed fixtures plus isolated user dirs.
# Prints env exports; use:  eval "$(bash make_sandbox.sh <name>)"
set -euo pipefail
REPO=/home/user/wowlab
FIX=$REPO/lab/core/tests/fixtures
EVAL="$(cd "$(dirname "$0")" && pwd)"
NAME=${1:?name}; OVERLAY=${2:-}
SB=$EVAL/sandboxes/$NAME
if [ -e "$SB" ]; then echo "sandbox exists: $SB" >&2; exit 1; fi
ROOT="$SB/World of Warcraft"
mkdir -p "$ROOT" "$SB/home" "$SB/xdg/data" "$SB/xdg/config" "$SB/xdg/cache" "$SB/xdg/state"
cp -p "$FIX/macos/.build.info" "$ROOT/.build.info"
cp -Rp "$FIX/macos/forever" "$ROOT/_classic_beta_"
if [ -n "$OVERLAY" ]; then
  if [ -f "$FIX/$OVERLAY/.build.info" ]; then cp -p "$FIX/$OVERLAY/.build.info" "$ROOT/.build.info"; fi
  cp -Rp "$FIX/$OVERLAY/forever/." "$ROOT/_classic_beta_/"
fi
cat <<OUT
export WOWLAB_WOW_ROOT="$ROOT"
export HOME="$SB/home"
export XDG_DATA_HOME="$SB/xdg/data" XDG_CONFIG_HOME="$SB/xdg/config" XDG_CACHE_HOME="$SB/xdg/cache" XDG_STATE_HOME="$SB/xdg/state"
export UV_CACHE_DIR=/root/.cache/uv
export PATH="/root/.local/bin:\$PATH"
alias wowlab='uv run --project /home/user/wowlab wowlab'
OUT
