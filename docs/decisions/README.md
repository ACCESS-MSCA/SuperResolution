# Decision register

These records preserve the engineering narrative without presenting superseded
code as a supported option.

| Decision | Current outcome | Evidence |
|---|---|---|
| Native sender API | Direct `libndi` retained | `ndi_sender_libndi_decision_*` |
| Media timeline | PyAV unified A/V timeline retained | `pyav_media_timeline_decision_*` |
| FFmpeg wrapper | Superseded and removed from active tree | `ffmpeg_migration_decision_section_*` |
| Standalone cyndilib/PyQt monitor | Removed; NDI Monitor is the external validation receiver | Git history + current architecture |
| Unity viewport handoff | Retained for explicit ROI-enabled streams | `unity_gaze_metadata_handoff_*` |

Every active decision must link forward to current operation. Every removed path
must remain explainable through this register and Git history; it must not stay
as dead code.
