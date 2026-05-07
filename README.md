# Complete Technical Manual - NDI LAN Streaming

Updated: 2026-04-21

## 1. Executive Summary

This project publishes an NDI source over LAN from a local video file, with synchronized audio and an optional dual mode for visual validation (secondary stream with animated overlay).

- Streamer entry point: `stream_video.py`
- Receiver entry point: `receiver.py`
- Helper modules: `utils.py`, `ffmpeg.py`
- Python dependencies: `cyndilib`, `numpy`, `PyQt5`
- External tools: `ffmpeg`, `ffprobe`

## 2. Structure and Components

| Path | Role | Notes |
|---|---|---|
| `stream_video.py` | Full orchestration: metadata, A/V decode, NDI send, pacing | Includes decoder failure recovery |
| `receiver.py` | PyQt5 GUI for receiving and displaying any NDI source on the LAN | |
| `utils.py` | NDI sender creation and overlay | Encapsulates `VideoSendFrame` setup |
| `ffmpeg.py` | All ffmpeg/ffprobe subprocess interactions | Shared by streamer |
| `requirements.txt` | Python dependencies | Minimal runtime requirements |
| `Videos/` | Playback/test assets | Default input path |
| `Documentation/` | Technical manuals | HTML ES/EN + Markdown ES/EN |

## 3. Technical Stack

| Component | Actual usage | Reason |
|---|---|---|
| `cyndilib` | `Sender`, `VideoSendFrame`, `AudioSendFrame`, `Receiver`, `VideoRecvFrame`, `Finder` | NDI send and receive API |
| `numpy` | Frame/audio buffers and operations | Performance and memory control |
| `PyQt5` | GUI, threading (`QThread`), and image rendering (`QImage`/`QPixmap`) | Receiver UI only |
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

## 9. Receiver (`receiver.py`)

`receiver.py` is a standalone PyQt5 desktop application that discovers NDI sources on the LAN and displays the selected stream in a window.

### 9.1 Architecture

```text
NDIViewer (QMainWindow)
    ├── Finder (polled every 1 s via QTimer) → source list (QListWidget)
    └── on connect:
            Receiver + VideoRecvFrame
            ReceiveThread (QThread) → tight receive loop
                └── frame_ready signal → _on_frame → QLabel / QPixmap
```

### 9.2 Key classes

| Class | Responsibility |
|---|---|
| `ReceiveThread` | Calls `Receiver.receive()` in a tight loop on a background thread; emits `frame_ready(data, w, h)` to the UI thread |
| `NDIViewer` | Main window: source discovery, connect/disconnect, and frame rendering |

### 9.3 Behavior details

- **Source discovery** — `cyndilib.finder.Finder` is kept open for the lifetime of the application and polled every 1 second. The source list updates automatically as sources appear or disappear, preserving the current selection if the source is still present.
- **Color format** — `RecvColorFormat.BGRX_BGRA`; frames are rendered as `QImage.Format_ARGB32`.
- **Frame delivery** — `VideoRecvFrame.fill_p_data()` copies the frame into a pre-allocated `uint8` numpy buffer. The buffer is re-allocated only when the frame size changes. A `.copy()` is emitted via the signal so the receive thread can immediately reuse the buffer.
- **Window sizing** — the window auto-resizes to match the stream resolution on the first frame.
- **Cleanup** — `closeEvent` stops the receive thread, disconnects the receiver, stops the timer, and closes the finder to release all NDI resources cleanly.

### 9.4 Running the receiver

```bash
python3 receiver.py
```

1. The source list populates automatically with all NDI sources found on the LAN.
2. Select a source and click **Start Stream**.
3. The window resizes to the stream resolution and begins displaying frames.
4. Click **Stop Stream** to return to the source list, or **Exit** to quit.

## 10. Dual Mode

With `--dual`, a second NDI source is created with suffix `-Square`.

- Main output: clean video.
- Secondary output: animated overlay.
- Both outputs can share the same per-frame audio block.

## 11. Operations

Install:

