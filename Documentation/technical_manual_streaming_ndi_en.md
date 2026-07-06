# Technical Manual - SuperResolution NDI Streaming

Updated: 2026-07-06

Navigation: [Index](index_en.md) | [ES](manual_tecnico_streaming_ndi_es.md) | [HTML](technical_manual_streaming_ndi_en.html)

This manual describes the current Python NDI streamer implementation. The primary architectural goal is stable long-run A/V sync with fewer opaque layers and a clear extension path for features such as Unity-driven viewport overlays.

## Executive Summary

- Main entry point: `stream_video.py`.
- Unified A/V decode: `media_reader.py` with `PyAV`.
- NDI sender: `ndi_native.py` using direct `libndi`.
- Frame processing and overlays: `numpy`.
- Unity backchannel: received NDI metadata can be projected back into outgoing video.

## Current Architecture

```text
CLI -> LoopingMediaReader (PyAV)
    -> time-ordered media events (video/audio)
    -> single playback clock in stream_video.py
    -> NativeNdiSender (libndi)
    -> primary NDI output
    -> optional secondary NDI output (--dual)
    -> optional Unity metadata overlay
```

Audio and video come from the same media timeline. The system no longer reconstructs audio by slicing a predecoded buffer from video frame indices.

## Main Components

| Path | Role | Notes |
|---|---|---|
| `stream_video.py` | Runtime orchestration | Scheduling, dual output, metadata, cleanup |
| `media_reader.py` | Unified media reader | Loop-pass handling, A/V decode and ordering |
| `ndi_native.py` | Minimal NDI bindings | Direct sender and metadata capture |
| `utils.py` | Visual helpers and sender factory | Simple overlay and sender configuration |
| `extensions/backchannel/receiver.py` | Metadata return path | Receives XML from NDI receivers |
| `integrations/unity/` | Unity integration | Viewport parsing and state handling |

## Dependencies

| Component | Status | Reason |
|---|---|---|
| `numpy==2.3.5` | Mandatory | Video/audio buffers and overlays |
| `av==17.1.0` | Mandatory | Unified timeline and A/V decode |
| `libndi` | Mandatory | Native NDI sender runtime |
| `cyndilib` | Optional/legacy | No longer required for streaming; may still help locate a bundled `libndi` in development |
| `ffmpeg` | Optional outside runtime | Useful for preprocessing or helper launchers, not for the main streaming path |

## Design Principles

- One media timeline: media PTS is the scheduling source.
- One playback clock: `stream_video.py` controls pacing.
- Direct sender: the high-level wrapper is avoided in the runtime sender path.
- Metadata as extension: Unity viewport metadata enriches the base pipeline without redefining it.

## Runtime Flow

1. CLI arguments are parsed.
2. `LoopingMediaReader` opens the media and detects video/audio streams.
3. Main sender and optional secondary sender are configured.
4. The runtime consumes video or audio events already ordered by media time.
5. The scheduler waits for each event deadline and sends the frame or audio chunk.
6. If recent Unity metadata exists, the debug overlay can draw it into the outgoing video frame before send.
7. At end of file, the reader reopens the media and continues with an accumulated media-time offset.

## Operation

```bash
python3 -m pip install -r requirements.txt
python3 stream_video.py
python3 stream_video.py Videos/big_buck_bunny.mp4 --dual
python3 stream_video.py Videos/big_buck_bunny.mp4 --rx-metadata-verbose
```

See `setup_and_run_en.md` for the full first-time setup and validation path.

## Recommended QA

Validation matrix passed on 2026-06-18:

- NDI Monitor
- Unity Editor
- Unity AVP Simulator
- Apple Vision Pro device build

Ongoing QA:

1. Validate first in NDI Monitor.
2. Repeat next in Unity.
3. Run long sessions with production-like sources.
4. Compare 24/23.976 fps and 60/59.94 fps behavior.
5. Verify sources with audio, without audio, and source changes when relevant.

## Troubleshooting

| Symptom | Likely area | Action |
|---|---|---|
| NDI source not visible | NDI runtime or network visibility | Verify `libndi` installation and LAN visibility |
| Script fails at startup | `PyAV` dependency | Reinstall `requirements.txt` |
| Long-run A/V drift | Source generation or scheduler | Compare NDI Monitor and Unity, then inspect sender logs |
| Video stutter | Decode pressure or CPU load | Reduce source complexity and inspect timing warnings |
| Viewport overlay missing | Backchannel or stale metadata | Check Python console and Unity metadata emission |

## Current Status

The base architecture is organized around direct sender, unified media timeline, and metadata as an extension layer. Long-session soak tests remain recommended as ongoing QA practice.
