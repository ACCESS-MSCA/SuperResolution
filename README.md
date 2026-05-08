# Complete Technical Manual - NDI LAN Streaming

Updated: 2026-05-05

## 0. Documentation Navigation

### HTML

- ES (core): `Documentation/manual_tecnico_streaming_ndi_es.html`
- ES (visual architecture): `Documentation/arquitectura_visual_ndi_streaming.html`
- ES (deliverable section): `Documentation/Deliverable/ffmpeg_migration_decision_section_es.html`
- EN (technical manual): `Documentation/technical_manual_streaming_ndi_en.html`
- EN (deliverable section): `Documentation/Deliverable/ffmpeg_migration_decision_section_en.html`

### Markdown

- ES (technical manual): `Documentation/manual_tecnico_streaming_ndi_es.md`
- ES (deliverable section): `Documentation/Deliverable/ffmpeg_migration_decision_section_es.md`
- EN (technical manual): `Documentation/technical_manual_streaming_ndi_en.md`
- EN (deliverable section): `Documentation/Deliverable/ffmpeg_migration_decision_section_en.md`
- Deliverables folder: `Documentation/Deliverable/`

Linearity note:
- Every satellite document and deliverable should include a link back to the core manual (`manual_tecnico_streaming_ndi_es.html`) to preserve bidirectional navigation.

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
| `ffmpeg.py` | Media probing/decoding helpers | Isolates ffprobe/ffmpeg subprocess logic |
| `utils.py` | NDI sender creation and overlay | Encapsulates `VideoSendFrame` setup |
| `core/` | Runtime timing primitives | Contains monotonic frame clock |
| `extensions/backchannel/` | Generic metadata return-channel infrastructure | Receiver + dispatcher |
| `integrations/unity/` | Unity-specific metadata parsing/handling | Optional integration layer |
| `Launchers/` | macOS launcher scripts/apps | Default and lowres WAN profiles |
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
| `_configure_audio_frame` | 28-32 | Configures audio frame shape/rate for each sender |
| `stream_video` | 35-220 | Setup, loop, A/V send, dual mode, metadata RX, cleanup |
| Main block | 223-243 | CLI entrypoint and argument flags |

## 7. Function Analysis (`ffmpeg.py and utils.py`)

| Function | Lines | Responsibility |
|---|---|---|
| `_parse_fps` | 12-18 | FPS normalization with safe fallback |
| `probe_video` | 21-75 | Metadata retrieval via `ffprobe` |
| `start_video_decoder` | 78-100 | Starts looping `ffmpeg` BGRA raw decode |
| `read_exact` | 103-113 | Exact byte reads for each frame |
| `decode_audio_to_array` | 116-160 | Decodes audio to planar `float32` |

### utils.py

| Function | Lines | Responsibility |
|---|---|---|
| `_pixelate_roi` (`utils.py`) | 10-16 | Simple pixelation by subsampling |
| `draw_square` (`utils.py`) | 19-43 | Animated overlay with sinusoidal motion |
| `make_sender` (`utils.py`) | 46-57 | NDI sender and frame format setup |

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
python3 ./stream_video.py Videos/big_buck_bunny.mp4
python3 ./stream_video.py Videos/big_buck_bunny.mp4 --dual
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
| `ffmpeg.py` | 21-75 | Robust metadata preflight |
| `ffmpeg.py` | 116-160 | Audio decode to internal structure |
| `stream_video.py` | 131-220 | Main loop + recovery logic |
| `stream_video.py` | 149-150 | Writable buffer fix |
| `core/clock.py` | 8-33 | Monotonic pacing and overrun resync |
| `utils.py` | 46-57 | Sender and frame setup |

## 15. Validation Checklist

1. Python dependencies installed.
2. `ffmpeg`/`ffprobe` available in PATH.
3. Stable execution in normal mode.
4. Stable execution in `--dual` mode.
5. Audio and video visible in NDI receiver.

## 16. Technical Conclusion

The current implementation provides a stable and maintainable NDI LAN pipeline, with practical audio/video synchronization and automatic decoder-recovery behavior. This manual is intended to let any engineer operate, debug, and extend the system with low risk.


## 17. Architecture Decision: FFmpeg Migration

### Technical context

- The original implementation was functional, but specific macOS environments exposed a native-library conflict within the same process.
- OpenCV loaded one `libavdevice` variant while the NDI HX stack loaded another variant with overlapping Objective-C classes.
- This condition could lead to spurious casting failures, non-deterministic behavior, and crash risk.

### Decision taken

- Remove OpenCV from the runtime streamer path.
- Use `ffprobe`/`ffmpeg` for probing and decoding.
- Keep frame and overlay processing in `numpy`.

### Benefits

- Removal of the native conflict root cause.
- More predictable behavior for cross-organization integration.
- Preserved compatibility with `np.ndarray`-based downstream processing.
- Decode performance aligned with project targets (typically comparable or better, depending on host/load).

### Validation scope

The solution has been tested on macOS and Apple Vision Pro.


## 18. NDI Metadata Return Channel (Unity -> Server)

### Objective

This feature enables return communication from Unity (NDI receiver) back to this streamer (NDI sender) using NDI SDK XML metadata, without an extra WebSocket layer.

### Technical components

- Unity side (metadata sent from receiver):
  - `NdiReceiverMetadataSender`
  - `NdiTransformMetadataPayloadProvider`
  - `NdiMetadataInterop`
