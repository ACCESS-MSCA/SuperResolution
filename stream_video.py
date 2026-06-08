"""
Stream a video file as an NDI source using cyndilib.
Other apps on the network (OBS, NDI Monitor, etc.) can receive it.

Usage:
    python stream_video.py
    python stream_video.py <path_to_video>
    python stream_video.py <path_to_video> --dual
    python stream_video.py <path_to_video> --rx-metadata-verbose
    python stream_video.py <path_to_video> --rx-metadata-log-all
    python stream_video.py <path_to_video> --no-rx-metadata
"""

from __future__ import annotations

import sys
import time

import numpy as np
from cyndilib.audio_frame import AudioSendFrame

from core import MonotonicFrameClock
from extensions.backchannel import MetadataDispatcher, NdiSenderBackchannelReceiver
from ffmpeg import decode_audio_to_array, probe_video, read_exact, start_video_decoder
from integrations.unity import UnityTransformLogHandler, UnityViewportMetadata, UnityViewportStateHandler
from utils import draw_square, make_sender

_ERP_ROI_MASK_CACHE: dict[tuple, np.ndarray] = {}
_ROI_POLYGON_MASK_CACHE: dict[tuple, np.ndarray] = {}
_GAZE_MARKER_RADIUS_PIXELS = 12
_GAZE_MARKER_THICKNESS_PIXELS = 3
_ERP_FRUSTUM_EDGE_SAMPLES = 96


def _configure_audio_frame(sender, sample_rate: int, channels: int, max_samples: int) -> None:
    af = AudioSendFrame(max_num_samples=max_samples)
    af.sample_rate = sample_rate
    af.num_channels = channels
    sender.set_audio_frame(af)


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


