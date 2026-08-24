#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_DIR="${SCRIPT_DIR:h}"
LOWRES_VIDEO="Videos/test_360p24.mp4"
SOURCE_VIDEO="Videos/big_buck_bunny.mp4"
EXTRA_ARGS=()

if [[ "${NDI_DIAGNOSTICS:-0}" == "1" ]]; then
    EXTRA_ARGS+=(--diagnostics)
fi

if [[ -n "${NDI_SOURCE_NAME:-}" ]]; then
    EXTRA_ARGS+=(--source-name "$NDI_SOURCE_NAME")
fi

cd "$REPO_DIR"

source "$SCRIPT_DIR/_ndi_runtime.zsh"
ndi_prepare_python "$REPO_DIR" || exit 1

if [[ ! -f "$LOWRES_VIDEO" ]]; then
  echo "[NDI] LowRes file not found. Generating $LOWRES_VIDEO from $SOURCE_VIDEO ..."
  ffmpeg -y -i "$SOURCE_VIDEO" -vf "scale=640:360,fps=24" -c:v libx264 -preset veryfast -crf 23 -c:a aac -ar 48000 -ac 2 "$LOWRES_VIDEO"
fi

echo "[NDI] Starting lowres WAN profile stream (metadata RX enabled)..."
echo "[NDI] Repo: $REPO_DIR"
"$NDI_PYTHON" ./stream_video.py "$LOWRES_VIDEO" "${EXTRA_ARGS[@]}"