```bash
cd <project-root>
python3 -m pip install -r requirements.txt
```

Run the streamer:

```bash
python3 ./stream_video.py
python3 ./stream_video.py Videos/alhaja.mp4
python3 ./stream_video.py Videos/alhaja.mp4 --dual
```

Run the receiver:

```bash
python3 ./receiver.py
```

Quick validation:

1. Start the streamer on one machine (or the same machine).
2. Open `receiver.py` — the source should appear in the list within ~1 second.
3. Select the source and click **Start Stream** to confirm video display.
4. In dual mode, both `StreamNDI` and `StreamNDI-Square` should appear in the list.
5. Confirm audio playback when the media has an audio track (use any NDI audio monitor).

## 12. Advanced Troubleshooting

| Symptom | Likely cause | Recommended action |
|---|---|---|
| No NDI output | NDI runtime or network visibility issue | Verify runtime installation and same LAN segment |
| No audio | Missing ffmpeg or file has no audio track | Install ffmpeg and validate track |
| Jitter / stutter | CPU overload or timing overruns | Reduce source fps/resolution |
| Read-only buffer error | Immutable frame buffer | Use writable copy before send (already integrated) |
| Intermittent frame failures | Decoder pipe closed unexpectedly | Built-in automatic decoder restart |
| Receiver source list empty | Finder not discovering sources | Confirm streamer is running and both hosts are on the same LAN segment |
| Receiver shows blank/corrupted frame | Color format mismatch | Receiver uses `BGRX_BGRA` + `Format_ARGB32`; do not change independently |
| Receiver window does not resize | Frame size not received on first frame | Stop and restart the stream |

## 13. Performance and Scalability

- Critical path: decode + per-frame NDI send.
- Dual mode increases overhead (copies/overlay/send).
- On limited hosts, prefer moderate bitrate/fps assets.
- The receiver renders every frame it receives on the UI thread via signals; high-fps sources (60 fps+) may tax the UI — consider skipping frames if needed.

Recommendations:

- Use files with stable declared fps.
- Keep OS and NDI software stack updated.
- Monitor timing warnings in long sessions.

## 14. Extension Guide

1. Add new overlays in `utils.py`.
2. Add CLI modes in the `__main__` argument parsing block.
3. Add telemetry counters every N frames.
4. Scale to multiple sources with multiple synchronized senders.
5. Extend `receiver.py` with audio receive by adding an `AudioRecvFrame` and a second signal.

## 15. Key Code References

| File | Lines | Key point |
|---|---|---|
| `stream_video.py` | 35-90 | Robust metadata preflight |
| `stream_video.py` | 130-175 | Audio decode to internal structure |
| `stream_video.py` | 248-331 | Main loop + recovery logic |
| `stream_video.py` | 271-273 | Writable buffer fix |
| `utils.py` | 46-57 | Sender and frame setup |
| `receiver.py` | 17-47 | `ReceiveThread` — background receive loop and signal emission |
| `receiver.py` | 49-165 | `NDIViewer` — source discovery, connect/disconnect, frame rendering |
| `receiver.py` | 104-116 | Source list refresh with selection preservation |
| `receiver.py` | 155-159 | Frame-to-`QImage` conversion (`Format_ARGB32`) |

## 16. Validation Checklist

1. Python dependencies installed (`cyndilib`, `numpy`).
2. `PyQt5` installed (receiver only).
3. `ffmpeg`/`ffprobe` available in PATH.
4. Stable streamer execution in normal mode.
5. Stable streamer execution in `--dual` mode.
6. NDI source visible in `receiver.py` source list.
7. Video renders correctly in the receiver window.
8. Receiver window resizes to match stream resolution.
9. Clean shutdown via **Stop Stream** and **Exit** releases NDI resources.

## 17. Technical Conclusion

The current implementation provides a stable and maintainable NDI LAN pipeline, with practical audio/video synchronization, automatic decoder-recovery behavior, and a GUI receiver for live stream monitoring. This manual is intended to let any engineer operate, debug, and extend the system with low risk.