- Server side (metadata capture on sender):
  - `extensions/backchannel/receiver.py`
  - `extensions/backchannel/dispatcher.py`
  - `integrations/unity/parsers.py`
  - `integrations/unity/handlers.py`
  - `stream_video.py` integration

### Runtime flow

1. Unity receiver builds XML payload (`<access_transform ... />`).
2. Unity sends metadata with `NDIlib_recv_send_metadata`.
3. Python sender captures incoming metadata with `NDIlib_send_capture`.
4. Server parses XML, extracts transform data, and logs received events.

### CLI and logging modes (server)

- Enabled by default: `python3 stream_video.py`
- Raw XML logs: `python3 stream_video.py --rx-metadata-verbose`
- Legacy flag (no effect, kept for compatibility):
  `python3 stream_video.py --rx-metadata-log-all`
- Disable return channel:
  `python3 stream_video.py --no-rx-metadata`

Current default behavior:

- No periodic console spam when no metadata is received.
- Every received message is logged (no duplicate filtering on server side).
- Cadence/duplication control is handled on Unity side (metadata sender).

### Recommended log interpretation

- `[RX-META] ...`: raw XML metadata received.
- `[RX Transform] ...`: transform payload parsed successfully.
- `cap_meta` (final summary): number of metadata frames captured by `send_capture`.
- `received`: valid parsed messages queued by the backchannel module.

### Note on `connections`

`get_num_connections()` may report `2` even when you perceive a single client.
This can happen when one app opens multiple internal subscriptions to the same source (for example, separate internal paths/components in the receiver).
It does not necessarily mean two distinct physical devices.

### Validated state

- Feature validated in real Unity Editor -> Python server workflow.
- Confirmed reception of `access_client` and `access_transform`.


## 19. WAN Publication Validation (Public IP)

### Objective

Extend NDI publishing beyond LAN so receivers on external networks can consume the stream through public IP and router NAT rules.

### Key outcome

- No streamer code changes were required for this milestone (`stream_video.py` remained unchanged).
- WAN publication was achieved through network configuration (router/firewall/receiver).

### Validated host setup

- Host public IP: `85.52.8.159`
- Host LAN IP (sender Mac): `192.168.1.33`
- Active NAT/PAT rules forwarding to host:
  - `5959` TCP/UDP (discovery/control)
  - `5960-6100` TCP/UDP (NDI transport)
  - `7960-8060` TCP/UDP (additional compatibility range)

### WAN test procedure (real external network)

1. Host runs `python3 stream_video.py`.
2. Remote receiver (different home/network) opens Access Manager.
3. Receiver adds `External Source` with `85.52.8.159`.
4. Receiver opens NDI Video Monitor and selects the source.
5. Audio/video reception is validated across WAN.

### Operational notes

- Testing public IP from the same host/LAN may fail due to NAT loopback (expected router behavior).
- In that scenario, duplicate sources or intermittent black video can appear when local and external paths are mixed.
- Discovery showing "Attempting" does not invalidate a working `External Source` path.

### Measured constraints

- With ~20 Mb/s symmetric internet, NDI Full can show audio cuts and very unstable video.
- For bandwidth-limited WAN links, lowering source resolution/FPS significantly improves stability.
- Recommended remote-test profile: `640x360 @ 24 fps`.

### Current status

- WAN feature validated across different residential networks.
- Remote reception confirmed in NDI Video Monitor.
- Runtime quality depends mainly on available WAN bandwidth and link stability.


## 20. Modular Backchannel Architecture

### Design objective

Keep the base project as an NDI A/V streamer (core) and move the return channel into optional modules that can be enabled/disabled per project.

### Resulting structure

- `core/clock.py`
  - Monotonic frame clock and pacing.
  - Prevents accumulated drift and keeps timing concerns out of the orchestrator.
- `extensions/backchannel/receiver.py`
  - Captures NDI receiver -> sender metadata.
  - Generic infrastructure (no Unity business logic inside).
- `extensions/backchannel/dispatcher.py`
  - Routes metadata messages to optional handlers.
- `integrations/unity/parsers.py`
  - Parses Unity payloads (currently `access_transform`).
- `integrations/unity/handlers.py`
  - Integration handlers (logging/actions per payload type).

### Extensibility contract

- Core does not depend on Unity-specific payload semantics.
- New Unity payload types are added in `integrations/unity/` (parser + handler), without touching base infrastructure.
- Return channel can be disabled with `--no-rx-metadata`; streamer remains a pure A/V sender.

### Compatibility status

- `stream_video.py` keeps backward-compatible CLI behavior.
- `--rx-metadata-log-all` is preserved as a legacy flag (no functional effect).

## 21. macOS Executable Launchers (Icon-based)

To avoid manual terminal startup, double-click launchers were added:

- `Launchers/Stream_NDI_Default.command`
  - Starts the default profile (`Videos/big_buck_bunny.mp4`).
  - Metadata return channel enabled (does not use `--no-rx-metadata`).
- `Launchers/Stream_NDI_LowRes_WAN.command`
  - Starts the lowres WAN profile (`Videos/test_360p24.mp4`).
  - Auto-generates the lowres asset with `ffmpeg` if missing.
  - Metadata return channel enabled.
- `Launchers/Create_Launcher_Apps.command`
  - Generates Finder apps in `Launchers/Apps/`:
    - `Stream NDI Default.app`
    - `Stream NDI LowRes WAN.app`

Recommended usage:

1. Double-click the `.app` icon (or `.command` for direct terminal launch).
2. Stop streaming with `Ctrl+C` in the launcher Terminal window.
