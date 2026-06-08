"""Unity metadata parsers.

This module is intentionally integration-specific. New Unity payload types
(e.g. gaze, interaction, object state) should be added here as separate parsers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from extensions.backchannel.receiver import NdiMetadataMessage


@dataclass(frozen=True)
class UnityTransformMetadata:
    source_id: str
    sequence: int
    scene: str
    name: str
    space: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    scale: Optional[tuple[float, float, float]]


@dataclass(frozen=True)
class UnityViewportMetadata:
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
    erp_edge_normals: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
    erp_corner_directions: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
    uv_center: tuple[float, float]
    gaze_uv: tuple[float, float]
    gaze_world: tuple[float, float, float]
    uv_min: tuple[float, float]
    uv_max: tuple[float, float]
    uv_corners: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]
    uv_corner_direct_hits: tuple[bool, bool, bool, bool]
    uv_polygon: tuple[tuple[float, float], ...]


def try_parse_unity_transform(message: NdiMetadataMessage) -> Optional[UnityTransformMetadata]:
    if message.tag != "access_transform":
        return None

    attrs = message.attrs

    def _f(name: str, default: float = 0.0) -> float:
        value = attrs.get(name)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            return default

    def _i(name: str, default: int = 0) -> int:
        value = attrs.get(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    scale = None
    if "sx" in attrs and "sy" in attrs and "sz" in attrs:
        scale = (_f("sx"), _f("sy"), _f("sz"))

    return UnityTransformMetadata(
        source_id=attrs.get("id", ""),
        sequence=_i("seq"),
        scene=attrs.get("scene", ""),
        name=attrs.get("name", ""),
        space=attrs.get("space", ""),
        position=(_f("px"), _f("py"), _f("pz")),
        rotation=(_f("qx"), _f("qy"), _f("qz"), _f("qw")),
        scale=scale,
    )


def try_parse_unity_viewport(message: NdiMetadataMessage) -> Optional[UnityViewportMetadata]:
    if message.tag != "access_viewport":
        return None

    attrs = message.attrs

    def _f(name: str, default: float = 0.0) -> float:
        value = attrs.get(name)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            return default

    def _i(name: str, default: int = 0) -> int:
        value = attrs.get(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def _b(name: str) -> bool:
        return attrs.get(name, "0") in ("1", "true", "True")

    poly_n = max(0, min(128, _i("uv_poly_n")))
    poly: list[tuple[float, float]] = []
    for i in range(poly_n):
        poly.append((_f(f"uv_poly{i}_x"), _f(f"uv_poly{i}_y")))

    return UnityViewportMetadata(
        source_id=attrs.get("id", ""),
        sequence=_i("seq"),
        scene=attrs.get("scene", ""),
        camera=attrs.get("camera", ""),
        uv_projection=attrs.get("uv_projection", ""),
        hit_any=_b("hit_any"),
        plane_intersection=_b("plane_intersection"),
        hit_center=_b("hit_center"),
        gaze_hit=_b("gaze_hit") or _b("hit_center"),
        corner_hits=_i("corner_hits"),
        contains_north_pole=_b("uv_contains_north_pole"),
        contains_south_pole=_b("uv_contains_south_pole"),
        erp_frustum_valid=_b("erp_frustum_valid"),
        erp_edge_normals=(
            (_f("erp_edge0_nx"), _f("erp_edge0_ny"), _f("erp_edge0_nz")),
            (_f("erp_edge1_nx"), _f("erp_edge1_ny"), _f("erp_edge1_nz")),
            (_f("erp_edge2_nx"), _f("erp_edge2_ny"), _f("erp_edge2_nz")),
            (_f("erp_edge3_nx"), _f("erp_edge3_ny"), _f("erp_edge3_nz")),
        ),
        erp_corner_directions=(
            (_f("erp_corner0_x"), _f("erp_corner0_y"), _f("erp_corner0_z")),
            (_f("erp_corner1_x"), _f("erp_corner1_y"), _f("erp_corner1_z")),
            (_f("erp_corner2_x"), _f("erp_corner2_y"), _f("erp_corner2_z")),
            (_f("erp_corner3_x"), _f("erp_corner3_y"), _f("erp_corner3_z")),
        ),
        uv_center=(_f("uv_cx"), _f("uv_cy")),
        gaze_uv=(
            _f("gaze_uv_x", _f("uv_cx")),
            _f("gaze_uv_y", _f("uv_cy")),
        ),
        gaze_world=(
            _f("gaze_world_x", _f("world_cx")),
            _f("gaze_world_y", _f("world_cy")),
            _f("gaze_world_z", _f("world_cz")),
        ),
        uv_min=(_f("uv_min_x"), _f("uv_min_y")),
        uv_max=(_f("uv_max_x"), _f("uv_max_y")),
        uv_corners=(
            (_f("uv00_x"), _f("uv00_y")),  # BL
            (_f("uv01_x"), _f("uv01_y")),  # TL
            (_f("uv11_x"), _f("uv11_y")),  # TR
            (_f("uv10_x"), _f("uv10_y")),  # BR
        ),
        uv_corner_direct_hits=(
            _b("uv00_hit"),
            _b("uv01_hit"),
            _b("uv11_hit"),
            _b("uv10_hit"),
        ),
        uv_polygon=tuple(poly),
    )
