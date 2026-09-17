# SuperResolution - NDI Streaming Base

Updated: 2026-09-16

## Overview

This repository publishes an NDI source from Python, with optional dual-output visual validation and an NDI metadata backchannel from Unity.

Current runtime architecture is intentionally narrow:

- `libndi` is used directly for NDI send operations.
- `PyAV` provides a single decode timeline for audio and video.
- `numpy` is used for frame buffers and overlays.
- Unity metadata is received through NDI metadata frames and can be drawn back into the outgoing video.

The production A/V contract is now common to every public media launcher. The
remaining 8K limitation is receiver-side full-bandwidth cadence on the measured
AVP LAN path, not accumulated audio drift or a receiver-specific sender profile.

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

- Audio and video use the same explicit media timeline. The universal production sender uses zero startup pre-roll; non-zero pre-roll is diagnostic-only.
- Audio is emitted in continuous 1024-sample blocks that cross loop boundaries; there are no short tail packets or floating-point loop rebases.
- Exactly one NDI sender owns native audio clocking. Python does not pace the same PCM a second time.
- Completed native PCM media position is the continuity authority: if an audio send stalls, video waits and shifts its future deadlines instead of consuming pre-roll and accumulating loop-by-loop drift.
- Audio is decoded and sent on a dedicated thread so heavy video decode/overlay work cannot starve the receiver audio queue.
- Seekable media loops reuse and flush persistent PyAV decoders. Reopening remains a compatibility fallback.
- Diagnostics are queued and written asynchronously so filesystem flushes cannot stall the audio sender thread.
- Late video frames are dropped before NDI send when needed to protect continuous audio; video never rebases independently from the shared A/V clock.
- I420 is supported as an explicit ROI-OFF diagnostic path. It reduces each 8192x4320 raw frame from 67.5 MiB in UYVY to 50.625 MiB, but the 45-minute 156 Mbps trial made native NDI submission slower and reduced delivered cadence, so it is not the universal production format.
- NV12 is the current receiver-acceptance candidate for HEVC/yuv420p media. VideoToolbox frames are submitted without a 4:2:0-to-4:2:2 conversion; UYVY remains the safe fallback until Device acceptance is complete.
- Decoded/packed NV12 arrays are leased from a bounded reuse pool. At 7680x4320 this avoids allocating a new roughly 47.5 MiB array per frame. The lease remains owned by the asynchronous NDI submission until the following video send returns (or shutdown flushes it), so reuse cannot overwrite pixels still in native use. Diagnostics report allocation and reuse counts.
- ROI feedback is an explicit sender capability. With ROI enabled, Unity computes/sends viewport metadata and Python draws it directly into NV12 or packed UYVY. With ROI disabled, Python does not start the backchannel and Unity automatically skips the viewport provider.
- The 8K launcher defaults to the local 7680x4320/23.976 HEVC quality candidate, VideoToolbox decode and ROI OFF/NV12. `Stream_NDI_Default_8K_ROI.command` and its `.app` variant enable the same media/decode/NV12 path plus feedback drawing. The optional `--dual` square output remains BGRA-only.
- The universal launcher defaults to isolated single-TCP. A controlled AVP comparison showed that SDK auto/RUDP could grow native receiver memory to the 5120 MB visionOS limit under the current saturated Wi-Fi path, whereas TCP backpressure kept the app alive and audio stable. `NDI_TRANSPORT=auto` remains an expert diagnostic override.
- The sender path is direct `libndi`, not `cyndilib`.

This optimization does not change the full-bandwidth NDI wire format and does
not require the NDI Advanced SDK. The matching experimental Unity receiver keeps
its jitter queue as raw UYVY on a dedicated capture worker and performs GPU
conversion only for the frame selected for presentation. Standard NDI may still
perform an internal NV12-to-UYVY conversion; bypassing that or sending the HEVC
bitstream directly is outside this architecture.

## Universal launcher contract

Every public media launcher delegates to `Stream_NDI_Default_8K.command`, which
is the historical filename of the universal production entry point. It does not
force an 8K resolution: `Stream_NDI_Default.command` runs the original 1080p60
file through exactly the same runtime contract.

Production defaults shared by 360p, 1080p, 4K and 8K profiles:

- one combined `StreamNDI` source for NDI Monitor, Unity Simulator and AVP;
- isolated `single-tcp` transport;
- zero sender preroll and explicit A/V media timecodes;
- preloaded audio when it fits the 256 MiB decoded-PCM budget;
- six-frame bounded video prefetch (about 95 MiB more than the former four-frame queue at 8K NV12);
- VideoToolbox decode with visible software fallback;
- direct NV12 output by default, UYVY as the safe diagnostic fallback;
- asynchronous JSONL diagnostics enabled;
- ROI OFF unless the selected launcher or `NDI_ROI_FEEDBACK=1` enables it.

