#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_DIR="${SCRIPT_DIR:h}"

cd "$REPO_DIR"

echo "[NDI] Starting default quality stream (metadata RX enabled)..."
echo "[NDI] Repo: $REPO_DIR"
python3 ./stream_video.py Videos/big_buck_bunny.mp4
