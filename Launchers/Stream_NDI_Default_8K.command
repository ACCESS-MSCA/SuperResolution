#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_DIR="${SCRIPT_DIR:h}"
VIDEO_PATH="${NDI_VIDEO_PATH:-Videos/Prod/NDI_AV_Sync_Test_002_8K_UHD_24fps_160Mbps_HEVC_AAC.mp4}"
VIDEO_PREFETCH_FRAMES="${NDI_VIDEO_PREFETCH_FRAMES:-4}"
AUDIO_PREROLL_MS="${NDI_AUDIO_PREROLL_MS:-0}"
ROI_FEEDBACK="${NDI_ROI_FEEDBACK:-0}"
VIDEO_PIXEL_FORMAT="${NDI_VIDEO_PIXEL_FORMAT:-nv12}"
VIDEO_HWACCEL="${NDI_VIDEO_HWACCEL:-videotoolbox}"
PROFILE_LABEL="${NDI_PROFILE_LABEL:-Default 8K quality}"
# The universal AVP/SIM/Monitor baseline uses one TCP connection. On the
# current AVP Wi-Fi path, RUDP overload can accumulate native receiver memory
# until visionOS kills the process; TCP backpressure keeps memory bounded.
export NDI_TRANSPORT="${NDI_TRANSPORT:-single-tcp}"
EXTRA_ARGS=()

EXTRA_ARGS+=(--video-prefetch-frames "$VIDEO_PREFETCH_FRAMES")
EXTRA_ARGS+=(--audio-preroll-ms "$AUDIO_PREROLL_MS")

case "${VIDEO_HWACCEL:l}" in
    none|off|software) VIDEO_HWACCEL="none" ;;
    videotoolbox)
        VIDEO_HWACCEL="videotoolbox"
        EXTRA_ARGS+=(--video-hwaccel videotoolbox)
        ;;
    *)
        print -u2 -- "[NDI] ERROR: NDI_VIDEO_HWACCEL debe ser none o videotoolbox."
        exit 1
        ;;
esac

if [[ "${NDI_PRELOAD_AUDIO:-1}" == "1" ]]; then
    EXTRA_ARGS+=(--preload-audio)
fi

if [[ "${NDI_DIAGNOSTICS:-1}" == "1" ]]; then
    EXTRA_ARGS+=(--diagnostics)
fi

if [[ -n "${NDI_SOURCE_NAME:-}" ]]; then
    EXTRA_ARGS+=(--source-name "$NDI_SOURCE_NAME")
fi

case "$ROI_FEEDBACK" in
    0) EXTRA_ARGS+=(--no-roi-feedback) ;;
    1) EXTRA_ARGS+=(--roi-feedback) ;;
    *)
        print -u2 -- "[NDI] ERROR: NDI_ROI_FEEDBACK debe ser 0 o 1."
        exit 1
        ;;
esac

case "${VIDEO_PIXEL_FORMAT:l}" in
    auto)
        if [[ "$ROI_FEEDBACK" == "1" ]]; then
            VIDEO_PIXEL_FORMAT="uyvy422"
        else
            VIDEO_PIXEL_FORMAT="auto"
        fi
        ;;
    uyvy|uyvy422) VIDEO_PIXEL_FORMAT="uyvy422" ;;
    nv12) VIDEO_PIXEL_FORMAT="nv12" ;;
    i420) VIDEO_PIXEL_FORMAT="i420" ;;
    bgra) VIDEO_PIXEL_FORMAT="bgra" ;;
    *)
        print -u2 -- "[NDI] ERROR: NDI_VIDEO_PIXEL_FORMAT debe ser auto, i420, nv12, uyvy422 o bgra."
        exit 1
        ;;
esac

if [[ "$ROI_FEEDBACK" == "1" && "$VIDEO_PIXEL_FORMAT" == "i420" ]]; then
    print -u2 -- "[NDI] ERROR: ROI necesita NV12, UYVY422 o BGRA; I420 queda reservado al modo ROI OFF."
    exit 1
fi

case "$VIDEO_PIXEL_FORMAT" in
    i420) EXTRA_ARGS+=(--i420) ;;
    nv12) EXTRA_ARGS+=(--nv12) ;;
    uyvy422) EXTRA_ARGS+=(--uyvy) ;;
    auto) EXTRA_ARGS+=(--auto-video-format) ;;
esac

cd "$REPO_DIR"

source "$SCRIPT_DIR/_ndi_runtime.zsh"

VIDEO_PATH="$(ndi_resolve_video "$REPO_DIR" "$VIDEO_PATH")" || {
    ndi_fail "Selecciona un vídeo existente o define NDI_VIDEO_PATH con su ruta."
    exit 1
}

ndi_prepare_transport "$REPO_DIR" || exit 1
ndi_prepare_python "$REPO_DIR" || exit 1
ndi_warn_hx_pyav_collision "$REPO_DIR"

echo "[NDI] Starting universal production stream: $PROFILE_LABEL"
echo "[NDI] Repo: $REPO_DIR"
echo "[NDI] Video: $VIDEO_PATH"
echo "[NDI] Video prefetch: $VIDEO_PREFETCH_FRAMES frames"
echo "[NDI] Audio preroll: $AUDIO_PREROLL_MS ms"
echo "[NDI] NDI pixels: ${VIDEO_PIXEL_FORMAT:u}"
echo "[NDI] Video decode: ${VIDEO_HWACCEL:u}"
echo "[NDI] Diagnostics: ${NDI_DIAGNOSTICS:-1}"
echo "[NDI] Audio preload: ${NDI_PRELOAD_AUDIO:-1}"
if [[ "$VIDEO_PIXEL_FORMAT" == "auto" ]]; then
    echo "[NDI] Pixel policy: I420 for 4:2:0 input; UYVY422 safe fallback"
fi
if [[ "$ROI_FEEDBACK" == "1" ]]; then
    echo "[NDI] ROI feedback: ON"
else
    echo "[NDI] ROI feedback: OFF"
fi

"$NDI_PYTHON" ./stream_video.py "$VIDEO_PATH" "${EXTRA_ARGS[@]}"
