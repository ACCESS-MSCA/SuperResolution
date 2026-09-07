import math
import unittest

import numpy as np

from integrations.unity import UnityViewportMetadata
from stream_video import (
    _apply_uyvy_pixels,
    _draw_viewport_roi_uyvy,
    _rgb_to_uyvy_bt709_limited,
    _uyvy_pair_view,
)


def _viewport(**overrides) -> UnityViewportMetadata:
    values = {
        "schema_version": 1,
        "source_id": "test",
        "sequence": 1,
        "scene": "Cine",
        "camera": "Main Camera",
        "uv_projection": "Planar",
        "hit_any": True,
        "plane_intersection": True,
        "hit_center": False,
        "gaze_hit": False,
        "corner_hits": 4,
        "contains_north_pole": False,
        "contains_south_pole": False,
        "erp_frustum_valid": False,
        "erp_edge_normals": ((0.0, 0.0, 0.0),) * 4,
        "erp_corner_directions": ((0.0, 0.0, 0.0),) * 4,
        "uv_center": (0.5, 0.5),
        "gaze_uv": (0.5, 0.5),
        "gaze_world": (0.0, 0.0, 0.0),
        "uv_min": (0.25, 0.25),
        "uv_max": (0.75, 0.75),
        "uv_corners": ((0.25, 0.25), (0.25, 0.75), (0.75, 0.75), (0.75, 0.25)),
        "uv_corner_direct_hits": (True, True, True, True),
        "uv_polygon": ((0.25, 0.25), (0.25, 0.75), (0.75, 0.75), (0.75, 0.25)),
    }
    values.update(overrides)
    return UnityViewportMetadata(**values)


def _neutral_frame(width: int, height: int) -> np.ndarray:
    frame = np.empty((height, width * 2), dtype=np.uint8)
    frame.reshape(height, width // 2, 4)[:] = (128, 16, 128, 16)
    return frame


def _direction(yaw_degrees: float, pitch_degrees: float) -> tuple[float, float, float]:
    yaw = math.radians(yaw_degrees)
    pitch = math.radians(pitch_degrees)
    cos_pitch = math.cos(pitch)
    return (
        math.sin(yaw) * cos_pitch,
        math.sin(pitch),
        math.cos(yaw) * cos_pitch,
    )


class UyvyViewportOverlayTests(unittest.TestCase):
    def test_bt709_limited_overlay_colors(self) -> None:
        self.assertEqual(_rgb_to_uyvy_bt709_limited(255, 0, 0), (102, 63, 240))
        self.assertEqual(_rgb_to_uyvy_bt709_limited(255, 255, 0), (16, 219, 138))
        self.assertEqual(_rgb_to_uyvy_bt709_limited(0, 0, 0), (128, 16, 128))

    def test_pixel_writer_preserves_unselected_luma_in_shared_pair(self) -> None:
        frame = _neutral_frame(4, 2)
        pairs = _uyvy_pair_view(frame)
        _apply_uyvy_pixels(
            pairs,
            np.asarray([0], dtype=np.int32),
            np.asarray([0], dtype=np.int32),
            (102, 63, 240),
        )
        self.assertEqual(tuple(pairs[0, 0]), (102, 63, 240, 16))

    def test_planar_roi_is_drawn_in_place_without_touching_frame_center(self) -> None:
        frame = _neutral_frame(32, 16)
        address_before = frame.__array_interface__["data"][0]
        _draw_viewport_roi_uyvy(frame, _viewport(), thickness=1)
        self.assertEqual(frame.__array_interface__["data"][0], address_before)

        pairs = _uyvy_pair_view(frame)
        red = (102, 63, 240)
        self.assertEqual(tuple(pairs[4, 4, :3]), red)
        self.assertEqual(tuple(pairs[8, 8]), (128, 16, 128, 16))

    def test_gaze_marker_is_yellow_and_can_be_drawn_without_plane_roi(self) -> None:
        frame = _neutral_frame(64, 32)
        viewport = _viewport(
            plane_intersection=False,
            gaze_hit=True,
            uv_polygon=(),
            gaze_uv=(0.5, 0.5),
        )
        _draw_viewport_roi_uyvy(frame, viewport, thickness=1)
        pairs = _uyvy_pair_view(frame)
        x = int(round(0.5 * 63))
        y = int(round(0.5 * 31))
        pixel_pair = pairs[y, x // 2]
        self.assertEqual((int(pixel_pair[0]), int(pixel_pair[2])), (16, 138))
        self.assertIn(219, (int(pixel_pair[1]), int(pixel_pair[3])))

    def test_erp_frustum_crossing_seam_draws_on_both_horizontal_edges(self) -> None:
        frame = _neutral_frame(128, 64)
        viewport = _viewport(
            uv_projection="EquirectangularSphere",
            erp_frustum_valid=True,
            erp_corner_directions=(
                _direction(170.0, -10.0),
                _direction(170.0, 10.0),
                _direction(-170.0, 10.0),
                _direction(-170.0, -10.0),
            ),
            uv_polygon=(),
        )
        _draw_viewport_roi_uyvy(frame, viewport, thickness=1)
        pairs = _uyvy_pair_view(frame)
        changed_pairs = (pairs[:, :, 0] == 102) & (pairs[:, :, 2] == 240)
        self.assertTrue(np.any(changed_pairs[:, :2]))
        self.assertTrue(np.any(changed_pairs[:, -2:]))

    def test_overlay_is_noop_without_plane_or_gaze_hit(self) -> None:
        frame = _neutral_frame(16, 8)
        expected = frame.copy()
        _draw_viewport_roi_uyvy(
            frame,
            _viewport(plane_intersection=False, gaze_hit=False),
        )
        np.testing.assert_array_equal(frame, expected)

    def test_invalid_packed_row_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "four-byte pixel pairs"):
            _uyvy_pair_view(np.zeros((2, 6), dtype=np.uint8))


if __name__ == "__main__":
    unittest.main()
