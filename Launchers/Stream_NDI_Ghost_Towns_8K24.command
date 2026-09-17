#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"

# Prefer the reproducible VideoToolbox/AAC production derivative when present.
# Keep the original Wikimedia 7680x4320 VP9/Opus source as a non-destructive
# fallback; both preserve the original 5959/250 cadence.
OPTIMIZED_VIDEO="Videos/Prod/Ghost_Towns_8K_UHD_23_836fps_HEVC_AAC.mp4"
ORIGINAL_VIDEO="Videos/Ghost_Towns_in_8K_GoPro_be_Hero.webm"
if [[ -z "${NDI_VIDEO_PATH:-}" ]]; then
    if [[ -f "$SCRIPT_DIR/../$OPTIMIZED_VIDEO" ]]; then
        export NDI_VIDEO_PATH="$OPTIMIZED_VIDEO"
    else
        export NDI_VIDEO_PATH="$ORIGINAL_VIDEO"
        print -u2 -- "[NDI] Ghost Town optimized master not found; using original VP9/Opus source."
    fi
fi
export NDI_PROFILE_LABEL="${NDI_PROFILE_LABEL:-Ghost Towns 8K24 reference}"
export NDI_SOURCE_NAME="${NDI_SOURCE_NAME:-StreamNDI}"
export NDI_DIAGNOSTICS="${NDI_DIAGNOSTICS:-1}"
export NDI_VIDEO_PREFETCH_FRAMES="${NDI_VIDEO_PREFETCH_FRAMES:-4}"
export NDI_PRELOAD_AUDIO="${NDI_PRELOAD_AUDIO:-1}"

exec "$SCRIPT_DIR/Stream_NDI_Default_8K.command"
