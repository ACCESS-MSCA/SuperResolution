#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_DIR="${SCRIPT_DIR:h}"
VIDEO_PATH="${NDI_VIDEO_PATH:-Videos/big_buck_bunny_8k30.mp4}"
VIDEO_PREFETCH_FRAMES="${NDI_VIDEO_PREFETCH_FRAMES:-4}"
EXTRA_ARGS=()

EXTRA_ARGS+=(--video-prefetch-frames "$VIDEO_PREFETCH_FRAMES")

if [[ "${NDI_PRELOAD_AUDIO:-1}" == "1" ]]; then
    EXTRA_ARGS+=(--preload-audio)
fi

if [[ "${NDI_DIAGNOSTICS:-0}" == "1" ]]; then
    EXTRA_ARGS+=(--diagnostics)
fi

if [[ -n "${NDI_SOURCE_NAME:-}" ]]; then
    EXTRA_ARGS+=(--source-name "$NDI_SOURCE_NAME")
fi

cd "$REPO_DIR"

echo "[NDI] Starting default quality stream (metadata RX enabled)..."
echo "[NDI] Repo: $REPO_DIR"
echo "[NDI] Video: $VIDEO_PATH"
echo "[NDI] Video prefetch: $VIDEO_PREFETCH_FRAMES frames"

if [[ ! -f "$VIDEO_PATH" ]]; then
    echo "[NDI] ERROR: video file not found: $REPO_DIR/$VIDEO_PATH" >&2
    echo "[NDI] Set NDI_VIDEO_PATH or update VIDEO_PATH in this launcher." >&2
    exit 1
fi

python3 ./stream_video.py "$VIDEO_PATH" --uyvy "${EXTRA_ARGS[@]}"
