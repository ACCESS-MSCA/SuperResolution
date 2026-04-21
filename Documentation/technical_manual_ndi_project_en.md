# Complete Technical Manual - NDI LAN Streaming

Updated: 2026-04-21

## 1. Executive Summary

This project publishes an NDI source over LAN from a local video file, with synchronized audio and an optional dual mode for visual validation (secondary stream with animated overlay).

- Entry point: `stream_video.py`
- Helper module: `utils.py`
- Python dependencies: `cyndilib`, `numpy`
- External tools: `ffmpeg`, `ffprobe`

## 2. Structure and Components

| Path | Role | Notes |
|---|---|---|
| `stream_video.py` | Full orchestration: metadata, A/V decode, NDI send, pacing | Includes decoder failure recovery |
| `utils.py` | NDI sender creation and overlay | Encapsulates `VideoSendFrame` setup |
| `requirements.txt` | Python dependencies | Minimal runtime requirements |
| `Videos/` | Playback/test assets | Default input path |
| `Documentation/` | Technical manuals | HTML ES/EN + Markdown ES/EN |

## 3. Technical Stack

| Component | Actual usage | Reason |
|---|---|---|
| `cyndilib` | `Sender`, `VideoSendFrame`, `AudioSendFrame` | Clean NDI API for coherent video+audio sending |
| `numpy` | Frame/audio buffers and operations | Performance and memory control |
| `ffprobe` | Resolution/fps/frame-count metadata | Robust preflight metadata |
| `ffmpeg` | BGRA video decode + float32 audio decode | Stable multimedia compatibility |

Design note: the pipeline intentionally avoids `cv2` to prevent native media-library conflicts on macOS in some NDI driver setups.

## 4. Pipeline Architecture

```text
CLI -> parse args -> ffprobe metadata -> sender config (NDI)
    -> ffmpeg video raw BGRA pipe + ffmpeg audio f32le
    -> loop:
         BGRA frame
         audio block (channels,samples)
         write_video_and_audio()
         monotonic pacing
```

Data contracts:

- Video: `BGRA`, 8-bit/channel.
- Bytes per frame: `width * height * 4`.
- Audio: `float32`, shape `(channels, samples)`.
- Configured sample rate: 48 kHz, 2 channels.

## 5. Step-by-step Execution Flow

1. Parse CLI args and select input file.
2. Read metadata with `ffprobe`.
3. Configure the main NDI sender.
4. Compute `audio_samples_per_frame`.
5. Decode full audio into memory.
6. Start BGRA video decoder through pipe.
7. Per-frame loop: read video, slice audio, send NDI.
8. Restart decoder automatically if pipe fails.
9. Pace output using an accumulated monotonic clock.

## 6. Function Analysis (`stream_video.py`)

| Function | Lines | Responsibility |
|---|---|---|
| `_parse_fps` | 26-33 | FPS normalization with safe fallback |
| `_probe_video` | 35-90 | Metadata retrieval via `ffprobe` |
| `_start_video_decoder` | 92-115 | Starts looping `ffmpeg` BGRA raw decode |
| `_read_exact` | 117-128 | Exact byte reads for each frame |
| `_decode_audio_to_array` | 130-175 | Decodes audio to planar `float32` |
| `stream_video` | 177-331 | Setup, loop, A/V send, dual mode, cleanup |
| Main block | 334-340 | CLI entrypoint and default video path |

## 7. Function Analysis (`utils.py`)

| Function | Lines | Responsibility |
|---|---|---|
| `_pixelate_roi` | 10-16 | Simple pixelation by subsampling |
| `draw_square` | 19-43 | Animated overlay with sinusoidal motion |
| `make_sender` | 46-57 | NDI sender and frame format setup |

## 8. Timing and Synchronization

### 8.1 Video pacing

- Accumulated strategy: `next_frame_time += frame_duration`.
- On overrun, the loop re-syncs to `now` to prevent persistent drift.

### 8.2 Audio/frame relation

- `audio_samples_per_frame = round(sample_rate / fps)`.
- If fractional, the script logs a warning and uses fixed-size blocks.

### 8.3 Buffer integrity

- `np.frombuffer(...).copy()` ensures writable memory.
- Prevents: `ValueError: buffer source array is read-only`.

## 9. Dual Mode

With `--dual`, a second NDI source is created with suffix `-Square`.

- Main output: clean video.
- Secondary output: animated overlay.
- Both outputs can share the same per-frame audio block.

## 10. Operations

Install:

```bash
cd <project-root>
python3 -m pip install -r requirements.txt
```

Run:

```bash
python3 ./stream_video.py
python3 ./stream_video.py Videos/alhaja.mp4
python3 ./stream_video.py Videos/alhaja.mp4 --dual
```

Quick validation:

1. Open NDI Monitor (or another NDI receiver).
2. Validate main source visibility.
3. In dual mode, validate secondary source.
4. Confirm audio playback when the media has an audio track.

## 11. Advanced Troubleshooting

| Symptom | Likely cause | Recommended action |
|---|---|---|
| No NDI output | NDI runtime or network visibility issue | Verify runtime installation and same LAN segment |
| No audio | Missing ffmpeg or file has no audio track | Install ffmpeg and validate track |
| Jitter / stutter | CPU overload or timing overruns | Reduce source fps/resolution |
| Read-only buffer error | Immutable frame buffer | Use writable copy before send (already integrated) |
| Intermittent frame failures | Decoder pipe closed unexpectedly | Built-in automatic decoder restart |

## 12. Performance and Scalability

- Critical path: decode + per-frame NDI send.
- Dual mode increases overhead (copies/overlay/send).
- On limited hosts, prefer moderate bitrate/fps assets.

Recommendations:

- Use files with stable declared fps.
- Keep OS and NDI software stack updated.
- Monitor timing warnings in long sessions.

## 13. Extension Guide

1. Add new overlays in `utils.py`.
2. Add CLI modes in the `__main__` argument parsing block.
3. Add telemetry counters every N frames.
4. Scale to multiple sources with multiple synchronized senders.

## 14. Key Code References

| File | Lines | Key point |
|---|---|---|
| `stream_video.py` | 35-90 | Robust metadata preflight |
| `stream_video.py` | 130-175 | Audio decode to internal structure |
| `stream_video.py` | 248-331 | Main loop + recovery logic |
| `stream_video.py` | 271-273 | Writable buffer fix |
| `utils.py` | 46-57 | Sender and frame setup |

## 15. Validation Checklist

1. Python dependencies installed.
2. `ffmpeg`/`ffprobe` available in PATH.
3. Stable execution in normal mode.
4. Stable execution in `--dual` mode.
5. Audio and video visible in NDI receiver.

## 16. Technical Conclusion

The current implementation provides a stable and maintainable NDI LAN pipeline, with practical audio/video synchronization and automatic decoder-recovery behavior. This manual is intended to let any engineer operate, debug, and extend the system with low risk.
