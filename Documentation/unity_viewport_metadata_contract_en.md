# Unity Viewport Metadata Contract

Updated: 2026-07-06

Navigation: [Index](index_en.md) | [ES](unity_viewport_metadata_contract_es.md) | [HTML](unity_viewport_metadata_contract_en.html)

This document defines the metadata emitted by the supplied Unity build to report the visible NDI region and gaze center back to the Python streamer.

## Scope

Unity emits an NDI metadata XML element named `access_viewport`. Python receives it through the NDI sender backchannel and parses it into `UnityViewportMetadata`.

Current implementation:

- Unity producer: `NdiHeadViewportMetadataPayloadProvider`
- Python receiver: `extensions/backchannel/receiver.py`
- Python parser: `integrations/unity/parsers.py`
- Runtime state holder: `integrations/unity/handlers.py`
- Debug overlay consumer: `stream_video.py`

## Integration Surface

The intended Python integration surface is the typed object returned by `try_parse_unity_viewport(...)` and stored in `UnityViewportStateHandler.state.latest`.

Important validity facts:

- `gaze_uv` is valid only when `gaze_hit` is true.
- UV values are normalized floats in `[0, 1]`.
- Unity/texture UV origin is bottom-left.
- NumPy frame origin is top-left, so `v` is inverted when mapping to pixels.
- `uv_min/uv_max` is an axis-aligned UV bounding box and is not sufficient by itself for ERP seam-crossing cases.

Pixel mapping:

```python
pixel_x = int(round(u * (frame_width - 1)))
pixel_y = int(round((1.0 - v) * (frame_height - 1)))
```

## Metadata Flow

```text
Unity head/camera + NDI surface collider
    -> access_viewport XML
    -> NDI metadata backchannel
    -> NdiSenderBackchannelReceiver
    -> try_parse_unity_viewport(...)
    -> UnityViewportMetadata
```

## Example XML

```xml
<access_viewport
  schema_version="1"
  id="unity_receiver"
  seq="42"
  timestamp="123.456789"
  scene="NDI"
  camera="Main Camera"
  cam_px_w="1920"
  cam_px_h="1080"
  head_px="0.001"
  head_py="1.600"
  head_pz="-0.250"
  head_qx="0"
  head_qy="0.120"
  head_qz="0"
  head_qw="0.993"
  hit_any="1"
  hit_center="1"
  gaze_hit="1"
  corner_hits="4"
  plane_intersection="1"
  plane_intersection_any="1"
  uv_poly_n="4"
  uv_projection="MeshTextureCoordinates"
  uv_edge_samples="16"
  uv_contains_north_pole="0"
  uv_contains_south_pole="0"
  erp_frustum_valid="0"
  uv_cx="0.500"
  uv_cy="0.500"
  gaze_uv_x="0.500"
  gaze_uv_y="0.500"
  uv_min_x="0.250"
  uv_min_y="0.250"
  uv_max_x="0.750"
  uv_max_y="0.750"
  uv_poly0_x="0.250"
  uv_poly0_y="0.250"
  uv_poly1_x="0.250"
  uv_poly1_y="0.750"
  uv_poly2_x="0.750"
  uv_poly2_y="0.750"
  uv_poly3_x="0.750"
  uv_poly3_y="0.250" />
```

The real payload may include additional attributes, especially world hit points and equirectangular frustum data.

## Coordinate System

- `u = 0`: left edge of the source image.
- `u = 1`: right edge.
- `v = 0`: bottom edge in Unity/texture UV space.
- `v = 1`: top edge in Unity/texture UV space.

Frame arrays in NumPy use top-left origin:

```python
pixel_x = int(round(u * (frame_width - 1)))
pixel_y = int(round((1.0 - v) * (frame_height - 1)))
```

Clamp UVs to `[0, 1]` before indexing a frame.

## Parsed Python Type

`integrations/unity/parsers.py` exposes:

```python
@dataclass(frozen=True)
class UnityViewportMetadata:
    schema_version: int
    source_id: str
    sequence: int
    scene: str
    camera: str
    uv_projection: str
    hit_any: bool
    plane_intersection: bool
    hit_center: bool
    gaze_hit: bool
    corner_hits: int
    contains_north_pole: bool
    contains_south_pole: bool
    erp_frustum_valid: bool
    erp_edge_normals: tuple[tuple[float, float, float], ...]
    erp_corner_directions: tuple[tuple[float, float, float], ...]
    uv_center: tuple[float, float]
    gaze_uv: tuple[float, float]
    gaze_world: tuple[float, float, float]
    uv_min: tuple[float, float]
    uv_max: tuple[float, float]
    uv_corners: tuple[tuple[float, float], ...]
    uv_corner_direct_hits: tuple[bool, bool, bool, bool]
    uv_polygon: tuple[tuple[float, float], ...]
```

