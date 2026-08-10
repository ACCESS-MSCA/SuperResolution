#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_DIR="${SCRIPT_DIR:h}"
EXTRA_ARGS=()

if [[ "${NDI_DIAGNOSTICS:-0}" == "1" ]]; then
    EXTRA_ARGS+=(--diagnostics)
fi

if [[ -n "${NDI_SOURCE_NAME:-}" ]]; then
    EXTRA_ARGS+=(--source-name "$NDI_SOURCE_NAME")
fi

cd "$REPO_DIR"

echo "[NDI] Starting default quality stream (metadata RX enabled)..."
echo "[NDI] Repo: $REPO_DIR"
python3 ./stream_video.py Videos/big_buck_bunny.mp4 "${EXTRA_ARGS[@]}"
