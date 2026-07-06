# Architecture Decision: Migrate the A/V Sender from `cyndilib` to `libndi`

Updated: 2026-06-18

Status: implemented in code and functionally validated in NDI Monitor, Unity Editor, Unity AVP Simulator, and Apple Vision Pro device build

Navigation:
- Core ES manual HTML: `../manual_tecnico_streaming_ndi_es.html`
- Core EN manual HTML: `../technical_manual_streaming_ndi_en.html`
- ES deliverable: `./ndi_sender_libndi_decision_es.html`

## 1. Context

The project needs to publish an NDI signal from Python with these requirements:

- BGRA video decoded with `ffmpeg`,
- planar `float32` audio,
- support for multiple frame rates and media with or without audio,
- Unity-to-sender metadata so the sender can draw the user viewport or future features,
- a technical foundation that multiple engineers can evolve safely.

During long-run tests, a systematic A/V drift was observed:

- after roughly 20-30 minutes, audio was delayed by about 1-2 seconds behind video,
- the issue reproduced both in Unity and in NDI Monitor,
- therefore Unity was no longer the primary suspect and the Python sender became the main focus.

## 2. Previous implementation

The previous A/V sender used `cyndilib` as a high-level wrapper over NDI:

- `Sender`
- `VideoSendFrame`
- `AudioSendFrame`
- `write_video`, `write_audio`, `write_video_and_audio`

The rest of the project already used:

- `ffprobe` for media metadata,
- `ffmpeg` for video/audio decode,
- `numpy` for buffers and overlays,
- a metadata backchannel that was already very close to direct `libndi` usage via `ctypes`.

## 3. Observed problem in the previous architecture

Several strategies were tested without solving long-run drift:

1. manual pacing with a monotonic clock,
2. exact per-frame audio windows,
3. explicit control of video looping,
4. explicit timestamps/timecodes,
5. different NDI clock configurations,
6. combinations of internal and external pacing.

Drift still appeared consistently after long sessions.

Operational conclusion:

- adding more corrective logic on top of the wrapper increased complexity,
- and still did not produce a stable verifiable fix.

## 4. Decision

The high-level `cyndilib` A/V sender is replaced with a minimal direct sender built on top of `libndi`.

This migration only affects the A/V sending core. It is not a rewrite of the whole project.

The following remain unchanged:

- `ffmpeg` / `ffprobe` decode and probing,
- full-audio-in-memory strategy for deterministic access,
- `numpy` overlays,
- Unity metadata parsing and dispatch,
- metadata backchannel.

The following are replaced:

- sender creation,
- native video/audio frame structures,
- A/V send calls.

## 5. Design motivation

The goal is not “lower level for its own sake”, but reducing uncertainty.

With direct `libndi` we gain:

- explicit control over `NDIlib_send_create_t`,
- explicit control over `NDIlib_video_frame_v2_t` and `NDIlib_audio_frame_v3_t`,
- explicit control over `clock_video` and `clock_audio`,
- less implicit wrapper buffering,
- less opaque behavior in the A/V critical path,
- one less mandatory Python dependency in the runtime path.

The key value is traceability:

- if drift disappears, the wrapper was a relevant factor,
- if drift remains, the problem is elsewhere and time stops being wasted on the wrong layer.

## 6. Costs and tradeoffs

What we gain:

- a more direct sender architecture,
- reduced dependence on wrapper internals,
- better instrumentation and debugging potential,
- stronger technical coherence with the backchannel, which was already near-direct `libndi`.

What we lose:

- the convenience of `cyndilib` abstractions,
- some protection against native struct or call mistakes,
- more ownership of compatibility and maintenance,
- the need for stronger validation of memory, format and lifecycle behavior.

Conscious decision:

- we accept more low-level code in exchange for less uncertainty in the critical A/V path.

## 7. Exact migration scope

Files added or modified in this phase:

- `ndi_native.py`
- `utils.py`
- `stream_video.py`
- `extensions/backchannel/receiver.py`
- `requirements.txt`

Their roles:

- `ndi_native.py`: `libndi` runtime, `ctypes` structs, minimal A/V sender.
- `utils.py`: direct NDI sender factory.
- `stream_video.py`: still orchestrates decode, looping, audio windows and overlays.
- `receiver.py`: adapts the backchannel so it can work with either the new native sender or the legacy sender.
- `requirements.txt`: reduces the mandatory Python dependency set to `numpy`.

## 8. Dependency state after the decision

Mandatory runtime dependencies:

- `numpy==2.3.5`
- `ffmpeg`
- `ffprobe`
- `libndi`

Optional / legacy dependencies:

- `cyndilib`: no longer required for A/V sending.
- It may still remain installed as a legacy comparison path or as a fallback way to locate a bundled `libndi`.

## 9. Versions observed on the development machine

Observed during this migration:

- Python: `3.11.0`
- NumPy: `2.3.5`
- FFmpeg: `8.1`
- FFprobe: `8.1`
- Detected NDI runtime: `NDI SDK APPLE 6.2.1.0`

These references do not replace a full QA matrix, but they do define the technical context of the decision.

## 10. What does NOT change

This phase does not change:

- the metadata contract with Unity,
- the viewport overlay semantics,
- the `ffmpeg` decode model,
- the explicitly controlled video-pass loop,
- dual-output support,
- the planar `float32` audio strategy.

This is a migration of the NDI sending layer, not of the entire pipeline.

## 11. Open risks

Open risks that remain:

1. A/V drift may still persist even with a direct sender,
2. behavior may differ across macOS, NDI Monitor and other receivers,
3. the real issue may live in media timing or audio slicing logic,
4. some `libndi` initialization behavior may depend on the runtime environment.

Proper interpretation:

- migrating to direct `libndi` removes one major suspect,
- but it does not guarantee by itself that drift disappears.

## 12. Current validation status

Functional validation is complete on the current project matrix:

1. NDI Monitor,
2. Unity Editor,
3. Unity AVP Simulator,
4. Apple Vision Pro device build.

Observed result: after simplifying the sender and then moving the A/V timeline to `PyAV`, the current behavior is correct on all of these targets.

## 13. Recommended QA from this point

1. keep long-session soak tests as an operational regression check,
2. continue using NDI Monitor as the fastest reference when a source looks suspicious,
3. repeat the matrix with `24/23.976` and `60/59.94` signals,
4. treat sender restarts and hot source changes as standard regression cases.

## 14. Executive summary of the decision

Migrating the A/V sender from `cyndilib` to direct `libndi` is a deliberate simplification of the critical path after long-run sync drift proved resistant to incremental fixes.

This is not a full rewrite.

It is a reduction of layers:

- less wrapper in the sender,
- same decode and metadata pipeline,
- better ability to isolate the real cause of long-run drift.
