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

# Diagnostic low-resolution media, but the same transport/audio/runtime
# contract as every other public launcher. This is not a separate receiver-
# specific sender profile.
export NDI_PROFILE_LABEL="${NDI_PROFILE_LABEL:-LowRes 360p24 diagnostic}"
export NDI_VIDEO_PATH="${NDI_VIDEO_PATH:-$LOWRES_VIDEO}"
export NDI_SOURCE_NAME="${NDI_SOURCE_NAME:-StreamNDI}"
export NDI_ROI_FEEDBACK="${NDI_ROI_FEEDBACK:-0}"

exec "$SCRIPT_DIR/Stream_NDI_Default_8K.command"
