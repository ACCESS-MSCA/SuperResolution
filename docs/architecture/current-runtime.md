# Current runtime architecture

Status: implemented and regression-tested  
Updated: 2026-09-25

## NDI production flow

```text
HEVC/H.264/VP9 master
  -> PyAV + optional VideoToolbox decode
  -> NV12 production path (UYVY safe diagnostic fallback)
  -> bounded frame prefetch and reuse
  -> shared media timeline + explicit 100 ns NDI timecodes
  -> direct libndi combined video/PCM source
  -> NDI Monitor / Unity Simulator / Apple Vision Pro

Unity viewport metadata (ROI ON only)
  -> NDI sender backchannel
  -> validated Unity parser
  -> direct overlay on NV12 or UYVY buffer
```

Audio is decoded/preloaded when eligible and emitted as continuous 48 kHz stereo
PCM blocks on a dedicated worker. Video follows completed PCM media position;
late video may be dropped to protect uninterrupted audio, but the two media
clocks are not independently rebased.

## Accepted decisions

| Area | Current decision | Reason |
|---|---|---|
| NDI API | Direct `libndi` | Small binding surface and deterministic ownership |
| Decode | PyAV, VideoToolbox requested | One A/V timeline with visible software fallback |
| NDI pixels | NV12 default | Avoids an application-level 4:2:0 to UYVY conversion |
| Transport | Isolated single-TCP | Bounded AVP memory and stable audio in measured LAN tests |
| Audio | 48 kHz stereo, 1024-sample blocks | Stable continuous receiver reservoir and loop boundaries |
| ROI | Explicit capability, OFF by default | Zero metadata/drawing work when not required |
| Diagnostics | Async JSONL in `runtime/logs` | Observability without blocking the audio worker |

## Measured status

- 23.976 fps sender gates completed with zero drops in controlled UYVY and NV12
  short runs; the optimized Ghost Town path completed multiple 128.25 s loops
  with zero PCM gaps, short blocks, decode errors or hardware fallbacks.
- NDI Monitor passed long A/V synchronization runs with no accumulated drift.
- Unity SIM and AVP accepted stable, uninterrupted audio after all public
  launchers were unified on the same sender contract.
- AVP full-bandwidth 8K cadence remains content and path dependent. It is a
  documented limitation, not a closed quality claim.

Exact run IDs, diagnostics and claim labels live in the canonical ACCESS
technical report. Runtime logs remain under `runtime/logs` and are ignored by
Git until selected as formal evidence.

## Removed paths

- `receiver.py`: standalone PyQt/cyndilib monitor; unused by production.
- `ffmpeg.py`: superseded experimental helper; offline preparation now lives in
  `tools/media/` and uses PyAV.
- receiver-only dependency file: removed with the standalone monitor.

The rationale remains in `docs/decisions/` and Git history. No legacy folder is
kept in the active tree.
