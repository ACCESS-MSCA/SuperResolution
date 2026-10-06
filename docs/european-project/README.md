# European-project evidence bridge

This repository owns executable sender evidence. The sibling ACCESS repository
owns the canonical Grant Agreement mapping, technical reports and deliverable
assembly.

## Bidirectional map

| SuperResolution evidence | Canonical ACCESS target |
|---|---|
| `apps/ndi_streamer/` and `packages/ndi/` | `TR-01_LIVE_STREAMING_ARCHITECTURE.md` |
| `docs/decisions/` | TR-01 design-decision sections |
| `docs/architecture/current-runtime.md` | TR-01 current architecture and limitations |
| `tests/ndi/` and runtime diagnostics | TR-01 verification/evidence register |
| `apps/patchlab/` | Patchlab protocol/operation evidence; not itself a contractual deliverable |

Canonical local paths:

```text
../ACCESS_VisionOS_Metal/Documentation/index.html#europeo
../ACCESS_VisionOS_Metal/ProjectContext/EuropeanProject/TechnicalReports/
../ACCESS_VisionOS_Metal/ProjectContext/EuropeanProject/OBLIGATIONS.md
```

Repository link: [ACCESS EuropeanProject](https://github.com/NowAR-lab/ACCESS_VisionOS_Metal/tree/main/ProjectContext/EuropeanProject)

## Claim discipline

Use only: implemented, tested, planned, not evidenced or superseded. Do not call
8K AVP video universally smooth; do not describe the head/camera vector as
ocular gaze; do not call the ROI overlay a deployed super-resolution model.

Technical reports may be reused inside D4.2/D5.1/D6.1 as supporting evidence,
but internal `TR-*` identifiers never create new contractual deliverables.
