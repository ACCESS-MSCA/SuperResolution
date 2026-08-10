#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"

# Public-domain U.S. Air Force footage (DVIDS 989435).
export NDI_VIDEO_PATH="${NDI_VIDEO_PATH:-Videos/operation_high_mast_8k30.mp4}"
export NDI_SOURCE_NAME="${NDI_SOURCE_NAME:-StreamNDI}"
export NDI_DIAGNOSTICS="${NDI_DIAGNOSTICS:-1}"
export NDI_VIDEO_PREFETCH_FRAMES="${NDI_VIDEO_PREFETCH_FRAMES:-4}"
export NDI_PRELOAD_AUDIO="${NDI_PRELOAD_AUDIO:-1}"

exec "$SCRIPT_DIR/Stream_NDI_Default_8K.command"
