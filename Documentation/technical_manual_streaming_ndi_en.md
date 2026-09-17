# Technical Manual - SuperResolution NDI Streaming

Updated: 2026-09-16

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
universal launcher -> persistent LoopingMediaReader (PyAV/VideoToolbox)
    -> video: bounded prefetch -> reusable NV12 lease -> optional direct ROI
    -> audio: preload -> fixed 1024-sample PCM blocks -> dedicated worker
    -> continuous timeline + explicit A/V NDI timecodes
    -> NativeNdiSender (libndi) -> combined StreamNDI -> single-TCP
    -> optional secondary NDI output (--dual)
    -> optional Unity metadata overlay
```

Audio and video come from the same media timeline. The system no longer reconstructs audio by slicing a predecoded buffer from video frame indices.

NV12 output buffers are pooled instead of allocating a new full 8K array for
every frame. A lease is retained through asynchronous `libndi` ownership and is
released only after the next submission returns or the sender flushes on close.
At 7680x4320 this avoids a repeated allocation of roughly 47.5 MiB. Allocation
and reuse counters are included in diagnostics. Media pixels, clocks, ROI and
audio scheduling are unchanged.

## Main Components

| Path | Role | Notes |
|---|---|---|
| `stream_video.py` | Runtime orchestration | Scheduling, dual output, metadata, cleanup |
| `media_reader.py` | Unified media reader | Loop-pass handling, A/V decode and ordering |
| `ndi_native.py` | Minimal NDI bindings | Direct sender and metadata capture |
| `create_8k_uhd_master.py` | Offline preparation | 7680x4320 HEVC/NV12 and optional AAC normalization |
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

- One media timeline: media PTS and completed native PCM progress govern continuity.
- One shared A/V clock: zero sender pre-roll and explicit timecodes.
- One universal profile: Monitor, SIM and Device receive the same emission.
- Direct sender: the high-level wrapper is avoided in the runtime sender path.
- Metadata as extension: Unity viewport metadata enriches the base pipeline without redefining it.

## Runtime Flow

1. CLI arguments are parsed.
2. `LoopingMediaReader` opens the media and detects video/audio streams.
3. Eligible audio is preloaded from the media or a compact AAC sidecar, and the bounded six-frame video prefetch fills before release.
4. A dedicated worker sends continuous PCM; video follows accepted PCM progress and drops expired frames.
5. Audio and video timecodes come from the same content position.
6. If recent Unity metadata exists, the debug overlay can draw it into the outgoing video frame before send.
7. At end of file, persistent readers flush/seek and continue without rebasing the timeline.

The sidecar is only an internal PCM preload source. Audio and video remain
multiplexed into one NDI source with the same timecodes. Six queued frames let
the decode worker prepare the next loop while the consumer is still presenting
the tail of the current cycle.

## Operation

```bash
python3 -m pip install -r requirements.txt
Launchers/Stream_NDI_Default.command
Launchers/Stream_NDI_Default_8K.command
Launchers/Stream_NDI_Default_8K_ROI.command
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

A/V continuity and synchronization are closed as the technical baseline. Every
public launcher shares single-TCP, zero pre-roll, timecodes, preloaded audio,
prefetch, VideoToolbox/NV12 and diagnostics. The 16 September A/B removed the
old 1080p launcher glitches by routing it through this path.

Sustained AVP 8K smoothness is not closed: on the measured LAN, full-bandwidth
delivery can fall below source cadence while sender and audio remain healthy.
The next decision is low-latency infrastructure or a licensed compressed-NDI
architecture, not another receiver-specific launcher.

HEVC/NV12 preparation removes unnecessary master/decode variability and keeps
VideoToolbox active, but it does not change the standard NDI transport codec. In
the 16 September Device run the sender produced roughly 304–320 Mbit/s of output
and receive delivery remained bursty. A larger buffer can absorb bounded bursts;
it cannot reconstruct frames that never reach the receiver.
