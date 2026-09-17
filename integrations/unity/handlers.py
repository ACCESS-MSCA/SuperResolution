"""Unity integration handlers built on top of generic backchannel metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import time

from extensions.backchannel.receiver import NdiMetadataMessage

from .parsers import UnityViewportMetadata, try_parse_unity_transform, try_parse_unity_viewport


class UnityTransformLogHandler:
    """Consumes Unity transform metadata and logs a concise trace line."""

    def handle(self, message: NdiMetadataMessage) -> bool:
        transform = try_parse_unity_transform(message)
        if transform is None:
            return False

        px, py, pz = transform.position
        print(
            "[RX Transform] "
            f"seq={transform.sequence} "
            f"scene={transform.scene} "
            f"name={transform.name} "
            f"space={transform.space} "
            f"pos=({px:.3f}, {py:.3f}, {pz:.3f})"
        )
        return True


@dataclass
class UnityViewportState:
    latest: Optional[UnityViewportMetadata] = None
    last_update_monotonic: float = 0.0


class UnityViewportStateHandler:
    """Consumes Unity viewport metadata and keeps the latest ROI state."""

    def __init__(self, log_interval_seconds: float = 1.0) -> None:
        self.state = UnityViewportState()
        self._last_sequence = -1
        self._last_log_monotonic = 0.0
        self._log_interval_seconds = max(0.0, float(log_interval_seconds))

    def handle(self, message: NdiMetadataMessage) -> bool:
        viewport = try_parse_unity_viewport(message)
        if viewport is None:
            return False

        self.state.latest = viewport
        self.state.last_update_monotonic = time.monotonic()
        should_log = (
            viewport.sequence != self._last_sequence
            and self.state.last_update_monotonic - self._last_log_monotonic >= self._log_interval_seconds
        )
        if should_log:
            self._last_sequence = viewport.sequence
            self._last_log_monotonic = self.state.last_update_monotonic
            print(
                "[RX Viewport] "
                f"seq={viewport.sequence} "
                f"scene={viewport.scene} "
                f"uv=({viewport.uv_min[0]:.3f},{viewport.uv_min[1]:.3f})-"
                f"({viewport.uv_max[0]:.3f},{viewport.uv_max[1]:.3f}) "
                f"hit_any={int(viewport.hit_any)} "
                f"plane_intersection={int(viewport.plane_intersection)} "
                f"poly_n={len(viewport.uv_polygon)} "
                f"gaze_hit={int(viewport.gaze_hit)} "
                f"gaze_uv=({viewport.gaze_uv[0]:.3f},{viewport.gaze_uv[1]:.3f}) "
                f"erp_frustum={int(viewport.erp_frustum_valid)} "
                f"north_pole={int(viewport.contains_north_pole)} "
                f"south_pole={int(viewport.contains_south_pole)}"
            )
        return True
