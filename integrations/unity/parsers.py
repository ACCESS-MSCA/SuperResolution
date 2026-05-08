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