`schema_version == 0` means the payload came from an older Unity build that did not emit `schema_version`.

## Field Reference

| Field | Type | Meaning | Notes |
|---|---:|---|---|
| `schema_version` | int | Contract version. Current version is `1`. | `0` means legacy/no explicit version. |
| `source_id` | str | Unity-side metadata source id. | Diagnostics. |
| `sequence` | int | Monotonic Unity payload sequence. | Useful for repeated/stale payload detection. |
| `scene` | str | Unity scene containing the NDI surface. | Diagnostics. |
| `camera` | str | Unity camera used for projection. | Diagnostics. |
| `uv_projection` | str | `MeshTextureCoordinates`, `EquirectangularSphere`, or effective auto mode. | Determines planar vs ERP interpretation. |
| `hit_any` | bool | At least one direct viewport ray hit the target surface. | Diagnostic confidence. |
| `plane_intersection` | bool | A visible ROI polygon exists on the target surface. | Gate for polygon use. |
| `hit_center` | bool | Center viewport ray directly hit the target surface. | Current source for gaze validity. |
| `gaze_hit` | bool | `gaze_uv` is valid. | Validity gate for `gaze_uv`. |
| `corner_hits` | int | Number of viewport corners with direct collider hits. | Diagnostic confidence. |
| `contains_north_pole` | bool | ERP viewport contains north pole. | ERP polygon handling. |
| `contains_south_pole` | bool | ERP viewport contains south pole. | ERP polygon handling. |
| `erp_frustum_valid` | bool | ERP corner/frustum direction data is valid. | Use when reconstructing ERP frustum geometry. |
| `erp_edge_normals` | 4x vec3 | Great-circle edge normals for ERP frustum. | Advanced ERP containment. |
| `erp_corner_directions` | 4x vec3 | ERP viewport corner directions. | ERP frustum geometry. |
| `uv_center` | vec2 | Center/centroid of visible UV ROI. | Fallback/diagnostic center. |
| `gaze_uv` | vec2 | Direct center gaze UV. | Valid only when `gaze_hit` is true. |
| `gaze_world` | vec3 | World-space hit point for the center ray. | Optional 3D diagnostics. |
| `uv_min` | vec2 | Axis-aligned UV bbox minimum. | Simple planar bounds, not sole ERP representation. |
| `uv_max` | vec2 | Axis-aligned UV bbox maximum. | Simple planar bounds, not sole ERP representation. |
| `uv_corners` | 4x vec2 | Projected viewport corners in BL, TL, TR, BR order. | Debug/legacy reconstruction. |
| `uv_corner_direct_hits` | 4 bools | Whether each corner was a direct hit. | Confidence/debug. |
| `uv_polygon` | N vec2 | Clipped visible ROI polygon. | Visible surface boundary in UV space. |

## ERP/Equirectangular Notes

For `uv_projection == "EquirectangularSphere"`:

- `uv_min/uv_max` can be misleading when the viewport crosses the horizontal seam.
- `uv_polygon`, `contains_north_pole`, `contains_south_pole`, and `erp_corner_directions` provide the ERP-aware geometry emitted by Unity.
- `gaze_uv` remains a normalized UV coordinate and remains gated by `gaze_hit`.

## Freshness and Timing

Unity metadata is delivered as latest-state control data, not as exact per-video-frame metadata. The current debug overlay in `stream_video.py` uses:

```text
viewport_stale_timeout_seconds = 3.0
```

Latency-sensitive processing can choose its own freshness policy based on algorithm and transport requirements.

## Compatibility Rules

- Ignore unknown XML attributes.
- Tolerate missing optional attributes.
- Treat `schema_version == 1` as the current contract.
- Treat `schema_version == 0` as legacy/no explicit version.
- Treat `gaze_uv` as valid only when `gaze_hit` is true.

## Known Boundaries

- Unity does not send foveal radius, falloff, or SR quality parameters.
- Python currently contains a debug overlay, not a production SR processing step.
- ERP seam handling is part of downstream image-space processing.