The former 1080p launcher bypassed this contract: it used SDK auto/RUDP, BGRA,
live AAC 5.1 decode/downmix, no audio preload, no prefetch and no diagnostics.
On AVP that profile produced repeated audible glitches even though the input was
only 1080p. Running the identical file through the universal launcher removed
the issue in the user A/B on 16 September 2026. Resolution alone was therefore
not a valid proxy for runtime load or stability.

## Key Files

| Path | Role |
|---|---|
| `stream_video.py` | Runtime orchestration, scheduling, dual output, metadata overlay |
| `media_reader.py` | Unified looping A/V reader built on `PyAV` |
| `ndi_native.py` | Minimal direct `libndi` sender and metadata capture bindings |
| `create_8k_uhd_master.py` | Reproducible 8192x4320 or native 7680x4320 source to HEVC/NV12 quality-master conversion; optional AAC normalization |
| `create_audio_sidecar.py` | Compact AAC 48 kHz stereo sidecar generation for fast deterministic audio preload |
| `utils.py` | Sender factory and visual overlay helper |
| `receiver.py` | Legacy standalone PyQt5/cyndilib NDI monitor; retained for compatibility, not used by the production sender |
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

- `cyndilib` and `PyQt5` are not required for production streaming.
- They are isolated in `requirements-receiver-legacy.txt` for the retained
  standalone `receiver.py` utility.

Observed development context during this refactor:

- Python `3.11.0`
- NumPy `2.3.5`
- NDI runtime `NDI SDK APPLE 6.2.1.0`

## Install

```bash
cd <project-root>
python3 -m pip install -r requirements.txt
```

To run the legacy desktop receiver instead:

```bash
python3 -m pip install -r requirements-receiver-legacy.txt
python3 receiver.py
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
python3 stream_video.py Videos/big_buck_bunny.mp4 --audio-preroll-ms 0
# Optional diagnostic topology only:
python3 stream_video.py Videos/big_buck_bunny.mp4 --source-name StreamNDI --audio-source-name StreamNDI_Audio
```

The default and recommended ACCESS path omits `--audio-source-name`, multiplexing PCM
with video in `StreamNDI`. Unity may still use a second `AudioOnly` receiver connection
to that same source, preserving the dedicated video and audio processing paths while
keeping both media types on one NDI source timeline.

Every public launcher requests audio preloading. Eligibility is based on the decoded PCM
memory estimate (256 MiB budget), rather than an arbitrary duration cutoff, so clips such
as the 128-second Ghost Town test keep audio in RAM and remain isolated from video decode
or storage stalls. Preloading uses the project's PyAV decoder and does not require a
separate `ffmpeg` executable. `NDI_AUDIO_PATH` or `--audio-file` may point to an
audio-only sidecar; the emitted NDI source is still one combined `StreamNDI` source with
one shared timeline. This avoids scanning a multi-gigabyte interleaved master at startup.

`--audio-source-name` remains available only for topology diagnostics. It publishes PCM
on a separate NDI source and removes audio from the primary video source. Its NDI sender
clock remains disabled because the audio worker already paces every PCM block against
the application-owned media timeline.

The combined default source owns the native NDI audio clock. Python feeds it fixed
1024-sample blocks and does not apply a second wall-clock wait. The production default
has zero sender preroll: audio and video start on the same content timeline for every
receiver. Both carry explicit 100 ns NDI timecodes derived from that continuous media
timeline. ACCESS derives its own initial PCM reservoir from those clocks and its actual
video presentation queue; sender-side preroll remains only an explicit diagnostic
override because generic monitors may play early audio immediately.

The universal launcher enables asynchronous diagnostics unless
`NDI_DIAGNOSTICS=0`; it also accepts explicit overrides:

```bash
NDI_DIAGNOSTICS=1 Launchers/Stream_NDI_Default.command
NDI_DIAGNOSTICS=1 NDI_SOURCE_NAME=StreamNDI-8K-Test Launchers/Stream_NDI_Default_8K.command
NDI_AUDIO_PREROLL_MS=3000 Launchers/Stream_NDI_Default_8K.command
```

8K ROI modes:

