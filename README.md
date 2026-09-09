# SuperResolution - NDI Streaming Base

Updated: 2026-09-07

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
CLI -> persistent looping PyAV decoder -> video events
    -> continuous fixed-size PCM block source -> native NDI audio clock
    -> NativeNdiSender(libndi)
    -> optional dual output overlay
    -> optional Unity metadata backchannel overlay
```

Important design choices:

- Audio and video use the same media timeline, with an explicit startup audio pre-roll for the AVP jitter buffer.
- Audio is emitted in continuous 1024-sample blocks that cross loop boundaries; there are no short tail packets or floating-point loop rebases.
- Exactly one NDI sender owns native audio clocking. Python does not pace the same PCM a second time.
- Audio is decoded and sent on a dedicated thread so heavy video decode/overlay work cannot starve the receiver audio queue.
- Seekable media loops reuse and flush persistent PyAV decoders. Reopening remains a compatibility fallback.
- Diagnostics are queued and written asynchronously so filesystem flushes cannot stall the audio sender thread.
- Late video frames are dropped before NDI send when needed to protect continuous audio; video never rebases independently from the shared A/V clock.
- The 8K launcher sends packed UYVY 4:2:2 instead of BGRA, halving frame memory and avoiding NDI's BGRA color conversion.
- ROI feedback is an explicit sender capability. With ROI enabled, Unity computes/sends viewport metadata and Python draws it directly on packed UYVY. With ROI disabled, Python does not start the backchannel and Unity automatically skips the viewport provider.
- The 8K launcher defaults to ROI OFF for the clean performance path. `Stream_NDI_Default_8K_ROI.command` and its `.app` variant enable the complete feedback loop. The optional `--dual` square output remains BGRA-only.
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
python3 stream_video.py Videos/big_buck_bunny.mp4 --no-roi-feedback
python3 stream_video.py Videos/big_buck_bunny.mp4 --roi-feedback
python3 stream_video.py Videos/big_buck_bunny.mp4 --diagnostics
python3 stream_video.py Videos/big_buck_bunny.mp4 --diagnostics --source-name StreamNDI-Test
python3 stream_video.py Videos/big_buck_bunny.mp4 --audio-preroll-ms 2500
# Optional diagnostic topology only:
python3 stream_video.py Videos/big_buck_bunny.mp4 --source-name StreamNDI --audio-source-name StreamNDI_Audio
```

The default and recommended ACCESS path omits `--audio-source-name`, multiplexing PCM
with video in `StreamNDI`. Unity may still use a second `AudioOnly` receiver connection
to that same source, preserving the dedicated video and audio processing paths while
keeping both media types on one NDI source timeline.

The 8K launcher also requests audio preloading. Eligibility is based on the decoded PCM
memory estimate (256 MiB budget), rather than an arbitrary duration cutoff, so clips such
as the 128-second Ghost Town test keep audio in RAM and remain isolated from video decode
or storage stalls. Preloading uses the project's PyAV decoder and does not require a
separate `ffmpeg` executable.

`--audio-source-name` remains available only for topology diagnostics. It publishes PCM
on a separate NDI source and removes audio from the primary video source. Its NDI sender
clock remains disabled because the audio worker already paces every PCM block against
the application-owned media timeline.

The combined default source owns the native NDI audio clock. Python feeds it fixed
1024-sample blocks and does not apply a second wall-clock wait. Video starts 2500 ms
after audio by default so the AVP receiver can fill its jitter buffer before the first
presented frame.

The default 8K launcher enables asynchronous diagnostics unless
`NDI_DIAGNOSTICS=0`; it also accepts explicit overrides:

```bash
NDI_DIAGNOSTICS=1 Launchers/Stream_NDI_Default.command
NDI_DIAGNOSTICS=1 NDI_SOURCE_NAME=StreamNDI-8K-Test Launchers/Stream_NDI_Default_8K.command
NDI_AUDIO_PREROLL_MS=3000 Launchers/Stream_NDI_Default_8K.command
```

8K ROI modes:

- `Launchers/Apps/Stream NDI Default 8K.app`: ROI OFF (recommended performance baseline).
- `Launchers/Apps/Stream NDI Default 8K ROI.app`: ROI ON.
- `NDI_ROI_FEEDBACK=0|1` selects the same mode when invoking `Stream_NDI_Default_8K.command` directly.

Every video frame advertises `<access_stream roi_feedback="0|1" />`. Compatible Unity receivers display this state and only evaluate/send viewport metadata when the source explicitly advertises ROI ON. `--no-roi-feedback` and the legacy `--no-rx-metadata` both disable the full ROI feedback path.

Diagnostics are written to `Logs/ndi_diagnostics_*.jsonl` and mirrored as compact `[diag]`
console summaries once per second.

## Runtime Diagnostics

### AVP 8K transport baseline (2026-09-09)

The 8K launchers and existing ROI-OFF/ROI-ON apps now default to
`NDI_TRANSPORT=single-tcp`; `auto` remains available for controlled comparisons:

```bash
NDI_TRANSPORT=single-tcp Launchers/Stream_NDI_Ghost_Towns_8K24.command
NDI_TRANSPORT=auto Launchers/Stream_NDI_Ghost_Towns_8K24.command
```

