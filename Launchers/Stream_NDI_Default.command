#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"

# Public 1080p profile. Resolution is the only intentional difference from the
# 8K profile: transport, A/V timeline, audio preload, decode path, raw pixel
# format, diagnostics and ROI capability all use the same production contract.
export NDI_PROFILE_LABEL="${NDI_PROFILE_LABEL:-Default 1080p60}"
export NDI_VIDEO_PATH="${NDI_VIDEO_PATH:-Videos/big_buck_bunny.mp4}"
export NDI_SOURCE_NAME="${NDI_SOURCE_NAME:-StreamNDI}"
export NDI_ROI_FEEDBACK="${NDI_ROI_FEEDBACK:-0}"

exec "$SCRIPT_DIR/Stream_NDI_Default_8K.command"