- `Launchers/Apps/Stream NDI Default 8K.app`: ROI OFF/NV12 (current receiver-acceptance candidate).
- `Launchers/Apps/Stream NDI Default 8K ROI.app`: ROI ON with direct NV12 drawing.
- `NDI_ROI_FEEDBACK=0|1` selects the same mode when invoking `Stream_NDI_Default_8K.command` directly.
- `NDI_VIDEO_PIXEL_FORMAT=auto|i420|nv12|uyvy422|bgra` is an expert diagnostic override. The current candidate is `nv12`; incompatible ROI/I420 combinations fail instead of silently losing the overlay.
- `NDI_VIDEO_HWACCEL=videotoolbox|none` selects decode. The 8K launcher defaults to VideoToolbox and records the backend actually used; an explicit software fallback remains visible in diagnostics.

The ignored local production candidate can be regenerated without modifying the
original H.264/AAC file:

```bash
.venv/bin/python create_8k_uhd_master.py \
  Videos/Prod/NDI_AV_Sync_Test_002_8K_156Mbps_H264_AAC.mp4 \
  Videos/Prod/NDI_AV_Sync_Test_002_8K_UHD_24fps_160Mbps_HEVC_AAC.mp4 \
  --fps 24000/1001 --bitrate-mbps 160
```

The recipe preserves the complete 8192x4320 image by scaling proportionally to
7680x4050 and adding chroma-aligned 134/136-pixel black bars. It copies the AAC packet
payloads and timestamps instead of re-encoding audio. The measured output is
7680x4320 HEVC Main/yuv420p, 24000/1001 fps, about 151.9 Mbit/s video, 300-to-240
frame conversion and 10.01 seconds.

Ghost Town can be normalized without replacing the LFS-tracked VP9/Opus
original. Its native 7680x4320 geometry and 5959/250 cadence are preserved;
only the delivery master changes to HEVC Main/NV12 plus AAC 48 kHz stereo:

```bash
.venv/bin/python create_8k_uhd_master.py \
  Videos/Ghost_Towns_in_8K_GoPro_be_Hero.webm \
  Videos/Prod/Ghost_Towns_8K_UHD_23_836fps_HEVC_AAC.mp4 \
  --fps source --bitrate-mbps 160 --audio-codec aac

.venv/bin/python create_audio_sidecar.py \
  Videos/Ghost_Towns_in_8K_GoPro_be_Hero.webm \
  Videos/Prod/Ghost_Towns_8K_AAC_48k_Stereo.m4a \
  --bitrate-kbps 320
```

`Stream_NDI_Ghost_Towns_8K24.command` prefers the ignored local video/audio pair
when both files are present and otherwise falls back to the original. Current diagnostics show
that VideoToolbox can already decode the VP9 original on this Mac, so the
derivative standardizes production media and loop behavior; it does not reduce
full-bandwidth NDI wire traffic because NDI receives the same decoded pixels.

The 17 September smoke test generated a 4.76 MB sidecar, preloaded 6,156,066
stereo samples in 166.2 ms and completed three 128.25-second loop boundaries
without boundary drops. The complete 396.6-second run retained zero PCM gaps,
audio lateness, short blocks, decode errors or hardware fallbacks. Forty-six
late frames still occurred in native-send stalls inside the loops; the sidecar
and six-frame lookahead solve startup/loop continuity, not transport blocking.

Local sender gates on 15 September 2026:

- 29.97 fps remained above this host's sustainable complete-pipeline capacity:
  UYVY sent 58 and dropped 1,388 frames in 49.2 seconds; I420 sent 1,574 and
  dropped 297 in 62.9 seconds.
- 23.976 fps/UYVY/VideoToolbox ROI OFF sent 1,705 frames with zero drops over
  71.6 seconds. The warmed ROI ON run sent 704 with zero drops over 30.0 seconds.
- Direct NV12/VideoToolbox also passed short ROI OFF and ROI ON sender gates with
  zero dropped video frames and no PCM media discontinuities. Its Device value
  must be reassessed after removing Unity's duplicated full 8K receiver.
- PCM media gaps, short blocks and audio lateness remained zero in all trials.
  The prefetch now reaches its six-frame startup depth before the shared A/V
  timeline begins, removing the observed cold-start video drops.

These establish sender cadence and PCM continuity. NDI Monitor has passed the
long A/V synchronization gate, and the user has confirmed synchronized audio in
current SIM/Device runs plus stable 1080p audio after the launcher unification.
Full-bandwidth 8K video smoothness on AVP remains limited by the measured LAN/
receiver delivery path and must not be reported as achieved.

Every video frame advertises `<access_stream roi_feedback="0|1" />`. Compatible Unity receivers display this state and only evaluate/send viewport metadata when the source explicitly advertises ROI ON. `--no-roi-feedback` and the legacy `--no-rx-metadata` both disable the full ROI feedback path.

