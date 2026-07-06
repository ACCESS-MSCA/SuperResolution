# SuperResolution - NDI Streaming Base

Updated: 2026-07-06

## Overview

This repository publishes an NDI source from Python, with optional dual-output visual validation and an NDI metadata backchannel from Unity.

Current runtime architecture is intentionally narrow:

- `libndi` is used directly for NDI send operations.
- `PyAV` provides a single decode timeline for audio and video.
- `numpy` is used for frame buffers and overlays.
- Unity metadata is received through NDI metadata frames and can be drawn back into the outgoing video.

The main technical goal is long-run A/V stability without building extra corrective layers around the sender.

## Delivery Quick Start

For first-time setup and validation:

1. Follow `Documentation/setup_and_run_en.md` to install Python dependencies, validate `libndi`, and run the streamer.
2. Confirm the `StreamNDI` source in NDI Monitor before adding Unity.
3. Launch the supplied Unity Simulator build and connect it to the Python NDI source.
4. Run with metadata logging:

   ```bash
   python3 stream_video.py Videos/big_buck_bunny.mp4 --rx-metadata-verbose
   ```

5. Confirm `[RX Viewport]` lines appear in the Python console.
6. Use `Documentation/unity_viewport_metadata_contract_en.md` as the field-level contract for gaze/viewport metadata.

Additional delivery context is available in `Documentation/Deliverable/unity_gaze_metadata_handoff_en.md`.

## Current Architecture

The streamer now uses one authoritative media timeline.

```text
CLI -> LoopingMediaReader(PyAV) -> ordered media events (video/audio)
    -> single playback clock in stream_video.py
    -> NativeNdiSender(libndi)
    -> optional dual output overlay
    -> optional Unity metadata backchannel overlay
```

Important design choices:

- Audio and video are decoded from the same container timeline.
- Audio is no longer reconstructed by slicing a full decoded buffer per video frame.
- NDI sender clocks are disabled in the runtime path so that timing has one authority instead of two competing ones.
- The sender path is direct `libndi`, not `cyndilib`.

## Key Files

| Path | Role |
|---|---|
| `stream_video.py` | Runtime orchestration, scheduling, dual output, metadata overlay |
| `media_reader.py` | Unified looping A/V reader built on `PyAV` |
| `ndi_native.py` | Minimal direct `libndi` sender and metadata capture bindings |
| `utils.py` | Sender factory and visual overlay helper |
| `extensions/backchannel/receiver.py` | Metadata backchannel capture from NDI receivers |
| `integrations/unity/` | Unity metadata parsing and viewport interpretation |
| `Launchers/` | Convenience launchers for local workflows |
| `Documentation/` | Technical manuals and architecture decisions |
| `Documentation/index_en.md` | Documentation map in English |
| `Documentation/index_es.md` | Documentation map in Spanish |
| `Documentation/setup_and_run_en.md` | Installation, first run, and Unity Simulator validation |
| `Documentation/unity_viewport_metadata_contract_en.md` | Gaze/viewport metadata contract for Python consumers |

Note on `ffmpeg.py`:
- it is no longer part of the runtime streaming path.
- `ffmpeg` may still be useful for offline preprocessing or launcher workflows.

## Dependency Policy

Pinned Python dependencies:

- `numpy==2.3.5`
- `av==17.1.0`

External runtime dependencies:

- NDI runtime (`libndi`)

Optional legacy dependency:

- `cyndilib` is not required for streaming anymore.
- it may still help locate a bundled `libndi` during development.

Observed development context during this refactor:

- Python `3.11.0`
- NumPy `2.3.5`
- NDI runtime `NDI SDK APPLE 6.2.1.0`

## Install

```bash
cd <project-root>
python3 -m pip install -r requirements.txt
```

For a full first-time setup, including virtual environment and NDI runtime checks, use `Documentation/setup_and_run_en.md`.

## Run

```bash
python3 stream_video.py
python3 stream_video.py Videos/big_buck_bunny.mp4
python3 stream_video.py Videos/big_buck_bunny.mp4 --dual
python3 stream_video.py Videos/big_buck_bunny.mp4 --rx-metadata-verbose
python3 stream_video.py Videos/big_buck_bunny.mp4 --no-rx-metadata
```

## Validation Workflow

Completed validation scope as of 2026-06-18:

- NDI Monitor
- Unity Editor
- Unity AVP Simulator
- Apple Vision Pro device build

Recommended ongoing QA:

1. Start the sender with a representative source.
2. Validate first in NDI Monitor.
3. Validate next in Unity.
4. Run at least one long session with the actual production-like source.
5. Compare behavior across:
   - 23.976 / 24 fps,
   - 59.94 / 60 fps,
   - sources with audio,
   - sources without audio,
   - live source changes when applicable.

## Troubleshooting

| Symptom | Likely area | Recommended action |
|---|---|---|
| No NDI source visible | `libndi` runtime or LAN visibility | Verify runtime installation and same network segment |
| Stream fails immediately | Missing `PyAV` dependency | Reinstall `requirements.txt` |
| Audio drift after long session | Source generation or scheduling | Compare in NDI Monitor and Unity; inspect sender logs |
| Video stutter under load | Decode or host pressure | Reduce source complexity and monitor timing warnings |
| Metadata overlay missing | Unity backchannel or stale metadata | Check sender console and Unity metadata sender |

## Design Decisions

Current important decisions are documented here:

- `Documentation/index_en.md`
- `Documentation/index_es.md`
- `Documentation/setup_and_run_en.md`
- `Documentation/setup_and_run_es.md`
- `Documentation/unity_viewport_metadata_contract_en.md`
- `Documentation/unity_viewport_metadata_contract_es.md`
- `Documentation/manual_tecnico_streaming_ndi_es.html`
- `Documentation/manual_tecnico_streaming_ndi_es.md`
- `Documentation/technical_manual_streaming_ndi_en.html`
- `Documentation/technical_manual_streaming_ndi_en.md`
- `Documentation/Deliverable/unity_gaze_metadata_handoff_es.md`
- `Documentation/Deliverable/unity_gaze_metadata_handoff_en.md`
- `Documentation/Deliverable/ndi_sender_libndi_decision_es.md`
- `Documentation/Deliverable/ndi_sender_libndi_decision_en.md`
- `Documentation/Deliverable/pyav_media_timeline_decision_es.md`
- `Documentation/Deliverable/pyav_media_timeline_decision_en.md`

## Current Status

The runtime path has been simplified around these principles:

- one media timeline,
- one playback clock,
- one direct NDI sender implementation,
- metadata as an extension layer instead of a second transport.

Current validation matrix passed on 2026-06-18:

- NDI Monitor
- Unity Editor
- Unity AVP Simulator
- Apple Vision Pro device build

Long-session soak testing remains recommended as ongoing QA, but the current architecture is now validated on the active target surfaces.
