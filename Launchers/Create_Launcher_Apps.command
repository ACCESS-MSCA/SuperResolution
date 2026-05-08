#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_DIR="${SCRIPT_DIR:h}"
APPS_DIR="$SCRIPT_DIR/Apps"

mkdir -p "$APPS_DIR"

DEFAULT_APP="$APPS_DIR/Stream NDI Default.app"
LOWRES_APP="$APPS_DIR/Stream NDI LowRes WAN.app"

DEFAULT_CMD="$SCRIPT_DIR/Stream_NDI_Default.command"
LOWRES_CMD="$SCRIPT_DIR/Stream_NDI_LowRes_WAN.command"

osacompile -o "$DEFAULT_APP" <<OSA
on run
  tell application "Terminal"
    activate
    do script "cd " & quoted form of "$REPO_DIR" & "; " & quoted form of "$DEFAULT_CMD"
  end tell
end run
OSA

osacompile -o "$LOWRES_APP" <<OSA
on run
  tell application "Terminal"
    activate
    do script "cd " & quoted form of "$REPO_DIR" & "; " & quoted form of "$LOWRES_CMD"
  end tell
end run
OSA

echo "[NDI] Apps created:"
echo "  - $DEFAULT_APP"
echo "  - $LOWRES_APP"
