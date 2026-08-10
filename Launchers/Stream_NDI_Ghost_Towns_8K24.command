#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"

# Wikimedia Commons source: 7680x4320 VP9/Opus at 23.836 fps.
export NDI_VIDEO_PATH="${NDI_VIDEO_PATH:-Videos/Ghost_Towns_in_8K_GoPro_be_Hero.webm}"
export NDI_SOURCE_NAME="${NDI_SOURCE_NAME:-StreamNDI}"
export NDI_DIAGNOSTICS="${NDI_DIAGNOSTICS:-1}"
export NDI_VIDEO_PREFETCH_FRAMES="${NDI_VIDEO_PREFETCH_FRAMES:-4}"
export NDI_PRELOAD_AUDIO="${NDI_PRELOAD_AUDIO:-1}"

exec "$SCRIPT_DIR/Stream_NDI_Default_8K.command"
