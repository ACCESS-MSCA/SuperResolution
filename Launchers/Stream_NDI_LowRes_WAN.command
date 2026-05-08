#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_DIR="${SCRIPT_DIR:h}"
LOWRES_VIDEO="Videos/test_360p24.mp4"
SOURCE_VIDEO="Videos/big_buck_bunny.mp4"

cd "$REPO_DIR"

if [[ ! -f "$LOWRES_VIDEO" ]]; then
  echo "[NDI] LowRes file not found. Generating $LOWRES_VIDEO from $SOURCE_VIDEO ..."
  ffmpeg -y -i "$SOURCE_VIDEO" -vf "scale=640:360,fps=24" -c:v libx264 -preset veryfast -crf 23 -c:a aac -ar 48000 -ac 2 "$LOWRES_VIDEO"
fi

echo "[NDI] Starting lowres WAN profile stream (metadata RX enabled)..."
echo "[NDI] Repo: $REPO_DIR"
python3 ./stream_video.py "$LOWRES_VIDEO"
