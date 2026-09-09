#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
export NDI_ROI_FEEDBACK=1
exec "$SCRIPT_DIR/Stream_NDI_Default_8K.command"
