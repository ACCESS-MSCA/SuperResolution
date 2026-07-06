# Architecture Decision: Unified Media Timeline with `PyAV`

Updated: 2026-06-18

Status: implemented in code and functionally validated in NDI Monitor, Unity Editor, Unity AVP Simulator, and Apple Vision Pro device build

Navigation:
- Core ES manual HTML: `../manual_tecnico_streaming_ndi_es.html`
- Core EN manual HTML: `../technical_manual_streaming_ndi_en.html`
- ES deliverable: `./pyav_media_timeline_decision_es.html`
- Related previous decision: `./ndi_sender_libndi_decision_en.html`

## 1. Context

After migrating the A/V sender to direct `libndi`, long-run drift did not disappear on its own.

Important observations:

- Unity and NDI Monitor showed the same offset.
- The receiver layer was no longer the primary suspect.
- The sender wrapper alone could not explain the remaining drift.

The strongest hypothesis then became the streamer's own time model.

## 2. Problem in the previous pipeline

The previous runtime still followed two distinct logical paths:

- video: frame decode and pacing from video fps,
- audio: full decode into memory and manual slicing per video frame.

That introduced two implicit clocks:

1. the real clock from the media container,
2. the reconstructed clock derived from the video frame index.

Over long sessions, especially at `23.976`-style frame rates or during source changes, this was a clear candidate for accumulated error or drift.

## 3. Decision

The separated decode plus manual A/V reconstruction model is replaced with a unified reader built on `PyAV`.

The new rule is simple:

- audio and video come from the same container,
- every event carries media time,
- the runtime uses one playback clock,
- the NDI sender receives audio and video already ordered by that timeline.

## 4. Applied changes

Relevant files:

- `media_reader.py`: new decode and temporal-ordering module.
- `stream_video.py`: now consumes `MediaVideoEvent` and `MediaAudioEvent`.
- `utils.py`: the NDI sender is now created with `clock_video=False` and `clock_audio=False` to avoid two timing authorities.
- `requirements.txt`: adds `av==17.1.0`.

## 5. Design motivation

The goal is not to add another library for its own sake. The goal is to remove homemade timing logic where it can do the most damage.

With `PyAV` we gain:

- one shared timeline for audio and video,
- more natural support for fractional frame-rate material,
- less manual sample-per-frame math,
- lower risk of accumulated reconstruction drift,
- looser coupling between video cadence and audio delivery.

## 6. What the runtime stops doing

The new implementation stops depending on these ideas in the critical path:

- fully decoding audio into memory and slicing it by video frame,
- manually computing exact audio windows from `frame_idx`,
- assuming audio should be derived from the temporal grid of video,
- mixing NDI internal clocking and external clocking as if both were equally authoritative.

## 7. What remains unchanged

The following are kept:

- direct `libndi` sender,
- `numpy` overlays,
- Unity metadata backchannel,
- dual output mode,
- looped clip playback by passes,
- planar `float32` audio delivered to NDI.

## 8. Costs and tradeoffs

What we gain:

- a more coherent temporal architecture,
- less special-case corrective code,
- a better basis for heterogeneous sources,
- a clearer pipeline for the team to reason about.

What we pay:

- one additional Python dependency (`PyAV`),
- the need to validate wheel/install behavior on target machines,
- a somewhat more sophisticated reader than the earlier raw piping approach.

## 9. Frozen dependencies

Mandatory Python dependencies after this decision:

- `numpy==2.3.5`
- `av==17.1.0`

Mandatory external runtime dependency:

- `libndi`

`cyndilib` remains optional/legacy, not part of the main path.

## 10. Open risks

1. some sources may already be out of sync before they reach the sender,
2. `PyAV` behavior may differ across platforms or wheel builds,
3. a real issue may still live in source generation rather than the sender,
4. `ffmpeg.py` still exists as a legacy helper and may need cleanup in a later phase.

## 11. Current validation status

Functional validation is complete on the current project matrix:

1. NDI Monitor,
2. Unity Editor,
3. Unity AVP Simulator,
4. Apple Vision Pro device build.

Observed result: the streamer now behaves correctly on these targets after the move to a unified `PyAV` media timeline.

## 12. Recommended QA from this point

1. keep long-session soak runs as an operational regression test,
2. continue comparing NDI Monitor and Unity whenever a new or suspicious source appears,
3. repeat validation with `24/23.976` and `60/59.94` sources,
4. keep hot source changes and sender restarts as standard regression cases.

## 13. Executive summary

Introducing `PyAV` is not about gratuitous complexity. It is about removing a manual A/V reconstruction layer that was exactly the kind of mechanism likely to introduce long-run drift.

The architecture becomes cleaner:

- `PyAV` defines media time,
- `stream_video.py` defines when each event is emitted,
- `libndi` focuses on sending.
