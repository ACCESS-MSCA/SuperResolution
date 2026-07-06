# Delivery Note: Unity Gaze Metadata for Hyper-Resolution

Status: implemented in Unity metadata provider and parsed in Python

Updated: 2026-07-06

Navigation: [Documentation index](../index_en.md) | [ES](unity_gaze_metadata_handoff_es.md) | [HTML](unity_gaze_metadata_handoff_en.html)

## Purpose

This delivery note explains how the Python `SuperResolution` project receives gaze/viewport metadata from the supplied Unity Simulator build. It identifies the delivered integration points, the data contract, and the current validation path.

## Delivered Scope

The delivery includes:

- Python NDI streaming runtime in `SuperResolution`.
- Direct `libndi` sender implementation.
- PyAV-based A/V media timeline.
- Unity metadata backchannel receiver.
- Unity viewport metadata parser.
- Debug ROI/gaze overlay in Python.
- Unity simulator build expected to connect to the Python NDI source and emit `access_viewport` metadata.

The delivery does not include:

- A production hyper-resolution algorithm.
- A final foveated quality profile.
- Per-video-frame metadata synchronization.
- A custom installer for NDI Runtime or Python.

## Validation Path

The following path validates that the Python source, Unity receiver, and metadata backchannel are connected:

1. Install and run Python using `Documentation/setup_and_run_en.md`.
2. Confirm `StreamNDI` appears in NDI Monitor.
3. Launch the Unity simulator build and connect it to the Python NDI source.
4. Run Python with:

   ```bash
   python3 stream_video.py Videos/big_buck_bunny.mp4 --rx-metadata-verbose
   ```

5. Confirm `[RX Viewport]` lines appear in the Python console.
6. Read the latest parsed metadata from `UnityViewportStateHandler.state.latest`.
7. Confirm that `viewport.gaze_hit` and `viewport.gaze_uv` are populated in the parsed `UnityViewportMetadata` object.

## Main Contract

Use `Documentation/unity_viewport_metadata_contract_en.md` as the field-level contract.

Core fields for gaze/viewport integration:

- `schema_version`: current contract version, currently `1`.
- `sequence`: monotonic Unity payload sequence.
- `gaze_hit`: whether `gaze_uv` is valid.
- `gaze_uv`: normalized foveal center in Unity/texture UV space.
- `uv_projection`: planar vs equirectangular projection mode.
- `uv_polygon`: visible surface ROI polygon.
- `erp_frustum_valid` and `erp_corner_directions`: preferred geometry for equirectangular/360 viewport reconstruction.

Pixel conversion:

```python
x = int(round(u * (width - 1)))
y = int(round((1.0 - v) * (height - 1)))
```

## Metadata Usage Notes

The Unity metadata provides gaze and visible-region state. It does not define the downstream image-processing algorithm.

- `gaze_uv` is valid only when `gaze_hit` is true.
- UV values are normalized in `[0, 1]`.
- Frame-space mapping in NumPy inverts `v`.
- Foveal radius, falloff, quality profile, and fallback policy are outside the Unity metadata contract.
- `uv_polygon` describes the visible surface boundary in UV space.
- Equirectangular sources require ERP-aware handling; `uv_min/uv_max` alone can be ambiguous around the seam.

## Python Extension Points

Relevant Python files:

- `integrations/unity/parsers.py`: typed metadata contract.
- `integrations/unity/handlers.py`: latest viewport state.
- `stream_video.py`: current runtime loop and debug overlay.
- `utils.py`: existing simple frame overlay helpers.

Current runtime shape:

```text
MediaVideoEvent frame
    -> latest UnityViewportMetadata
    -> optional gaze/ROI interpretation
    -> optional downstream frame processing
    -> NativeNdiSender.write_video(...)
```

## Risks and Constraints

| Risk | Impact | Mitigation |
|---|---|---|
| Metadata is latest-state, not frame-locked | Gaze may be slightly ahead/behind video frame processing | Define a freshness policy appropriate to downstream processing |
| No foveal radius in Unity payload | Radius/falloff are not part of the metadata contract | Define these in downstream configuration if needed |
| ERP seam crossing | Naive bbox ROI can wrap incorrectly | Use ERP-aware fields from the metadata contract |
| Missing/stale metadata | Gaze center may be invalid | Gate on `gaze_hit` and metadata age |
| Older Unity builds may lack `schema_version` | Parser sees version `0` | Treat `0` as legacy and prefer the delivered simulator build |

## Validation Checklist

Before adding downstream processing:

- Python dependencies install successfully.
- NDI runtime visibility check prints a version.
- `StreamNDI` appears in NDI Monitor.
- Unity simulator build receives the Python source.
- Python console shows `[RX Viewport]`.
- `gaze_hit` becomes `1` when looking at the NDI surface.
- `gaze_uv` changes as the view/head moves.
- Debug overlay gaze marker follows the expected center.

After adding downstream processing:

- Processing responds to valid `gaze_uv` updates as expected.
- Fallback behavior is stable when Unity stops sending metadata.
- ERP/360 content is tested around the horizontal seam.
- Long-session behavior is tested with representative media.

## Handoff Summary

The gaze/viewport signal is present and parsed in Python. The typed `UnityViewportMetadata` object is the intended integration surface; raw XML remains available for diagnostics via `--rx-metadata-verbose`. Radius/falloff, metadata freshness policy, and ERP seam behavior are intentionally left to downstream processing.