Single-TCP uses the repository's `Launchers/Config/SingleTCP/ndi-config.v1.json`
through process-local `NDI_CONFIG_DIR`, set before runtime preflight. It disables
RUDP, multi-TCP, unicast UDP and multicast sending so NDI falls back to base TCP.
`auto` preserves normal SDK/user configuration, including an explicitly inherited
`NDI_CONFIG_DIR`; unset that variable when comparing against SDK defaults. No
machine-wide NDI preferences are modified. Invalid transport values fail before
starting the sender. The JSONL `stream_start` records the requested policy and
configuration directory; those fields do not prove the negotiated transport.

The current AVP baseline passed 334.905 seconds after a clean scene re-entry:
zero underruns, zero concealment and zero mixer deadline misses, with a
2350.7–2500.0 ms reserve. The user confirmed continuous audible playback.
The verified connection used the Mac's Ethernet interface. This is a bounded
ROI-OFF test, not a long-session/ROI-ON sign-off or a universal networking
recommendation. Verify
actual sender sockets and interface during comparisons: the observed Mac has
both Ethernet and Wi-Fi on the same LAN, and NDI changed interface after a scene
reconnect. Keep the interface, clip, ROI setting and receiver prefill identical
before attributing a difference solely to TCP/RUDP. The existing 8K apps share
this launcher, so they do not require a new build-profile combination.

The AVP mixer-filter run still starved despite zero native NDI dropped frames.
That SDK counter covers frames not dequeued fast enough; it is not a complete
end-to-end packet-loss counter. Compare received PCM duration against elapsed
playout time and queue trend. A 10-second queue capacity is not a guaranteed
10-second reserve. Exclude intentional restart and headset suspension from the
measurement; re-enter the scene after suspension to remove stale queued audio
until the receiver's pause/resume lifecycle is corrected.

References: [NDI configuration](https://docs.ndi.video/all/developing-with-ndi/sdk/configuration-files),
[configuration location](https://docs.ndi.video/all/developing-with-ndi/sdk/platform-considerations),
[receiver counters](https://docs.ndi.video/all/developing-with-ndi/sdk/ndi-recv).

Use diagnostics when audio clicks, pitch wobble, receiver dropouts, or video stalls appear.

Correlate these fields first:

- `audio_late`, `audio_late_ms_max`: audio sender missed its playback deadline.
- `audio_gap_count`, `audio_gap_ms_max`: decoded audio timeline has a gap.
- `audio_send_ms_max`: NDI audio send call is taking too long.
- `audio_short_blocks`: must stay at zero; every packet is exactly 1024 samples.
- `audio_output_gaps` / `audio_output_bursts`: packet-cadence discontinuities seen after the NDI send call.
- `audio_native_clock`: must be `true` for the normal combined ACCESS source.
- `video_drops`: late video frames intentionally dropped to keep audio continuous.
- `video_late_ms_max`: maximum lateness against the shared, non-rebased media clock.
- `av_media_delta_ms`: latest audio media timestamp minus the video media timestamp; it should stay close to zero instead of growing over time.
- `video_gap_count`, `video_gap_ms_max`: visible output stalls between sent video frames.
- `video_read_ms_max`, `video_read_slow`: PyAV/decode/conversion was slow.
- `video_send_ms_max`, `video_send_slow`: NDI video send call was slow or blocked.
- `video_decode_errors`, `video_decode_recoveries`: source decoder hit invalid data and reopened.

For Apple Vision Pro/Xcode log correlation, run this in a separate Terminal before reproducing
the issue:

```bash
Launchers/Capture_AVP_Logs.command
```

Optional filters:

```bash
AVP_LOG_PROCESS=ACCESSVisionOS Launchers/Capture_AVP_Logs.command
AVP_LOG_TIMEOUT=45m Launchers/Capture_AVP_Logs.command
```

Current device/app observed during debugging:

- Apple Vision Pro: `Javier's Apple Vision Pro`
- CoreDevice identifier: `8C7D41D6-A14D-50DB-9EDF-865B71CD0CCA`
- Unity app: `ACCESS_VisionOS`
- Bundle id: `com.DefaultCompany.ACCESS-VisionOS`

The AVP/Xcode capture writes `Logs/avp_xcode_*.ndjson`. Compare its timestamps with
`Logs/ndi_diagnostics_*.jsonl` around the moment where NDI Monitor, Unity Editor, or AVP
shows a stall or audio artifact.

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
| Audio clicks or intermittent choppiness | Sender audio starvation under video/decode load | Keep the dedicated audio sender thread and audio-first video dropping enabled |
| 8K audio/video stalls in BGRA mode | 8K BGRA conversion and NDI compression overload | Use `Launchers/Stream_NDI_Default_8K.command`, which selects AV1 plus UYVY performance mode |
| AVFoundation duplicate-class warning on startup | NDI HX Driver and PyAV both load FFmpeg AVFoundation classes | Remove/disable the conflicting NDI HX FFmpeg driver for production validation |
| Video stutter under load | Decode or host pressure | Reduce source complexity and monitor timing warnings |
| Metadata overlay missing | Unity backchannel or stale metadata | Check sender console and Unity metadata sender |
| Metadata arrives but ROI is absent in UYVY | Invalid/stale viewport geometry or an older sender checkout | Check `[RX Viewport]`, then run the current `stream_video.py`; UYVY viewport drawing is supported in-place |

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
