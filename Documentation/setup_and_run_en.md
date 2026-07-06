# Setup and Run Guide

Updated: 2026-07-06

Navigation: [Index](index_en.md) | [ES](setup_and_run_es.md) | [HTML](setup_and_run_en.html)

This guide covers first-time installation, runtime checks, and validation with NDI Monitor and the supplied Unity Simulator build.

## What This Project Runs

`SuperResolution` publishes an NDI video/audio source from Python and can receive Unity metadata through the NDI sender backchannel. In the current integration, Unity reports gaze/viewport state as `access_viewport` metadata, and Python parses it into `UnityViewportMetadata`.

```text
media file -> PyAV LoopingMediaReader -> stream_video.py scheduler
    -> NativeNdiSender/libndi -> NDI source visible to Unity and NDI Monitor
    <- Unity NDI metadata backchannel with gaze/viewport state
```

## Requirements

- macOS host.
- Python 3.11 was used during development.
- NDI Runtime or NDI SDK installed so `libndi` is visible to Python.
- Network/firewall settings that allow NDI discovery between Python, NDI Monitor, and Unity.

Pinned Python dependencies:

```text
numpy==2.3.5
av==17.1.0
```

`stream_video.py` uses PyAV for media decode. A separate system `ffmpeg` install is not part of the main runtime path, but can still be useful for diagnostics or legacy/offline tooling.

## Install

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

## Check NDI Runtime Visibility

```bash
python3 -c "from ndi_native import get_ndi_runtime; rt = get_ndi_runtime(); print(rt.lib.NDIlib_version().decode('utf-8', errors='replace'))"
```

Expected result: a printed NDI runtime version, for example `NDI SDK APPLE ...`.

Common failure: `Could not load NDI runtime library`.

Action: install the NDI Runtime/SDK and make sure `libndi` can be found by the Python process.

## Run the Streamer

```bash
python3 stream_video.py
python3 stream_video.py Videos/big_buck_bunny.mp4
python3 stream_video.py Videos/big_buck_bunny.mp4 --rx-metadata-verbose
python3 stream_video.py Videos/big_buck_bunny.mp4 --no-rx-metadata
python3 stream_video.py Videos/big_buck_bunny.mp4 --dual
```

By default, the primary NDI source name is `StreamNDI`.

## Validate Without Unity

1. Start the Python streamer.
2. Open NDI Monitor.
3. Confirm that `StreamNDI` appears.
4. Confirm video playback is visible and audio is present when the source has audio.
5. Leave the stream running for a representative duration if long-session behavior matters.

This isolates the Python sender and media timeline before adding Unity.

## Validate With the Unity Simulator Build

1. Start the Python streamer with metadata enabled:

   ```bash
   python3 stream_video.py Videos/big_buck_bunny.mp4 --rx-metadata-verbose
   ```

2. Launch the provided Unity Simulator build.
3. In Unity, connect the NDI receiver to the Python source, normally `StreamNDI`.
4. Move the simulated view/head so the NDI surface is visible.
5. Watch the Python console.

Expected Python logs include lines like:

```text
[info] backchannel receiver started
[RX Viewport] seq=12 scene=NDI uv=(0.123,0.234)-(0.456,0.678) hit_any=1 plane_intersection=1 poly_n=4 gaze_hit=1 gaze_uv=(0.321,0.456)
```

With `--rx-metadata-verbose`, raw XML payloads are also printed.

## How Python Receives Unity Metadata

- `extensions/backchannel/receiver.py`: captures NDI metadata frames from the active sender.
- `extensions/backchannel/dispatcher.py`: dispatches parsed XML messages to integration handlers.
- `integrations/unity/parsers.py`: converts `access_viewport` XML into `UnityViewportMetadata`.
- `integrations/unity/handlers.py`: keeps the latest viewport state.
- `stream_video.py`: draws the current debug viewport overlay when recent metadata exists.

The current debug overlay stale timeout in `stream_video.py` is 3 seconds.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Python cannot start NDI | `libndi` is not visible | Install NDI Runtime/SDK and rerun the runtime visibility check |
| NDI Monitor does not show `StreamNDI` | NDI runtime, firewall, LAN discovery, or sender not running | Check console output, same network segment, and host firewall |
| Unity does not see the source | NDI discovery or source selection | Confirm NDI Monitor sees it first, then reconnect Unity to `StreamNDI` |
| Python shows no `[RX Viewport]` logs | Unity metadata sender is not connected or stale | Run with `--rx-metadata-verbose`, inspect Unity metadata provider/sender |
| Gaze marker jumps to a corner | `gaze_hit` validity was not checked | Treat `gaze_uv` as valid only when `gaze_hit` is true |
| ERP/360 ROI is wrong around the seam | Consumer used only `uv_min/uv_max` | Use ERP-aware geometry from the metadata contract |

## Related Documents

- `Documentation/unity_viewport_metadata_contract_en.md`
- `Documentation/Deliverable/unity_gaze_metadata_handoff_en.md`
- `Documentation/technical_manual_streaming_ndi_en.md`
