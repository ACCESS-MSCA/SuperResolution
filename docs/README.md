# Internal documentation source map

Updated: 2026-09-25

Use this Markdown map only when editing or reviewing implementation evidence.
The single human-facing visual entry point is
`../ACCESS_VisionOS_Metal/Documentation/index.html` in the sibling ACCESS
repository. SuperResolution does not maintain a second portal.

## Current operation

- [Current runtime architecture](architecture/current-runtime.md)
- [NDI setup and run — ES](operations/ndi/setup_and_run_es.md)
- [NDI setup and run — EN](operations/ndi/setup_and_run_en.md)
- [NDI technical manual — ES](operations/ndi/manual_tecnico_streaming_ndi_es.md)
- [Unity viewport metadata contract — ES](operations/ndi/unity_viewport_metadata_contract_es.md)
- [Patchlab one-pass sender](operations/patchlab/study.md)
- [Patchlab archive and analysis](operations/patchlab/archive_analysis.md)

## Decisions and history

- [Decision register](decisions/README.md)
- [Direct libndi sender](decisions/ndi_sender_libndi_decision_es.md)
- [Single PyAV media timeline](decisions/pyav_media_timeline_decision_es.md)
- [Unity metadata handoff](decisions/unity_gaze_metadata_handoff_es.md)
- [8K source and optimization criteria](operations/ndi/video_ideal_y_optimizacion_streaming_8k_es.md)

Historical decisions explain why a path was attempted, superseded or removed.
They do not compete with `architecture/current-runtime.md` as current guidance.

## European project

- [Evidence ownership and bidirectional links](european-project/README.md)
- Canonical report: `ACCESS_VisionOS_Metal/ProjectContext/EuropeanProject/TechnicalReports/TR-01_LIVE_STREAMING_ARCHITECTURE.md`
- Unified visual documentation: `ACCESS_VisionOS_Metal/Documentation/index.html#europeo`

Contractual deliverables, internal technical reports and periodic reports are
different artefacts. This repository provides implementation evidence; ACCESS
owns the canonical cross-repository narrative and Grant Agreement mapping.
