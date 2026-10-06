# SuperResolution · ACCESS media services

Python services used by ACCESS for full-bandwidth NDI streaming and controlled
Patchlab playback. The repository has two explicit applications over one shared
NDI package; runtime data and source media are no longer mixed with code.

## Start here

- NDI streaming: double-click a launcher in `launchers/ndi/`.
- Patchlab station: run `REMOTE_CONTROLLER/Run_AVP_Station.command` in the
  sibling `ACCESS_VisionOS_Metal` repository. It starts the coordinator and
  `apps.patchlab.study_sender` together.
- Unified visual documentation: open the sibling ACCESS file
  `../ACCESS_VisionOS_Metal/Documentation/index.html` and choose **Streaming NDI**
  or **Playtesting**.
- Internal source/evidence map: `docs/README.md`.

## Repository map

| Directory | Ownership |
|---|---|
| `apps/ndi_streamer/` | Looping A/V sender, PyAV media timeline and ROI drawing |
| `apps/patchlab/` | One-pass, controller-driven study sender and clip catalog |
| `packages/ndi/` | Direct libndi bindings, clock, metadata backchannel and Unity parser |
| `launchers/ndi/` | Operator-facing `.command` and `.app` entry points |
| `tools/media/` | Offline master and audio-sidecar preparation |
| `media/ndi/` | NDI reference clips and local quality masters |
| `media/patchlab/clips/` | Source clips used by Patchlab studies |
| `runtime/` | Ignored logs, raw study records, exports and generated media |
| `docs/` | Operations, decisions, metrics and European-project evidence links |
| `tests/` | NDI, Patchlab and media-tool regression tests |

## Stable NDI contract

The production path uses direct `libndi`, one PyAV audio/video timeline, fixed
1024-sample PCM blocks, a dedicated audio worker, bounded video prefetch and
explicit NDI timecodes. Public profiles delegate to the same universal sender;
the selected media resolution does not create a receiver-specific protocol.

Current production defaults are single-TCP, zero sender preroll,
VideoToolbox decode, NV12 input to NDI, audio preload when it fits the bounded
PCM budget, asynchronous diagnostics and ROI disabled unless explicitly
requested. The same source is intended for NDI Monitor, Unity Simulator and AVP.

The sender and audio synchronization are accepted. Full-bandwidth 8K cadence on
AVP remains a measured platform/network/receiver limitation and must not be
reported as universally smooth. See `docs/architecture/current-runtime.md` and
the canonical ACCESS technical report linked from `docs/european-project/`.

## Commands

```bash
# Production 1080p and 8K profiles
launchers/ndi/Stream_NDI_Default.command
launchers/ndi/Stream_NDI_Default_8K.command
launchers/ndi/Stream_NDI_Default_8K_ROI.command

# Direct expert invocation
.venv/bin/python -m apps.ndi_streamer.stream_video \
  media/ndi/reference/big_buck_bunny.mp4 --diagnostics

# Patchlab sender; normally started by ACCESS Remote Controller
.venv/bin/python -m apps.patchlab.study_sender \
  --coordinator ws://127.0.0.1:8090/ws

# Regression suite
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover \
  -s tests -p 'test_*.py'
```

## Dependencies and data safety

Install the shared runtime with `requirements.txt`. Patchlab adds
`apps/patchlab/requirements.txt`. NDI Runtime/Tools supplies `libndi`.

`runtime/` is intentionally excluded from Git. It contains research records and
diagnostics, not reproducible source. Copy it to the approved study backup after
each collection session. Media masters are also generally ignored because of
their size; the small tracked reference clips retain their existing Git/LFS
policy.

The former standalone PyQt/cyndilib receiver and obsolete `ffmpeg.py` helper were
removed after reference auditing. Their history and the reasons for replacement
remain in Git and in `docs/decisions/`; they are not active runtime components.

Do not create another visual portal in this repository. ACCESS owns the single
human-facing documentation gateway; this repository owns implementation sources,
tests, operation evidence and detailed manuals.