def stream_video(
    video_path: str,
    source_name: str = "StreamNDI",
    dual: bool = False,
    rx_metadata: bool = True,
    rx_metadata_verbose: bool = False,
    rx_metadata_log_all: bool = False,
):
    width, height, fps, total_frames = probe_video(video_path)
    fps_float = float(fps)

    print(f"Source  : {video_path}")
    print(f"Size    : {width}x{height} @ {fps_float:.3f} fps ({total_frames} frames)")

    audio_sample_rate = 48000
    audio_channels = 2
    samples_per_frame_exact = audio_sample_rate / max(fps_float, 1.0)
    audio_samples_per_frame = max(1, int(round(samples_per_frame_exact)))

    sender_plain, _ = make_sender(source_name, width, height, fps)
    print(f"NDI name: '{source_name}'")
    print(
        f"Audio   : {audio_sample_rate} Hz, {audio_channels} ch, "
        f"{audio_samples_per_frame} samples/frame"
    )

    if abs(samples_per_frame_exact - audio_samples_per_frame) > 1e-6:
        print(
            f"[warn] fractional audio/frame ({samples_per_frame_exact:.6f}); "
            "using fixed-size blocks for channel stability."
        )

    if rx_metadata:
        print("Backchannel: enabled (receiver -> sender metadata)")

    # Legacy flag kept for CLI compatibility. Server already logs all received messages by default.
    if rx_metadata_log_all:
        print("[info] --rx-metadata-log-all is a legacy compatibility flag (no effect).")

    audio_data = decode_audio_to_array(video_path, audio_sample_rate, audio_channels)
    audio_enabled = audio_data is not None and audio_data.shape[1] > 0

    if audio_enabled:
        _configure_audio_frame(sender_plain, audio_sample_rate, audio_channels, audio_samples_per_frame)
    else:
        print("[warn] could not start audio decoder; sending video only.")

    sender_overlay = None
    if dual:
        overlay_name = f"{source_name}-Square"
        sender_overlay, _ = make_sender(overlay_name, width, height, fps)
        print(f"NDI name: '{overlay_name}'")
        if audio_enabled:
            _configure_audio_frame(sender_overlay, audio_sample_rate, audio_channels, audio_samples_per_frame)

    print("Press Ctrl-C to stop.\n")

    frame_idx = 0
    dropped_timing_count = 0
    audio_pos = 0
    total_audio_samples = int(audio_data.shape[1]) if audio_enabled else 0

    frame_bytes = width * height * 4
    video_proc = start_video_decoder(video_path)
    if video_proc.stdout is None:
        print("Error: failed to start video decoder stdout pipe.")
        sys.exit(1)

    clock = MonotonicFrameClock(fps_float)
    backchannel = None
    dispatcher = None
    viewport_handler = None
    viewport_stale_timeout_seconds = 3.0

    with sender_plain:
        if sender_overlay is not None:
            sender_overlay.__enter__()

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

        try:
            while True:
                raw = read_exact(video_proc.stdout, frame_bytes)
                if raw is None:
                    if video_proc.poll() is None:
                        video_proc.terminate()
                    video_proc = start_video_decoder(video_path)
                    if video_proc.stdout is None:
                        print("Error: failed to restart video decoder.")
                        break

                    clock.reset()
                    audio_pos = 0
                    raw = read_exact(video_proc.stdout, frame_bytes)
                    if raw is None:
                        print("Error: failed to read first frame after decoder restart")
                        break

                if backchannel is not None and dispatcher is not None:
                    messages = backchannel.drain(max_messages=32)
                    if messages:
                        dispatcher.dispatch_many(messages)

                # frombuffer(raw, ...) over bytes is read-only; cyndilib expects writable memory
                bgra = np.frombuffer(raw, dtype=np.uint8).copy().reshape((height, width, 4))
                if viewport_handler is not None and viewport_handler.state.latest is not None:
                    age = time.monotonic() - viewport_handler.state.last_update_monotonic
                    if age <= viewport_stale_timeout_seconds:
                        _draw_viewport_roi(bgra, viewport_handler.state.latest)
                plain_frame = bgra.ravel()

                if audio_enabled and total_audio_samples > 0:
                    end_pos = audio_pos + audio_samples_per_frame
                    if end_pos <= total_audio_samples:
                        audio_frame = audio_data[:, audio_pos:end_pos].copy()
                        audio_pos = end_pos
                        if audio_pos >= total_audio_samples:
                            audio_pos = 0
                    else:
                        first = audio_data[:, audio_pos:total_audio_samples]
                        remain = end_pos - total_audio_samples
                        second = audio_data[:, 0:remain]
                        audio_frame = np.concatenate((first, second), axis=1).copy()
                        audio_pos = remain

                    sender_plain.write_video_and_audio(plain_frame, audio_frame)

                    if sender_overlay is not None:
                        bgra_sq = np.array(bgra, copy=True)
                        draw_square(bgra_sq, frame_idx)
                        overlay_frame = bgra_sq.ravel()
                        sender_overlay.write_video_and_audio(overlay_frame, audio_frame)
                else:
                    sender_plain.write_video_async(plain_frame)
                    if sender_overlay is not None:
                        bgra_sq = np.array(bgra, copy=True)
                        draw_square(bgra_sq, frame_idx)
                        overlay_frame = bgra_sq.ravel()
                        sender_overlay.write_video_async(overlay_frame)

                frame_idx += 1

                overrun = clock.wait_next()
                if overrun > 0:
                    dropped_timing_count += 1
                    if dropped_timing_count % 120 == 0:
                        print(
                            f"[warn] timing late {dropped_timing_count} times "
                            f"(latest overrun: {overrun * 1000:.2f} ms)"
                        )

        except KeyboardInterrupt:
            print("\nStopped by user.")

        finally:
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

            if video_proc.poll() is None:
                video_proc.terminate()
            if sender_overlay is not None:
                sender_overlay.__exit__(None, None, None)


if __name__ == "__main__":
    args = sys.argv[1:]
    dual = "--dual" in args
    rx_metadata_verbose = "--rx-metadata-verbose" in args
    rx_metadata_log_all = "--rx-metadata-log-all" in args
    rx_metadata = "--no-rx-metadata" not in args

    args = [
        a
        for a in args
        if a not in ("--dual", "--rx-metadata-verbose", "--rx-metadata-log-all", "--no-rx-metadata")
    ]

    video = args[0] if args else "Videos/big_buck_bunny.mp4"
    stream_video(
        video,
        dual=dual,
        rx_metadata=rx_metadata,
        rx_metadata_verbose=rx_metadata_verbose,
        rx_metadata_log_all=rx_metadata_log_all,
    )