Diagnostics are written to `Logs/ndi_diagnostics_*.jsonl` and mirrored as compact `[diag]`
console summaries once per second.
`video_audio_sync_waits`, `video_audio_sync_wait_ms_total`,
`video_audio_sync_wait_ms_max` and `av_media_delta_ms` expose when video had to
follow delayed native audio. A bounded wait is expected under load; an increasing
negative media delta is not.

Diagnostics schema 4 records the explicit media-timecode contract and separates
compressed decode (`video_decode_ms_max`), raw packing/conversion
(`video_pack_ms_max`), complete reader latency and native NDI submission. It also separates two
different measurements:

Detailed slow-event records are rate-limited after the first five occurrences;
the cumulative summary counters remain exact. This prevents a persistently slow
format from generating tens of thousands of JSON records and perturbing the run.

- `av_content_lead_ms`: PCM media position already accepted by `libndi` minus
  video media position. With the universal profile it should remain bounded near
  one audio block rather than a receiver-specific multi-second offset.
- `video_clock_lag_ms` / `audio_clock_lag_ms`: cumulative delay of each completed
  native submission against its original monotonic schedule.
- `av_submission_drift_ms`: video clock lag minus audio clock lag. A growing
  positive value means video submissions are falling behind audio submissions.

`[sync]` and `av_sync_checkpoint` report the same data once per media loop, with
per-loop drops and waits. Test the same `Stream_NDI_Default_8K.command` source
unchanged in NDI Monitor and ACCESS. Do not select a receiver-specific launcher.

## Runtime Diagnostics

### AVP 8K transport and receiver baseline

The 8K launchers and ROI-OFF/ROI-ON apps default to isolated single-TCP. The SDK
automatic/RUDP policy remains a controlled diagnostic override:

```bash
NDI_TRANSPORT=auto Launchers/Stream_NDI_Ghost_Towns_8K24.command
NDI_TRANSPORT=single-tcp Launchers/Stream_NDI_Ghost_Towns_8K24.command
```

Single-TCP uses the repository's `Launchers/Config/SingleTCP/ndi-config.v1.json`
through process-local `NDI_CONFIG_DIR`, set before runtime preflight. It disables
RUDP, multi-TCP, unicast UDP and multicast sending so NDI falls back to base TCP.
`auto` preserves normal SDK/user configuration, including an explicitly inherited
`NDI_CONFIG_DIR`; unset that variable when comparing against SDK defaults. No
machine-wide NDI preferences are modified. Invalid transport values fail before
starting the sender. The JSONL `stream_start` records the requested policy and
configuration directory; those fields do not prove the negotiated transport.

On 2026-09-15, the corrected one-video-receiver Device build still received only
about 2–5 fps through the current AVP Wi-Fi route. Unity remained at roughly
60–85 fps and decode/upload cost about 5–7 ms. With auto/RUDP the app reached the
5120 MB memory high-watermark in about 22 seconds; with the identical media,
NV12 path and timing over single-TCP it remained alive for more than three
minutes and audio stayed stable. Direct ping to the AVP showed 3–593 ms latency
(102 ms average), so full-cadence 8K acceptance is blocked by network throughput/
jitter rather than sender cadence or Unity presentation cost.

The 2026-09-09 AVP baseline passed 334.905 seconds after a clean scene re-entry:
zero underruns, zero concealment and zero mixer deadline misses, with a
2350.7–2500.0 ms reserve. The user confirmed continuous audible playback.
The verified connection used the Mac's Ethernet interface. This is a bounded
ROI-OFF test, not a long-session/ROI-ON sign-off or a universal networking
recommendation. It was later found to be confounded by an interface change and
two enabled full-video receivers in the Unity NDI scene. The corrected scene
keeps one visual receiver and an AudioOnly receiver, and caps its visionOS video
pool at 512 MiB. Verify
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
| Audio drift after long session | Sender submissions diverge, NDI transport/presentation queues grow, or receiver playout is not tracking the shared media timeline | Test the same normal source first in NDI Monitor and then ACCESS; inspect `av_submission_drift_ms`, source timecodes and per-loop `[sync]` checkpoints, then retain both logs. |
| Audio clicks or intermittent choppiness | Sender audio starvation under video/decode load | Keep the dedicated audio sender thread and audio-first video dropping enabled |
| 8K audio/video stalls | Raw conversion, software decode or NDI compression overload | Use the UYVY default and inspect schema-4 decode, pack and native-send timings separately. I420 is a measured diagnostic alternative, not an assumed optimization |
| AVFoundation duplicate-class warning on startup | Optional NDI HX Driver and PyAV both load FFmpeg AVFoundation classes | The 8K launcher now diagnoses it before startup. Remove/disable NDI HX Driver for production validation when this Mac does not need to receive NDI|HX cameras; the full-bandwidth sender does not use it |
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
