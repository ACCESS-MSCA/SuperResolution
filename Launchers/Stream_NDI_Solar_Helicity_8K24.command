#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"

# NASA SVS source: 7680x4320 H.264/AAC at 24 fps.
export NDI_VIDEO_PATH="${NDI_VIDEO_PATH:-Videos/helicity_8k_twocolor.mp4}"
export NDI_SOURCE_NAME="${NDI_SOURCE_NAME:-StreamNDI}"
export NDI_DIAGNOSTICS="${NDI_DIAGNOSTICS:-1}"
export NDI_VIDEO_PREFETCH_FRAMES="${NDI_VIDEO_PREFETCH_FRAMES:-4}"
export NDI_PRELOAD_AUDIO="${NDI_PRELOAD_AUDIO:-1}"

exec "$SCRIPT_DIR/Stream_NDI_Default_8K.command"
