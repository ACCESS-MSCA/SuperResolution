"""
Stream a video file as an NDI source using direct libndi calls.
Other apps on the network (OBS, NDI Monitor, etc.) can receive it.

Usage:
    python stream_video.py
    python stream_video.py <path_to_video>
    python stream_video.py <path_to_video> --dual
    python stream_video.py <path_to_video> --rx-metadata-verbose
    python stream_video.py <path_to_video> --rx-metadata-log-all
    python stream_video.py <path_to_video> --no-rx-metadata
    python stream_video.py <path_to_video> --uyvy
    python stream_video.py <path_to_video> --diagnostics
    python stream_video.py <path_to_video> --diagnostics-file Logs/run.jsonl
    python stream_video.py <path_to_video> --source-name StreamNDI-Test
    python stream_video.py <path_to_video> --audio-source-name StreamNDI_Audio
    python stream_video.py <path_to_video> --video-prefetch-frames 4 --preload-audio
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from ndi_native import get_ndi_runtime
from extensions.backchannel import MetadataDispatcher, NdiSenderBackchannelReceiver
from integrations.unity import UnityTransformLogHandler, UnityViewportMetadata, UnityViewportStateHandler
from utils import draw_square, make_sender

_ERP_ROI_MASK_CACHE: dict[tuple, np.ndarray] = {}
_ROI_POLYGON_MASK_CACHE: dict[tuple, np.ndarray] = {}
_ERP_ROI_UYVY_PIXEL_CACHE: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}
_ROI_POLYGON_UYVY_PIXEL_CACHE: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}
_GAZE_MARKER_RADIUS_PIXELS = 12
_GAZE_MARKER_THICKNESS_PIXELS = 3
_ERP_FRUSTUM_EDGE_SAMPLES = 96
_AUDIO_LATE_WARN_SECONDS = 0.020
_AUDIO_THREAD_LATE_LOG_INTERVAL = 120
_VIDEO_DROP_LATE_FRAME_FACTOR = 1.0
_VIDEO_DROP_LOG_INTERVAL = 60
_DIAGNOSTIC_INTERVAL_SECONDS = 1.0
_DIAGNOSTIC_VIDEO_GAP_FACTOR = 2.5
_DIAGNOSTIC_AUDIO_GAP_SECONDS = 0.030
_DIAGNOSTIC_SEND_WARN_SECONDS = 0.020
_DIAGNOSTIC_READ_WARN_SECONDS = 0.050
_AUDIO_PRELOAD_MAX_BYTES = 256 * 1024 * 1024
_AUDIO_OUTPUT_SAMPLE_RATE = 48000
_AUDIO_OUTPUT_CHANNELS = 2
_AUDIO_OUTPUT_BYTES_PER_SAMPLE = np.dtype(np.float32).itemsize


class _VideoPrefetcher:
    """Decode video ahead on a bounded worker queue without changing cadence."""

    def __init__(self, reader, capacity: int) -> None:
        self._reader = reader
        self._queue = queue.Queue(maxsize=max(1, int(capacity)))
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="NDI Video Decode Prefetch",
            daemon=True,
        )

    @property
    def capacity(self) -> int:
        return self._queue.maxsize

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=3.0)

    def read_next(self):
        wait_started = time.monotonic()
        kind, payload, read_ms = self._queue.get()
        wait_ms = (time.monotonic() - wait_started) * 1000.0
        if kind == "error":
            raise payload
        return payload, read_ms, wait_ms

    def _put(self, item) -> bool:
        while not self._stop_event.is_set():
            try:
                self._queue.put(item, timeout=0.050)
                return True
            except queue.Full:
                continue
        return False

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                read_started = time.monotonic()
                event = self._reader.read_next()
                read_ms = (time.monotonic() - read_started) * 1000.0
                if not self._put(("event", event, read_ms)):
                    return
        except Exception as exc:
            self._put(("error", exc, 0.0))


class StreamDiagnostics:
    def __init__(self, enabled: bool, output_path: str | None = None) -> None:
        self.enabled = bool(enabled)
        self._lock = threading.Lock()
        self._start = time.monotonic()
        self._next_summary = self._start + _DIAGNOSTIC_INTERVAL_SECONDS
        self.path: Path | None = None
        self._file = None

        if not self.enabled:
            return

        if output_path:
            self.path = Path(output_path)
        else:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.path = Path("Logs") / f"ndi_diagnostics_{stamp}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")
        print(f"[diag] writing diagnostics to {self.path}")

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    def emit(self, event: str, **fields) -> None:
        if not self.enabled:
            return

        payload = {
            "event": event,
            "elapsed": round(time.monotonic() - self._start, 6),
            **fields,
        }
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._lock:
            if self._file is not None:
                self._file.write(line + "\n")
                self._file.flush()

    def maybe_summary(self, **fields) -> None:
        if not self.enabled:
            return

        now = time.monotonic()
        if now < self._next_summary:
            return
        self._next_summary = now + _DIAGNOSTIC_INTERVAL_SECONDS
        self.emit("summary", **fields)
        print(
            "[diag] "
            f"t={fields.get('media_time', 0.0):.3f}s "
            f"video_sent={fields.get('video_sent', 0)} "
            f"video_drops={fields.get('video_drops', 0)} "
            f"audio_events={fields.get('audio_events', 0)} "
            f"audio_late={fields.get('audio_late', 0)} "
            f"video_gap_ms={fields.get('video_gap_ms_max', 0.0):.2f} "
            f"send_video_ms={fields.get('video_send_ms_max', 0.0):.2f} "
            f"send_audio_ms={fields.get('audio_send_ms_max', 0.0):.2f}"
        )


def _configure_audio_frame(sender, sample_rate: int, channels: int, max_samples: int) -> None:
    sender.configure_audio(sample_rate, channels, max_samples)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _clip_polygon_rect(
    poly: list[tuple[float, float]],
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
) -> list[tuple[float, float]]:
    def clip_left(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not points:
            return []
        out: list[tuple[float, float]] = []
        prev = points[-1]
        prev_in = prev[0] >= x_min
        for cur in points:
            cur_in = cur[0] >= x_min
            if cur_in != prev_in:
                dx = cur[0] - prev[0]
                t = 0.0 if abs(dx) < 1e-8 else (x_min - prev[0]) / dx
                out.append((x_min, prev[1] + t * (cur[1] - prev[1])))
            if cur_in:
                out.append(cur)
            prev, prev_in = cur, cur_in
        return out

    def clip_right(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not points:
            return []
        out: list[tuple[float, float]] = []
        prev = points[-1]
        prev_in = prev[0] <= x_max
        for cur in points:
            cur_in = cur[0] <= x_max
            if cur_in != prev_in:
                dx = cur[0] - prev[0]
                t = 0.0 if abs(dx) < 1e-8 else (x_max - prev[0]) / dx
                out.append((x_max, prev[1] + t * (cur[1] - prev[1])))
            if cur_in:
                out.append(cur)
            prev, prev_in = cur, cur_in
        return out

    def clip_bottom(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not points:
            return []
        out: list[tuple[float, float]] = []
        prev = points[-1]
        prev_in = prev[1] >= y_min
        for cur in points:
            cur_in = cur[1] >= y_min
            if cur_in != prev_in:
                dy = cur[1] - prev[1]
                t = 0.0 if abs(dy) < 1e-8 else (y_min - prev[1]) / dy
                out.append((prev[0] + t * (cur[0] - prev[0]), y_min))
            if cur_in:
                out.append(cur)
            prev, prev_in = cur, cur_in
        return out

    def clip_top(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not points:
            return []
        out: list[tuple[float, float]] = []
        prev = points[-1]
        prev_in = prev[1] <= y_max
        for cur in points:
            cur_in = cur[1] <= y_max
            if cur_in != prev_in:
                dy = cur[1] - prev[1]
                t = 0.0 if abs(dy) < 1e-8 else (y_max - prev[1]) / dy
                out.append((prev[0] + t * (cur[0] - prev[0]), y_max))
            if cur_in:
                out.append(cur)
            prev, prev_in = cur, cur_in
        return out

    clipped = clip_left(poly)
    clipped = clip_right(clipped)
    clipped = clip_bottom(clipped)
    clipped = clip_top(clipped)
    return clipped


def _clip_polygon_unit_square(poly: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return _clip_polygon_rect(poly, 0.0, 1.0, 0.0, 1.0)


def _order_polygon_ccw(poly: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(poly) < 3:
        return poly

    # Deduplicate near-identical points first.
    unique: list[tuple[float, float]] = []
    for p in poly:
        if not any(abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6 for q in unique):
            unique.append(p)
    if len(unique) < 3:
        return unique

    cx = sum(p[0] for p in unique) / len(unique)
    cy = sum(p[1] for p in unique) / len(unique)
    return sorted(unique, key=lambda p: np.arctan2(p[1] - cy, p[0] - cx))


def _unwrap_polygon_u(poly: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(poly) < 2:
        return poly

    out = [poly[0]]
    offset = 0.0
    prev_u = poly[0][0]
    for u, v in poly[1:]:
        candidate_u = u + offset
        while candidate_u - prev_u > 0.5:
            offset -= 1.0
            candidate_u = u + offset
        while prev_u - candidate_u > 0.5:
            offset += 1.0
            candidate_u = u + offset

        out.append((candidate_u, v))
        prev_u = candidate_u

    return out


def _rasterize_polyline_outline(mask: np.ndarray, pts: list[tuple[int, int]], thickness: int, closed: bool = False) -> None:
    if len(pts) < 2:
        return

    height, width = mask.shape
    t = max(1, int(thickness))
    segment_count = len(pts) if closed else len(pts) - 1
    for i in range(segment_count):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        xs = np.rint(np.linspace(x0, x1, steps + 1)).astype(np.int32)
        ys = np.rint(np.linspace(y0, y1, steps + 1)).astype(np.int32)
        for x, y in zip(xs, ys):
            x_min = max(0, int(x) - t // 2)
            x_max = min(width, int(x) + (t + 1) // 2)
            y_min = max(0, int(y) - t // 2)
            y_max = min(height, int(y) + (t + 1) // 2)
            mask[y_min:y_max, x_min:x_max] = True


def _rasterize_polygon_outline(mask: np.ndarray, pts: list[tuple[int, int]], thickness: int) -> None:
    _rasterize_polyline_outline(mask, pts, thickness, closed=True)


def _apply_edge_mask(frame_bgra: np.ndarray, edge_mask: np.ndarray, color: np.ndarray) -> None:
    frame_bgra[edge_mask] = color


def _normalize_vector(value: np.ndarray) -> np.ndarray | None:
    length = float(np.linalg.norm(value))
    if length < 1e-6 or not np.isfinite(length):
        return None
    return value / length


def _slerp_direction(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    dot = float(np.clip(np.dot(a, b), -1.0, 1.0))
    if dot > 0.9995:
        blended = a + (b - a) * t
        normalized = _normalize_vector(blended)
        return a if normalized is None else normalized

    theta = np.arccos(dot)
    sin_theta = np.sin(theta)
    if abs(float(sin_theta)) < 1e-6:
        return a

    return (
        np.sin((1.0 - t) * theta) / sin_theta * a
        + np.sin(t * theta) / sin_theta * b
    )


def _direction_to_erp_uv(direction: np.ndarray) -> tuple[float, float]:
    x, y, z = (float(direction[0]), float(direction[1]), float(direction[2]))
    yaw = np.arctan2(x, z)
    pitch = np.arcsin(max(-1.0, min(1.0, y)))
    u = (0.5 + yaw / (2.0 * np.pi)) % 1.0
    v = max(0.0, min(1.0, 0.5 + pitch / np.pi))
    return float(u), float(v)


def _split_erp_polyline_at_seam(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    if len(points) < 2:
        return []

    segments: list[list[tuple[float, float]]] = [[points[0]]]
    previous_u, previous_v = points[0]

    for current_u, current_v in points[1:]:
        delta_u = current_u - previous_u
        if delta_u < -0.5:
            adjusted_u = current_u + 1.0
            factor = (1.0 - previous_u) / max(1e-6, adjusted_u - previous_u)
            seam_v = previous_v + factor * (current_v - previous_v)
            segments[-1].append((1.0, seam_v))
            segments.append([(0.0, seam_v), (current_u, current_v)])
        elif delta_u > 0.5:
            adjusted_u = current_u - 1.0
            factor = (0.0 - previous_u) / min(-1e-6, adjusted_u - previous_u)
            seam_v = previous_v + factor * (current_v - previous_v)
            segments[-1].append((0.0, seam_v))
            segments.append([(1.0, seam_v), (current_u, current_v)])
        else:
            segments[-1].append((current_u, current_v))

        previous_u, previous_v = current_u, current_v

    return [segment for segment in segments if len(segment) >= 2]


def _draw_equirectangular_frustum_roi(frame_bgra: np.ndarray, viewport: UnityViewportMetadata, thickness: int, color: np.ndarray) -> bool:
    height, width = frame_bgra.shape[0], frame_bgra.shape[1]
    if height < 2 or width < 2:
        return False

    t = max(1, int(thickness))
    corners = np.asarray(viewport.erp_corner_directions, dtype=np.float32)
    if corners.shape != (4, 3):
        return False

    normalized_corners: list[np.ndarray] = []
    for corner in corners:
        normalized = _normalize_vector(corner)
        if normalized is None:
            return False
        normalized_corners.append(normalized)

    corner_key = tuple(tuple(round(float(component), 6) for component in corner) for corner in normalized_corners)
    cache_key = (width, height, viewport.sequence, t, _ERP_FRUSTUM_EDGE_SAMPLES, corner_key)
    cached = _ERP_ROI_MASK_CACHE.get(cache_key)
    if cached is not None:
        edge_full = cached
    else:
        edge_full = np.zeros((height, width), dtype=bool)
        samples = max(8, int(_ERP_FRUSTUM_EDGE_SAMPLES))

        for edge_index in range(4):
            start = normalized_corners[edge_index]
            end = normalized_corners[(edge_index + 1) & 3]
            uv_points = [
                _direction_to_erp_uv(_slerp_direction(start, end, i / samples))
                for i in range(samples + 1)
            ]

            for segment in _split_erp_polyline_at_seam(uv_points):
                pts = [
                    (
                        int(round(_clamp01(u) * (width - 1))),
                        int(round((1.0 - _clamp01(v)) * (height - 1))),
                    )
                    for u, v in segment
                ]
                _rasterize_polyline_outline(edge_full, pts, t, closed=False)

        if not np.any(edge_full):
            return False
        _ERP_ROI_MASK_CACHE.clear()
        _ERP_ROI_MASK_CACHE[cache_key] = edge_full

    _apply_edge_mask(frame_bgra, edge_full, color)
    return True


def _draw_gaze_hit_marker(frame_bgra: np.ndarray, viewport: UnityViewportMetadata) -> None:
    if not viewport.gaze_hit:
        return

    u, v = viewport.gaze_uv
    if not (np.isfinite(u) and np.isfinite(v)):
        return
    if u < 0.0 or u > 1.0 or v < 0.0 or v > 1.0:
        return

    height, width = frame_bgra.shape[0], frame_bgra.shape[1]
    if height < 2 or width < 2:
        return

    x = int(round(u * (width - 1)))
    y = int(round((1.0 - v) * (height - 1)))
    radius = min(_GAZE_MARKER_RADIUS_PIXELS, max(2, min(width, height) // 8))
    thickness = max(1, _GAZE_MARKER_THICKNESS_PIXELS)
    shadow = np.array([0, 0, 0, 255], dtype=np.uint8)
    color = np.array([0, 255, 255, 255], dtype=np.uint8)  # BGRA yellow.

    def draw_cross(c: np.ndarray, r: int, t: int) -> None:
        half = max(0, t // 2)
        x0 = max(0, x - r)
        x1 = min(width, x + r + 1)
        y0 = max(0, y - half)
        y1 = min(height, y + half + 1)
        frame_bgra[y0:y1, x0:x1] = c

        x0 = max(0, x - half)
        x1 = min(width, x + half + 1)
        y0 = max(0, y - r)
        y1 = min(height, y + r + 1)
        frame_bgra[y0:y1, x0:x1] = c

    draw_cross(shadow, radius + 1, thickness + 2)
    draw_cross(color, radius, thickness)


def _draw_uv_polygon_roi(
    frame_bgra: np.ndarray,
    viewport: UnityViewportMetadata,
    thickness: int,
    color: np.ndarray,
    is_equirectangular: bool,
) -> bool:
    height, width = frame_bgra.shape[0], frame_bgra.shape[1]
    if height < 2 or width < 2:
        return False

    corners = list(viewport.uv_polygon)
    if len(corners) < 3:
        return False

    t = max(1, int(thickness))
    polygon_key = tuple((round(float(u), 6), round(float(v), 6)) for u, v in corners)
    cache_key = (
        width,
        height,
        viewport.sequence,
        t,
        is_equirectangular,
        viewport.contains_north_pole,
        viewport.contains_south_pole,
        polygon_key,
    )
    cached = _ROI_POLYGON_MASK_CACHE.get(cache_key)
    if cached is not None:
        _apply_edge_mask(frame_bgra, cached, color)
        return True

    ordered = _unwrap_polygon_u(corners) if is_equirectangular else corners
    drew_any = False
    edge_mask = np.zeros((height, width), dtype=bool)

    if is_equirectangular:
        u_min = min(u for u, _ in ordered)
        u_max = max(u for u, _ in ordered)
        tile_min = int(np.floor(u_min))
        tile_max = int(np.floor(u_max))
        if abs(u_max - tile_max) < 1e-6:
            tile_max -= 1
        tile_max = max(tile_min, tile_max)
        tile_ranges = [(float(tile), float(tile + 1)) for tile in range(tile_min, tile_max + 1)]
    else:
        tile_ranges = [(0.0, 1.0)]

    for tile_left, tile_right in tile_ranges:
        polygon = ordered
        if is_equirectangular and viewport.contains_north_pole and not viewport.contains_south_pole:
            polygon = ordered + [
                (ordered[-1][0], 1.0),
                (tile_right, 1.0),
                (tile_left, 1.0),
                (ordered[0][0], 1.0),
            ]
        elif is_equirectangular and viewport.contains_south_pole and not viewport.contains_north_pole:
            polygon = ordered + [
                (ordered[-1][0], 0.0),
                (tile_right, 0.0),
                (tile_left, 0.0),
                (ordered[0][0], 0.0),
            ]

        clipped = _clip_polygon_rect(polygon, tile_left, tile_right, 0.0, 1.0) if is_equirectangular else _clip_polygon_unit_square(polygon)
        if len(clipped) < 3:
            continue

        pts = []
        for u, v in clipped:
            draw_u = (u - tile_left) if is_equirectangular else _clamp01(u)
            x = int(round(_clamp01(draw_u) * (width - 1)))
            y = int(round((1.0 - _clamp01(v)) * (height - 1)))
            pts.append((x, y))

        if len(pts) < 3:
            continue

        drew_any = True
        _rasterize_polygon_outline(edge_mask, pts, t)

    if drew_any:
        _ROI_POLYGON_MASK_CACHE.clear()
        _ROI_POLYGON_MASK_CACHE[cache_key] = edge_mask
        _apply_edge_mask(frame_bgra, edge_mask, color)
    return drew_any


def _draw_viewport_roi(frame_bgra: np.ndarray, viewport: UnityViewportMetadata, thickness: int = 4) -> None:
    if not viewport.plane_intersection and not viewport.gaze_hit:
        return

    # BGRA red
    color = np.array([0, 0, 255, 255], dtype=np.uint8)
    t = max(1, int(thickness))
    is_equirectangular = viewport.uv_projection == "EquirectangularSphere"

    # ERP uses the actual viewport corner directions sent by Unity. Each side
    # is drawn as a great-circle arc, then split at the equirectangular seam.
    if is_equirectangular and viewport.erp_frustum_valid:
        if _draw_equirectangular_frustum_roi(frame_bgra, viewport, t, color):
            _draw_gaze_hit_marker(frame_bgra, viewport)
            return

    # Polygon path is the efficient default for planes and the fallback for ERP
    # if frustum metadata is unavailable.
    if _draw_uv_polygon_roi(frame_bgra, viewport, t, color, is_equirectangular):
        _draw_gaze_hit_marker(frame_bgra, viewport)
        return

    _draw_gaze_hit_marker(frame_bgra, viewport)


def _rgb_to_uyvy_bt709_limited(red: int, green: int, blue: int) -> tuple[int, int, int]:
    """Return U, Y and V bytes for limited-range BT.709 UYVY video."""
    r = _clamp01(float(red) / 255.0)
    g = _clamp01(float(green) / 255.0)
    b = _clamp01(float(blue) / 255.0)
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    y = 16.0 + 219.0 * luma
    u = 128.0 + 224.0 * ((b - luma) / (2.0 * (1.0 - 0.0722)))
    v = 128.0 + 224.0 * ((r - luma) / (2.0 * (1.0 - 0.2126)))
    return (
        int(np.clip(np.rint(u), 0, 255)),
        int(np.clip(np.rint(y), 0, 255)),
        int(np.clip(np.rint(v), 0, 255)),
    )


def _uyvy_pair_view(frame_uyvy: np.ndarray) -> np.ndarray:
    """Expose packed UYVY bytes as [height, pixel_pair, U/Y0/V/Y1]."""
    if frame_uyvy.dtype != np.uint8 or frame_uyvy.ndim != 2:
        raise ValueError("UYVY frame must be a two-dimensional uint8 array.")
    if frame_uyvy.shape[1] % 4 != 0:
        raise ValueError("UYVY rows must contain complete four-byte pixel pairs.")
    if not frame_uyvy.flags.c_contiguous:
        raise ValueError("UYVY frame must be C-contiguous.")
    if not frame_uyvy.flags.writeable:
        raise ValueError("UYVY frame must be writeable for in-place overlay drawing.")
    return frame_uyvy.reshape(frame_uyvy.shape[0], frame_uyvy.shape[1] // 4, 4)


def _empty_pixel_indices() -> tuple[np.ndarray, np.ndarray]:
    return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int32)


def _rasterize_polyline_pixels(
    width: int,
    height: int,
    pts: list[tuple[int, int]],
    thickness: int,
    closed: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """Rasterize an outline to sparse y/x indices without a full-frame mask."""
    if len(pts) < 2 or width < 1 or height < 1:
        return _empty_pixel_indices()

    t = max(1, int(thickness))
    offset_values = np.arange(-(t // 2), (t + 1) // 2, dtype=np.int32)
    offset_x, offset_y = np.meshgrid(offset_values, offset_values)
    offset_x = offset_x.ravel()
    offset_y = offset_y.ravel()
    xs_chunks: list[np.ndarray] = []
    ys_chunks: list[np.ndarray] = []
    segment_count = len(pts) if closed else len(pts) - 1

    for i in range(segment_count):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        center_x = np.rint(np.linspace(x0, x1, steps + 1)).astype(np.int32)
        center_y = np.rint(np.linspace(y0, y1, steps + 1)).astype(np.int32)
        xs = (center_x[:, None] + offset_x[None, :]).ravel()
        ys = (center_y[:, None] + offset_y[None, :]).ravel()
        valid = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
        if np.any(valid):
            xs_chunks.append(xs[valid])
            ys_chunks.append(ys[valid])

    if not xs_chunks:
        return _empty_pixel_indices()
    return np.concatenate(ys_chunks), np.concatenate(xs_chunks)


def _combine_pixel_indices(
    chunks: list[tuple[np.ndarray, np.ndarray]],
) -> tuple[np.ndarray, np.ndarray]:
    populated = [(ys, xs) for ys, xs in chunks if ys.size]
    if not populated:
        return _empty_pixel_indices()
    return (
        np.concatenate([ys for ys, _ in populated]),
        np.concatenate([xs for _, xs in populated]),
    )


def _apply_uyvy_pixels(
    pair_view: np.ndarray,
    ys: np.ndarray,
    xs: np.ndarray,
    color: tuple[int, int, int],
) -> None:
    if ys.size == 0:
        return
    u, y, v = color
    pair_x = xs >> 1
    pair_view[ys, pair_x, 0] = u
    pair_view[ys, pair_x, 2] = v
    even = (xs & 1) == 0
    pair_view[ys[even], pair_x[even], 1] = y
    pair_view[ys[~even], pair_x[~even], 3] = y


def _fill_uyvy_rect(
    pair_view: np.ndarray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
) -> None:
    height, pair_width = pair_view.shape[:2]
    width = pair_width * 2
    x0 = max(0, min(width, int(x0)))
    x1 = max(0, min(width, int(x1)))
    y0 = max(0, min(height, int(y0)))
    y1 = max(0, min(height, int(y1)))
    if x1 <= x0 or y1 <= y0:
        return

    u, y, v = color
    first_pair = x0 // 2
    last_pair = (x1 + 1) // 2
    pair_view[y0:y1, first_pair:last_pair, 0] = u
    pair_view[y0:y1, first_pair:last_pair, 2] = v

    first_even_pair = (x0 + 1) // 2
    last_even_pair = (x1 + 1) // 2
    pair_view[y0:y1, first_even_pair:last_even_pair, 1] = y
    first_odd_pair = x0 // 2
    last_odd_pair = x1 // 2
    pair_view[y0:y1, first_odd_pair:last_odd_pair, 3] = y


def _draw_equirectangular_frustum_roi_uyvy(
    pair_view: np.ndarray,
    viewport: UnityViewportMetadata,
    thickness: int,
    color: tuple[int, int, int],
) -> bool:
    height, width = pair_view.shape[0], pair_view.shape[1] * 2
    if height < 2 or width < 2:
        return False

    t = max(1, int(thickness))
    corners = np.asarray(viewport.erp_corner_directions, dtype=np.float32)
    if corners.shape != (4, 3):
        return False

    normalized_corners: list[np.ndarray] = []
    for corner in corners:
        normalized = _normalize_vector(corner)
        if normalized is None:
            return False
        normalized_corners.append(normalized)

    corner_key = tuple(
        tuple(round(float(component), 6) for component in corner)
        for corner in normalized_corners
    )
    cache_key = (width, height, t, _ERP_FRUSTUM_EDGE_SAMPLES, corner_key)
    cached = _ERP_ROI_UYVY_PIXEL_CACHE.get(cache_key)
    if cached is None:
        chunks: list[tuple[np.ndarray, np.ndarray]] = []
        samples = max(8, int(_ERP_FRUSTUM_EDGE_SAMPLES))
        for edge_index in range(4):
            start = normalized_corners[edge_index]
            end = normalized_corners[(edge_index + 1) & 3]
            uv_points = [
                _direction_to_erp_uv(_slerp_direction(start, end, i / samples))
                for i in range(samples + 1)
            ]
            for segment in _split_erp_polyline_at_seam(uv_points):
                pts = [
                    (
                        int(round(_clamp01(u) * (width - 1))),
                        int(round((1.0 - _clamp01(v)) * (height - 1))),
                    )
                    for u, v in segment
                ]
                chunks.append(
                    _rasterize_polyline_pixels(width, height, pts, t, closed=False)
                )
        cached = _combine_pixel_indices(chunks)
        if cached[0].size == 0:
            return False
        _ERP_ROI_UYVY_PIXEL_CACHE.clear()
        _ERP_ROI_UYVY_PIXEL_CACHE[cache_key] = cached

    _apply_uyvy_pixels(pair_view, cached[0], cached[1], color)
    return True


def _draw_uv_polygon_roi_uyvy(
    pair_view: np.ndarray,
    viewport: UnityViewportMetadata,
    thickness: int,
    color: tuple[int, int, int],
    is_equirectangular: bool,
) -> bool:
    height, width = pair_view.shape[0], pair_view.shape[1] * 2
    if height < 2 or width < 2:
        return False

    corners = list(viewport.uv_polygon)
    if len(corners) < 3:
        return False

    t = max(1, int(thickness))
    polygon_key = tuple((round(float(u), 6), round(float(v), 6)) for u, v in corners)
    cache_key = (
        width,
        height,
        t,
        is_equirectangular,
        viewport.contains_north_pole,
        viewport.contains_south_pole,
        polygon_key,
    )
    cached = _ROI_POLYGON_UYVY_PIXEL_CACHE.get(cache_key)
    if cached is not None:
        _apply_uyvy_pixels(pair_view, cached[0], cached[1], color)
        return True

    ordered = _unwrap_polygon_u(corners) if is_equirectangular else corners
    if is_equirectangular:
        u_min = min(u for u, _ in ordered)
        u_max = max(u for u, _ in ordered)
        tile_min = int(np.floor(u_min))
        tile_max = int(np.floor(u_max))
        if abs(u_max - tile_max) < 1e-6:
            tile_max -= 1
        tile_max = max(tile_min, tile_max)
        tile_ranges = [(float(tile), float(tile + 1)) for tile in range(tile_min, tile_max + 1)]
    else:
        tile_ranges = [(0.0, 1.0)]

    chunks: list[tuple[np.ndarray, np.ndarray]] = []
    for tile_left, tile_right in tile_ranges:
        polygon = ordered
        if is_equirectangular and viewport.contains_north_pole and not viewport.contains_south_pole:
            polygon = ordered + [
                (ordered[-1][0], 1.0),
                (tile_right, 1.0),
                (tile_left, 1.0),
                (ordered[0][0], 1.0),
            ]
        elif is_equirectangular and viewport.contains_south_pole and not viewport.contains_north_pole:
            polygon = ordered + [
                (ordered[-1][0], 0.0),
                (tile_right, 0.0),
                (tile_left, 0.0),
                (ordered[0][0], 0.0),
            ]

        clipped = (
            _clip_polygon_rect(polygon, tile_left, tile_right, 0.0, 1.0)
            if is_equirectangular
            else _clip_polygon_unit_square(polygon)
        )
        if len(clipped) < 3:
            continue

        pts = []
        for u, v in clipped:
            draw_u = (u - tile_left) if is_equirectangular else _clamp01(u)
            pts.append(
                (
                    int(round(_clamp01(draw_u) * (width - 1))),
                    int(round((1.0 - _clamp01(v)) * (height - 1))),
                )
            )
        chunks.append(_rasterize_polyline_pixels(width, height, pts, t, closed=True))

    cached = _combine_pixel_indices(chunks)
    if cached[0].size == 0:
        return False
    _ROI_POLYGON_UYVY_PIXEL_CACHE.clear()
    _ROI_POLYGON_UYVY_PIXEL_CACHE[cache_key] = cached
    _apply_uyvy_pixels(pair_view, cached[0], cached[1], color)
    return True


def _draw_gaze_hit_marker_uyvy(
    pair_view: np.ndarray,
    viewport: UnityViewportMetadata,
) -> None:
    if not viewport.gaze_hit:
        return

    u, v = viewport.gaze_uv
    if not (np.isfinite(u) and np.isfinite(v)):
        return
    if u < 0.0 or u > 1.0 or v < 0.0 or v > 1.0:
        return

    height, width = pair_view.shape[0], pair_view.shape[1] * 2
    if height < 2 or width < 2:
        return

    x = int(round(u * (width - 1)))
    y = int(round((1.0 - v) * (height - 1)))
    radius = min(_GAZE_MARKER_RADIUS_PIXELS, max(2, min(width, height) // 8))
    thickness = max(1, _GAZE_MARKER_THICKNESS_PIXELS)
    shadow = _rgb_to_uyvy_bt709_limited(0, 0, 0)
    yellow = _rgb_to_uyvy_bt709_limited(255, 255, 0)

    def draw_cross(color: tuple[int, int, int], r: int, t: int) -> None:
        half = max(0, t // 2)
        _fill_uyvy_rect(pair_view, x - r, y - half, x + r + 1, y + half + 1, color)
        _fill_uyvy_rect(pair_view, x - half, y - r, x + half + 1, y + r + 1, color)

    draw_cross(shadow, radius + 1, thickness + 2)
    draw_cross(yellow, radius, thickness)


def _draw_viewport_roi_uyvy(
    frame_uyvy: np.ndarray,
    viewport: UnityViewportMetadata,
    thickness: int = 4,
) -> None:
    """Draw viewport ROI directly into a packed UYVY 4:2:2 frame in-place."""
    if not viewport.plane_intersection and not viewport.gaze_hit:
        return

    pair_view = _uyvy_pair_view(frame_uyvy)
    red = _rgb_to_uyvy_bt709_limited(255, 0, 0)
    t = max(1, int(thickness))
    is_equirectangular = viewport.uv_projection == "EquirectangularSphere"

    if is_equirectangular and viewport.erp_frustum_valid:
        if _draw_equirectangular_frustum_roi_uyvy(pair_view, viewport, t, red):
            _draw_gaze_hit_marker_uyvy(pair_view, viewport)
            return

    if _draw_uv_polygon_roi_uyvy(pair_view, viewport, t, red, is_equirectangular):
        _draw_gaze_hit_marker_uyvy(pair_view, viewport)
        return

    _draw_gaze_hit_marker_uyvy(pair_view, viewport)


def _wait_until_media_deadline(
    playback_start_monotonic: float,
    first_media_time_seconds: float,
    media_time_seconds: float,
    late_count: int,
) -> tuple[float, int]:
    target_time = _media_deadline(playback_start_monotonic, first_media_time_seconds, media_time_seconds)
    now = time.monotonic()
    sleep_for = target_time - now

    if sleep_for > 0.0:
        time.sleep(sleep_for)
        return playback_start_monotonic, late_count

    late_seconds = -sleep_for
    if late_seconds > _AUDIO_LATE_WARN_SECONDS:
        late_count += 1
        if late_count % 120 == 0:
            print(
                f"[warn] timing late {late_count} times "
                f"(latest overrun: {late_seconds * 1000.0:.2f} ms)"
            )

    # Never rebase video independently after a long decode/send stall. Audio
    # uses this original media clock too; moving only the video origin makes
    # every rebase permanent A/V drift. The video loop drops expired frames
    # instead, so both streams remain on the same source timeline.
    return playback_start_monotonic, late_count


def _media_deadline(
    playback_start_monotonic: float,
    first_media_time_seconds: float,
    media_time_seconds: float,
) -> float:
    return playback_start_monotonic + (media_time_seconds - first_media_time_seconds)


def _deadline_lateness_seconds(
    playback_start_monotonic: float,
    first_media_time_seconds: float,
    media_time_seconds: float,
) -> float:
    deadline = _media_deadline(playback_start_monotonic, first_media_time_seconds, media_time_seconds)
    return max(0.0, time.monotonic() - deadline)


def _send_audio_event(sender_plain, sender_overlay, sender_audio, event) -> None:
    # A dedicated audio-only NDI source keeps the tiny PCM stream off the
    # high-bandwidth 8K receiver connection. When it is configured, do not
    # duplicate audio into the primary video source.
    if sender_audio is not None:
        sender_audio.write_audio(event.samples)
    else:
        sender_plain.write_audio(event.samples)
    if sender_overlay is not None:
        sender_overlay.write_audio(event.samples)


def _preload_audio_events_ffmpeg(video_path: str, loop_duration_seconds: float):
    from media_reader import MediaAudioEvent

    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(video_path),
            "-vn", "-ac", "2", "-ar", "48000", "-f", "f32le", "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"FFmpeg audio preload failed: {error or result.returncode}")

    interleaved = np.frombuffer(result.stdout, dtype="<f4")
    usable_values = (interleaved.size // 2) * 2
    if usable_values <= 0:
        raise RuntimeError("FFmpeg audio preload produced no samples.")

    planar = np.ascontiguousarray(
        interleaved[:usable_values].reshape(-1, 2).T,
        dtype=np.float32,
    )
    expected_samples = max(1, int(round(loop_duration_seconds * 48000.0)))
    if planar.shape[1] > expected_samples:
        planar = planar[:, :expected_samples]
    elif planar.shape[1] < expected_samples:
        padded = np.zeros((2, expected_samples), dtype=np.float32)
        padded[:, :planar.shape[1]] = planar
        planar = padded

    events = []
    for start in range(0, expected_samples, 1024):
        end = min(expected_samples, start + 1024)
        events.append(
            MediaAudioEvent(
                start / 48000.0,
                np.ascontiguousarray(planar[:, start:end], dtype=np.float32),
            )
        )
    return events


def _wait_until_audio_deadline(
    playback_start_monotonic: float,
    first_media_time_seconds: float,
    media_time_seconds: float,
    stop_event: threading.Event,
    stats: dict,
) -> bool:
    target_time = _media_deadline(playback_start_monotonic, first_media_time_seconds, media_time_seconds)

    while True:
        sleep_for = target_time - time.monotonic()
        if sleep_for <= 0.0:
            break
        if stop_event.wait(min(sleep_for, 0.005)):
            return False

    late_seconds = time.monotonic() - target_time
    if late_seconds > _AUDIO_LATE_WARN_SECONDS:
        stats["late_count"] += 1
        stats["late_ms_max"] = max(stats.get("late_ms_max", 0.0), late_seconds * 1000.0)
        if stats["late_count"] % _AUDIO_THREAD_LATE_LOG_INTERVAL == 0:
            print(
                f"[warn] audio timing late {stats['late_count']} times "
                f"(latest overrun: {late_seconds * 1000.0:.2f} ms)"
            )

    return True


def _run_audio_sender(
    video_path: str,
    sender_plain,
    sender_overlay,
    sender_audio,
    worker_ready: threading.Event,
    clock_ready: threading.Event,
    stop_event: threading.Event,
    clock_state: dict,
    clock_lock: threading.Lock,
    stats: dict,
    diagnostics: StreamDiagnostics,
    preload_audio: bool,
    loop_duration_seconds: float,
) -> None:
    from media_reader import LoopingMediaReader, MediaAudioEvent

    reader = None
    last_audio_media_time = None
    try:
        cached_events = None
        cached_event_index = 0
        cached_loop_offset = 0.0
        cached_loop_duration = loop_duration_seconds
        if preload_audio:
            preload_started = time.monotonic()
            try:
                cached_events = _preload_audio_events_ffmpeg(
                    video_path,
                    cached_loop_duration,
                )
                preload_ms = (time.monotonic() - preload_started) * 1000.0
                diagnostics.emit(
                    "audio_preloaded",
                    events=len(cached_events),
                    preload_ms=round(preload_ms, 3),
                    duration=round(cached_loop_duration, 6),
                    backend="ffmpeg",
                )
                print(
                    f"[info] audio preloaded: {len(cached_events)} events in "
                    f"{preload_ms:.1f} ms (FFmpeg)"
                )
            except Exception as exc:
                cached_events = None
                diagnostics.emit("audio_preload_fallback", error=str(exc))
                print(f"[warn] audio preload failed; using streaming fallback: {exc}")

        if cached_events is None:
            reader = LoopingMediaReader(
                video_path,
                audio_sample_rate=48000,
                audio_channels=2,
                decode_video=False,
                decode_audio=True,
            )

        worker_ready.set()

        while not stop_event.is_set():
            if clock_ready.wait(0.005):
                break
        if stop_event.is_set():
            return

        with clock_lock:
            playback_start_monotonic = clock_state["playback_start_monotonic"]
            first_media_time_seconds = clock_state["first_media_time_seconds"]

        while not stop_event.is_set():
            read_started = time.monotonic()
            if cached_events is not None:
                base_event = cached_events[cached_event_index]
                event = MediaAudioEvent(
                    cached_loop_offset + base_event.media_time_seconds,
                    base_event.samples,
                )
                cached_event_index += 1
                if cached_event_index >= len(cached_events):
                    cached_event_index = 0
                    cached_loop_offset += cached_loop_duration
            else:
                event = reader.read_next()
            read_ms = (time.monotonic() - read_started) * 1000.0
            stats["read_ms_max"] = max(stats.get("read_ms_max", 0.0), read_ms)
            if read_ms > _DIAGNOSTIC_READ_WARN_SECONDS * 1000.0:
                stats["read_slow_count"] = stats.get("read_slow_count", 0) + 1
                diagnostics.emit(
                    "audio_reader_slow",
                    read_ms=round(read_ms, 3),
                    media_time=round(getattr(event, "media_time_seconds", 0.0), 6),
                )
            if not isinstance(event, MediaAudioEvent):
                continue

            sample_count = int(event.samples.shape[1])
            duration_seconds = sample_count / 48000.0
            if last_audio_media_time is not None:
                media_gap = event.media_time_seconds - last_audio_media_time
                expected_gap = duration_seconds
                if media_gap - expected_gap > _DIAGNOSTIC_AUDIO_GAP_SECONDS:
                    stats["media_gap_count"] = stats.get("media_gap_count", 0) + 1
                    stats["media_gap_ms_max"] = max(
                        stats.get("media_gap_ms_max", 0.0),
                        (media_gap - expected_gap) * 1000.0,
                    )
                    diagnostics.emit(
                        "audio_media_gap",
                        media_gap_ms=round(media_gap * 1000.0, 3),
                        expected_ms=round(expected_gap * 1000.0, 3),
                        media_time=round(event.media_time_seconds, 6),
                        samples=sample_count,
                    )
            last_audio_media_time = event.media_time_seconds
            stats["samples"] = stats.get("samples", 0) + sample_count
            stats["chunk_samples_max"] = max(stats.get("chunk_samples_max", 0), sample_count)

            if not _wait_until_audio_deadline(
                playback_start_monotonic,
                first_media_time_seconds,
                event.media_time_seconds,
                stop_event,
                stats,
            ):
                break

            send_started = time.monotonic()
            _send_audio_event(sender_plain, sender_overlay, sender_audio, event)
            send_ms = (time.monotonic() - send_started) * 1000.0
            stats["send_ms_max"] = max(stats.get("send_ms_max", 0.0), send_ms)
            if send_ms > _DIAGNOSTIC_SEND_WARN_SECONDS * 1000.0:
                stats["send_slow_count"] = stats.get("send_slow_count", 0) + 1
                diagnostics.emit(
                    "audio_send_slow",
                    send_ms=round(send_ms, 3),
                    media_time=round(event.media_time_seconds, 6),
                    samples=sample_count,
                )
            stats["events"] += 1
            stats["last_sent_media_time"] = event.media_time_seconds

    except Exception as exc:
        stats["error"] = exc
        worker_ready.set()
        stop_event.set()
    finally:
        if reader is not None:
            reader.close()


def stream_video(
    video_path: str,
    source_name: str = "StreamNDI",
    audio_source_name: str | None = None,
    dual: bool = False,
    rx_metadata: bool = True,
    rx_metadata_verbose: bool = False,
    rx_metadata_log_all: bool = False,
    video_pixel_format: str = "bgra",
    diagnostics_enabled: bool = False,
    diagnostics_file: str | None = None,
    video_prefetch_frames: int = 0,
    preload_audio: bool = False,
):
    # Initialize NDI before PyAV loads FFmpeg dylibs. This avoids the macOS
    # AVFoundation class collision becoming part of sender creation.
    get_ndi_runtime()
    from media_reader import LoopingMediaReader, MediaAudioEvent, MediaVideoEvent

    diagnostics = StreamDiagnostics(diagnostics_enabled, diagnostics_file)
    media_reader = LoopingMediaReader(
        video_path,
        audio_sample_rate=48000,
        audio_channels=2,
        decode_video=True,
        decode_audio=False,
        video_pixel_format=video_pixel_format,
    )
    media_info = media_reader.info

    preload_audio_requested = bool(preload_audio)
    audio_preload_duration_seconds = float(media_info.duration_seconds)
    audio_preload_duration_valid = bool(
        np.isfinite(audio_preload_duration_seconds)
        and audio_preload_duration_seconds > 0.0
    )
    audio_preload_estimated_bytes = int(
        audio_preload_duration_seconds
        * _AUDIO_OUTPUT_SAMPLE_RATE
        * _AUDIO_OUTPUT_CHANNELS
        * _AUDIO_OUTPUT_BYTES_PER_SAMPLE
    ) if audio_preload_duration_valid else 0
    preload_audio = bool(
        preload_audio_requested
        and audio_preload_duration_valid
        and audio_preload_estimated_bytes <= _AUDIO_PRELOAD_MAX_BYTES
    )
    if preload_audio_requested and not preload_audio:
        if not audio_preload_duration_valid:
            print("[info] audio preload disabled because source duration is unavailable")
        else:
            print(
                "[info] audio preload disabled by memory budget "
                f"({audio_preload_estimated_bytes / (1024 * 1024):.1f} MiB > "
                f"{_AUDIO_PRELOAD_MAX_BYTES / (1024 * 1024):.0f} MiB)"
            )

    width = media_info.width
    height = media_info.height
    fps = media_info.fps
    total_frames = media_info.total_video_frames
    fps_float = float(fps)
    expected_video_interval = 1.0 / max(fps_float, 1.0)
    video_drop_late_threshold = max(
        0.020,
        expected_video_interval * _VIDEO_DROP_LATE_FRAME_FACTOR,
    )

    print(f"Source  : {video_path}")
    print(f"Size    : {width}x{height} @ {fps_float:.3f} fps ({total_frames} frames)")

    if dual and media_info.video_pixel_format != "bgra":
        raise ValueError("The --dual overlay output requires BGRA video.")

    sender_plain, _ = make_sender(
        source_name,
        width,
        height,
        fps,
        video_pixel_format=media_info.video_pixel_format,
    )
    print(f"NDI name: '{source_name}'")
    print(f"Pixels  : {media_info.video_pixel_format.upper()}")
    diagnostics.emit(
        "stream_start",
        video_path=str(video_path),
        source_name=str(source_name),
        width=width,
        height=height,
        fps_num=int(fps.numerator),
        fps_den=int(fps.denominator),
        fps=round(fps_float, 6),
        total_frames=total_frames,
        pixel_format=media_info.video_pixel_format,
        video_prefetch_frames=max(0, int(video_prefetch_frames)),
        preload_audio=bool(preload_audio),
        preload_audio_requested=preload_audio_requested,
        audio_preload_estimated_mib=round(
            audio_preload_estimated_bytes / (1024 * 1024),
            3,
        ),
        audio_preload_budget_mib=round(
            _AUDIO_PRELOAD_MAX_BYTES / (1024 * 1024),
            3,
        ),
        audio_source_name=audio_source_name,
        video_drop_late_ms=round(video_drop_late_threshold * 1000.0, 3),
    )

    audio_enabled = media_info.audio_enabled
    sender_audio = None
    if audio_enabled:
        _configure_audio_frame(
            sender_plain,
            media_info.audio_sample_rate,
            media_info.audio_channels,
            media_info.audio_max_samples_per_chunk,
        )
        print(
            f"Audio   : {media_info.audio_sample_rate} Hz, {media_info.audio_channels} ch, "
            f"up to {media_info.audio_max_samples_per_chunk} samples/chunk"
        )

        if audio_source_name:
            if audio_source_name == source_name:
                raise ValueError("Dedicated audio source name must differ from the video source name.")
            sender_audio, _ = make_sender(
                audio_source_name,
                width,
                height,
                fps,
                video_pixel_format=media_info.video_pixel_format,
                # The audio worker already waits on the shared media deadline.
                # Enabling the NDI sender clock here would pace every chunk a
                # second time, turning transient send stalls into audio bursts.
                clock_audio=False,
            )
            _configure_audio_frame(
                sender_audio,
                media_info.audio_sample_rate,
                media_info.audio_channels,
                media_info.audio_max_samples_per_chunk,
            )
            print(f"NDI audio: '{audio_source_name}' (dedicated audio-only source)")
    else:
        print("Audio   : disabled (source has no audio stream)")

    if rx_metadata:
        print("Backchannel: enabled (receiver -> sender metadata)")

    if rx_metadata_log_all:
        print("[info] --rx-metadata-log-all is a legacy compatibility flag (no effect).")

    sender_overlay = None
    if dual:
        overlay_name = f"{source_name}-Square"
        sender_overlay, _ = make_sender(
            overlay_name,
            width,
            height,
            fps,
            video_pixel_format=media_info.video_pixel_format,
        )
        print(f"NDI name: '{overlay_name}'")
        if audio_enabled:
            _configure_audio_frame(
                sender_overlay,
                media_info.audio_sample_rate,
                media_info.audio_channels,
                media_info.audio_max_samples_per_chunk,
            )

    print("Press Ctrl-C to stop.\n")

    video_frame_idx = 0
    playback_start_monotonic = None
    first_media_time_seconds = None
    late_count = 0
    video_drop_count = 0
    video_sent_count = 0
    video_decode_read_ms_max = 0.0
    video_decode_slow_count = 0
    video_prefetch_wait_ms_max = 0.0
    video_send_ms_max = 0.0
    video_send_slow_count = 0
    video_late_ms_max = 0.0
    video_gap_ms_max = 0.0
    video_gap_count = 0
    last_video_send_monotonic = None
    audio_thread = None
    audio_stop_event = None
    audio_worker_ready = None
    audio_clock_ready = None
    audio_clock_lock = threading.Lock()
    audio_clock_state = {
        "playback_start_monotonic": None,
        "first_media_time_seconds": None,
    }
    audio_stats = {
        "events": 0,
        "late_count": 0,
        "late_ms_max": 0.0,
        "samples": 0,
        "chunk_samples_max": 0,
        "media_gap_count": 0,
        "media_gap_ms_max": 0.0,
        "send_ms_max": 0.0,
        "send_slow_count": 0,
        "read_ms_max": 0.0,
        "read_slow_count": 0,
        "last_sent_media_time": None,
        "error": None,
    }

    backchannel = None
    dispatcher = None
    viewport_handler = None
    viewport_stale_timeout_seconds = 3.0
    video_prefetcher = None

    with sender_plain:
        if sender_overlay is not None:
            sender_overlay.__enter__()
        if sender_audio is not None:
            sender_audio.__enter__()

        if audio_enabled:
            audio_stop_event = threading.Event()
            audio_worker_ready = threading.Event()
            audio_clock_ready = threading.Event()
            audio_thread = threading.Thread(
                target=_run_audio_sender,
                args=(
                    video_path,
                    sender_plain,
                    sender_overlay,
                    sender_audio,
                    audio_worker_ready,
                    audio_clock_ready,
                    audio_stop_event,
                    audio_clock_state,
                    audio_clock_lock,
                    audio_stats,
                    diagnostics,
                    preload_audio,
                    media_info.duration_seconds,
                ),
                name="NDI Audio Sender",
                daemon=True,
            )
            audio_thread.start()
            audio_ready_timeout = 30.0 if preload_audio else 5.0
            if not audio_worker_ready.wait(timeout=audio_ready_timeout):
                raise RuntimeError(
                    f"Audio sender thread did not become ready within {audio_ready_timeout:.0f} seconds."
                )
            if audio_stats["error"] is not None:
                raise RuntimeError(f"Audio sender thread failed during startup: {audio_stats['error']}")
            print("[info] audio sender thread ready")

        if rx_metadata:
            try:
                viewport_handler = UnityViewportStateHandler()
                backchannel = NdiSenderBackchannelReceiver(
                    sender_plain,
                    timeout_ms=0,
                    idle_sleep_seconds=0.001,
                    max_queue_size=1024,
                )
                backchannel.start()
                dispatcher = MetadataDispatcher(
                    handlers=[
                        UnityTransformLogHandler(),
                        viewport_handler,
                    ],
                    verbose_raw_xml=rx_metadata_verbose,
                    log_unhandled=False,
                )
                print("[info] backchannel receiver started")
            except Exception as exc:
                backchannel = None
                dispatcher = None
                viewport_handler = None
                print(f"[warn] could not start backchannel receiver: {exc}")

        if video_prefetch_frames > 0:
            video_prefetcher = _VideoPrefetcher(media_reader, video_prefetch_frames)
            video_prefetcher.start()
            print(f"[info] video prefetch enabled: {video_prefetcher.capacity} frames")

        try:
            while True:
                if backchannel is not None and dispatcher is not None:
                    messages = backchannel.drain(max_messages=32)
                    if messages:
                        dispatcher.dispatch_many(messages)

                read_started = time.monotonic()
                if video_prefetcher is not None:
                    event, read_ms, prefetch_wait_ms = video_prefetcher.read_next()
                    video_prefetch_wait_ms_max = max(
                        video_prefetch_wait_ms_max,
                        prefetch_wait_ms,
                    )
                else:
                    event = media_reader.read_next()
                    read_ms = (time.monotonic() - read_started) * 1000.0
                video_decode_read_ms_max = max(video_decode_read_ms_max, read_ms)
                if read_ms > _DIAGNOSTIC_READ_WARN_SECONDS * 1000.0:
                    video_decode_slow_count += 1
                    diagnostics.emit(
                        "video_reader_slow",
                        read_ms=round(read_ms, 3),
                        media_time=round(getattr(event, "media_time_seconds", 0.0), 6),
                    )
                if event is None:
                    print("Error: failed to decode media event.")
                    break

                if playback_start_monotonic is None or first_media_time_seconds is None:
                    playback_start_monotonic = time.monotonic()
                    first_media_time_seconds = event.media_time_seconds
                    if audio_clock_ready is not None:
                        with audio_clock_lock:
                            audio_clock_state["playback_start_monotonic"] = playback_start_monotonic
                            audio_clock_state["first_media_time_seconds"] = first_media_time_seconds
                        audio_clock_ready.set()

                playback_start_monotonic, late_count = _wait_until_media_deadline(
                    playback_start_monotonic,
                    first_media_time_seconds,
                    event.media_time_seconds,
                    late_count,
                )

                if isinstance(event, MediaVideoEvent):
                    video_late_seconds = _deadline_lateness_seconds(
                        playback_start_monotonic,
                        first_media_time_seconds,
                        event.media_time_seconds,
                    )
                    video_late_ms_max = max(
                        video_late_ms_max,
                        video_late_seconds * 1000.0,
                    )
                    if video_late_seconds > video_drop_late_threshold:
                        video_drop_count += 1
                        if video_drop_count % _VIDEO_DROP_LOG_INTERVAL == 0:
                            print(
                                f"[warn] dropping late video frames to protect audio "
                                f"(drops={video_drop_count}, latest={video_late_seconds * 1000.0:.2f} ms)"
                            )
                            diagnostics.emit(
                                "video_drop",
                                drops=video_drop_count,
                                late_ms=round(video_late_seconds * 1000.0, 3),
                                media_time=round(event.media_time_seconds, 6),
                            )
                        continue

                    frame_data = event.frame_data
                    if viewport_handler is not None and viewport_handler.state.latest is not None:
                        age = time.monotonic() - viewport_handler.state.last_update_monotonic
                        if age <= viewport_stale_timeout_seconds:
                            if media_info.video_pixel_format == "bgra":
                                frame_data = np.array(event.frame_data, copy=True)
                                _draw_viewport_roi(frame_data, viewport_handler.state.latest)
                            elif media_info.video_pixel_format == "uyvy422":
                                if not frame_data.flags.c_contiguous or not frame_data.flags.writeable:
                                    frame_data = np.ascontiguousarray(frame_data)
                                    if not frame_data.flags.writeable:
                                        frame_data = np.array(frame_data, copy=True)
                                _draw_viewport_roi_uyvy(
                                    frame_data,
                                    viewport_handler.state.latest,
                                )

                    plain_frame = frame_data.ravel()
                    send_started = time.monotonic()
                    sender_plain.write_video(plain_frame)
                    send_ms = (time.monotonic() - send_started) * 1000.0
                    video_send_ms_max = max(video_send_ms_max, send_ms)
                    if send_ms > _DIAGNOSTIC_SEND_WARN_SECONDS * 1000.0:
                        video_send_slow_count += 1
                        diagnostics.emit(
                            "video_send_slow",
                            send_ms=round(send_ms, 3),
                            media_time=round(event.media_time_seconds, 6),
                            frame=video_frame_idx,
                        )

                    if sender_overlay is not None:
                        bgra_sq = np.array(frame_data, copy=True)
                        draw_square(bgra_sq, video_frame_idx)
                        overlay_frame = bgra_sq.ravel()
                        sender_overlay.write_video(overlay_frame)

                    now = time.monotonic()
                    if last_video_send_monotonic is not None:
                        video_gap_seconds = now - last_video_send_monotonic
                        video_gap_ms_max = max(video_gap_ms_max, video_gap_seconds * 1000.0)
                        if video_gap_seconds > expected_video_interval * _DIAGNOSTIC_VIDEO_GAP_FACTOR:
                            video_gap_count += 1
                            diagnostics.emit(
                                "video_output_gap",
                                gap_ms=round(video_gap_seconds * 1000.0, 3),
                                expected_ms=round(expected_video_interval * 1000.0, 3),
                                media_time=round(event.media_time_seconds, 6),
                                frame=video_frame_idx,
                            )
                    last_video_send_monotonic = now
                    video_sent_count += 1
                    video_frame_idx += 1
                    diagnostics.maybe_summary(
                        media_time=event.media_time_seconds,
                        video_sent=video_sent_count,
                        video_drops=video_drop_count,
                        video_late=late_count,
                        video_late_ms_max=video_late_ms_max,
                        video_gap_count=video_gap_count,
                        video_gap_ms_max=video_gap_ms_max,
                        video_read_ms_max=video_decode_read_ms_max,
                        video_read_slow=video_decode_slow_count,
                        video_prefetch_depth=(video_prefetcher.depth if video_prefetcher else 0),
                        video_prefetch_wait_ms_max=video_prefetch_wait_ms_max,
                        video_send_ms_max=video_send_ms_max,
                        video_send_slow=video_send_slow_count,
                        video_decode_errors=getattr(media_reader, "video_decode_errors", 0),
                        video_decode_recoveries=getattr(media_reader, "video_decode_recoveries", 0),
                        audio_events=audio_stats["events"],
                        audio_late=audio_stats["late_count"],
                        audio_late_ms_max=audio_stats["late_ms_max"],
                        audio_samples=audio_stats["samples"],
                        audio_chunk_samples_max=audio_stats["chunk_samples_max"],
                        audio_gap_count=audio_stats["media_gap_count"],
                        audio_gap_ms_max=audio_stats["media_gap_ms_max"],
                        audio_send_ms_max=audio_stats["send_ms_max"],
                        audio_send_slow=audio_stats["send_slow_count"],
                        audio_read_ms_max=audio_stats["read_ms_max"],
                        audio_read_slow=audio_stats["read_slow_count"],
                        audio_media_time=audio_stats["last_sent_media_time"],
                        av_media_delta_ms=(
                            None
                            if audio_stats["last_sent_media_time"] is None
                            else round(
                                (audio_stats["last_sent_media_time"] - event.media_time_seconds)
                                * 1000.0,
                                3,
                            )
                        ),
                    )
                    continue

                if isinstance(event, MediaAudioEvent):
                    _send_audio_event(sender_plain, sender_overlay, sender_audio, event)
                    continue

        except KeyboardInterrupt:
            print("\nStopped by user.")

        finally:
            if video_prefetcher is not None:
                video_prefetcher.close()

            if audio_enabled:
                if audio_stop_event is not None:
                    audio_stop_event.set()
                if audio_thread is not None:
                    audio_thread.join(timeout=2.0)
                print(
                    "[info] audio thread stats: "
                    f"events={audio_stats['events']}, "
                    f"late={audio_stats['late_count']}, "
                    f"late_ms_max={audio_stats['late_ms_max']:.2f}, "
                    f"send_ms_max={audio_stats['send_ms_max']:.2f}, "
                    f"gaps={audio_stats['media_gap_count']}"
                )
                print(f"[info] video drops to protect audio: {video_drop_count}")
                if audio_stats["error"] is not None:
                    print(f"[warn] audio thread error: {audio_stats['error']}")

            if backchannel is not None:
                backchannel.stop()
                stats = backchannel.stats_snapshot()
                print(
                    "[info] backchannel stats: "
                    f"received={backchannel.received_messages}, "
                    f"parse_errors={backchannel.parse_errors}, "
                    f"queue_drops={backchannel.dropped_messages}, "
                    f"cap_none={stats['none']}, "
                    f"cap_meta={stats['metadata_frames']}, "
                    f"cap_err={stats['error']}"
                )
                if backchannel.last_error:
                    print(f"[warn] backchannel last error: {backchannel.last_error}")

            media_reader.close()
            if sender_audio is not None:
                sender_audio.__exit__(None, None, None)
            if sender_overlay is not None:
                sender_overlay.__exit__(None, None, None)
            diagnostics.emit(
                "stream_stop",
                video_sent=video_sent_count,
                video_drops=video_drop_count,
                video_gap_count=video_gap_count,
                video_gap_ms_max=round(video_gap_ms_max, 3),
                audio_events=audio_stats["events"],
                audio_late=audio_stats["late_count"],
                audio_gap_count=audio_stats["media_gap_count"],
            )
            diagnostics.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    dual = "--dual" in args
    rx_metadata_verbose = "--rx-metadata-verbose" in args
    rx_metadata_log_all = "--rx-metadata-log-all" in args
    rx_metadata = "--no-rx-metadata" not in args
    video_pixel_format = "uyvy422" if "--uyvy" in args else "bgra"
    diagnostics_enabled = "--diagnostics" in args
    diagnostics_file = None
    video_prefetch_frames = 0
    preload_audio = "--preload-audio" in args
    source_name = "StreamNDI"
    audio_source_name = None

    if "--video-prefetch-frames" in args:
        prefetch_index = args.index("--video-prefetch-frames")
        try:
            video_prefetch_frames = max(0, int(args[prefetch_index + 1]))
        except (IndexError, ValueError) as exc:
            raise SystemExit("--video-prefetch-frames requires a non-negative integer") from exc
        del args[prefetch_index:prefetch_index + 2]

    if "--diagnostics-file" in args:
        diagnostics_file_index = args.index("--diagnostics-file")
        try:
            diagnostics_file = args[diagnostics_file_index + 1]
        except IndexError as exc:
            raise SystemExit("--diagnostics-file requires a path") from exc
        del args[diagnostics_file_index:diagnostics_file_index + 2]
        diagnostics_enabled = True

    if "--source-name" in args:
        source_name_index = args.index("--source-name")
        try:
            source_name = args[source_name_index + 1]
        except IndexError as exc:
            raise SystemExit("--source-name requires a name") from exc
        del args[source_name_index:source_name_index + 2]

    if "--audio-source-name" in args:
        audio_source_name_index = args.index("--audio-source-name")
        try:
            audio_source_name = args[audio_source_name_index + 1]
        except IndexError as exc:
            raise SystemExit("--audio-source-name requires a name") from exc
        del args[audio_source_name_index:audio_source_name_index + 2]

    args = [
        a
        for a in args
        if a not in (
            "--dual",
            "--rx-metadata-verbose",
            "--rx-metadata-log-all",
            "--no-rx-metadata",
            "--uyvy",
            "--diagnostics",
            "--preload-audio",
        )
    ]

    video = args[0] if args else "Videos/big_buck_bunny.mp4"
    stream_video(
        video,
        source_name=source_name,
        audio_source_name=audio_source_name,
        dual=dual,
        rx_metadata=rx_metadata,
        rx_metadata_verbose=rx_metadata_verbose,
        rx_metadata_log_all=rx_metadata_log_all,
        video_pixel_format=video_pixel_format,
        diagnostics_enabled=diagnostics_enabled,
        diagnostics_file=diagnostics_file,
        video_prefetch_frames=video_prefetch_frames,
        preload_audio=preload_audio,
    )
