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

_ERP_DIRECTION_GRID_CACHE: dict[tuple[int, int], np.ndarray] = {}
_ERP_ROI_MASK_CACHE: dict[tuple[int, int, int], tuple[np.ndarray, np.ndarray]] = {}


def _configure_audio_frame(sender, sample_rate: int, channels: int, max_samples: int) -> None:
    af = AudioSendFrame(max_num_samples=max_samples)
    af.sample_rate = sample_rate
    af.num_channels = channels
    sender.set_audio_frame(af)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _clip_polygon_unit_square(poly: list[tuple[float, float]]) -> list[tuple[float, float]]:
    def clip_left(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not points:
            return []
        out: list[tuple[float, float]] = []
        prev = points[-1]
        prev_in = prev[0] >= 0.0
        for cur in points:
            cur_in = cur[0] >= 0.0
            if cur_in != prev_in:
                dx = cur[0] - prev[0]
                t = 0.0 if abs(dx) < 1e-8 else (0.0 - prev[0]) / dx
                out.append((0.0, prev[1] + t * (cur[1] - prev[1])))
            if cur_in:
                out.append(cur)
            prev, prev_in = cur, cur_in
        return out

    def clip_right(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not points:
            return []
        out: list[tuple[float, float]] = []
        prev = points[-1]
        prev_in = prev[0] <= 1.0
        for cur in points:
            cur_in = cur[0] <= 1.0
            if cur_in != prev_in:
                dx = cur[0] - prev[0]
                t = 0.0 if abs(dx) < 1e-8 else (1.0 - prev[0]) / dx
                out.append((1.0, prev[1] + t * (cur[1] - prev[1])))
            if cur_in:
                out.append(cur)
            prev, prev_in = cur, cur_in
        return out

    def clip_bottom(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not points:
            return []
        out: list[tuple[float, float]] = []
        prev = points[-1]
        prev_in = prev[1] >= 0.0
        for cur in points:
            cur_in = cur[1] >= 0.0
            if cur_in != prev_in:
                dy = cur[1] - prev[1]
                t = 0.0 if abs(dy) < 1e-8 else (0.0 - prev[1]) / dy
                out.append((prev[0] + t * (cur[0] - prev[0]), 0.0))
            if cur_in:
                out.append(cur)
            prev, prev_in = cur, cur_in
        return out

    def clip_top(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not points:
            return []
        out: list[tuple[float, float]] = []
        prev = points[-1]
        prev_in = prev[1] <= 1.0
        for cur in points:
            cur_in = cur[1] <= 1.0
            if cur_in != prev_in:
                dy = cur[1] - prev[1]
                t = 0.0 if abs(dy) < 1e-8 else (1.0 - prev[1]) / dy
                out.append((prev[0] + t * (cur[0] - prev[0]), 1.0))
            if cur_in:
                out.append(cur)
            prev, prev_in = cur, cur_in
        return out

    clipped = clip_left(poly)
    clipped = clip_right(clipped)
    clipped = clip_bottom(clipped)
    clipped = clip_top(clipped)
    return clipped


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


def _fill_polygon(frame_bgra: np.ndarray, pts: list[tuple[int, int]], color: np.ndarray, alpha: float) -> None:
    if len(pts) < 3:
        return

    height, width = frame_bgra.shape[0], frame_bgra.shape[1]
    y_min = max(0, min(p[1] for p in pts))
    y_max = min(height - 1, max(p[1] for p in pts))
    if y_min > y_max:
        return

    blend = max(0.0, min(1.0, float(alpha)))
    if blend <= 0.0:
        return

    src_rgb = color[:3].astype(np.float32)
    for y in range(y_min, y_max + 1):
        intersections: list[float] = []
        for i in range(len(pts)):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % len(pts)]
            if y0 == y1:
                continue

            if (y >= min(y0, y1)) and (y < max(y0, y1)):
                t = (y - y0) / (y1 - y0)
                intersections.append(x0 + t * (x1 - x0))

        if len(intersections) < 2:
            continue

        intersections.sort()
        for i in range(0, len(intersections) - 1, 2):
            x_min = max(0, int(np.ceil(intersections[i])))
            x_max = min(width - 1, int(np.floor(intersections[i + 1])))
            if x_min > x_max:
                continue

            dst = frame_bgra[y, x_min:x_max + 1, :3].astype(np.float32)
            frame_bgra[y, x_min:x_max + 1, :3] = (dst * (1.0 - blend) + src_rgb * blend).astype(np.uint8)
            frame_bgra[y, x_min:x_max + 1, 3] = 255


def _get_erp_direction_grid(width: int, height: int) -> np.ndarray:
    key = (width, height)
    cached = _ERP_DIRECTION_GRID_CACHE.get(key)
    if cached is not None:
        return cached

    u = np.linspace(0.0, 1.0, width, dtype=np.float32)
    v = np.linspace(1.0, 0.0, height, dtype=np.float32)
    yaw = (u - 0.5) * (2.0 * np.pi)
    pitch = (v - 0.5) * np.pi
    sin_yaw = np.sin(yaw).astype(np.float32)
    cos_yaw = np.cos(yaw).astype(np.float32)
    sin_pitch = np.sin(pitch).astype(np.float32)
    cos_pitch = np.cos(pitch).astype(np.float32)[:, None]

    directions = np.empty((height, width, 3), dtype=np.float32)
    directions[..., 0] = cos_pitch * sin_yaw[None, :]
    directions[..., 1] = sin_pitch[:, None]
    directions[..., 2] = cos_pitch * cos_yaw[None, :]
    _ERP_DIRECTION_GRID_CACHE[key] = directions
    return directions


def _draw_equirectangular_frustum_roi(frame_bgra: np.ndarray, viewport: UnityViewportMetadata, thickness: int, color: np.ndarray) -> bool:
    height, width = frame_bgra.shape[0], frame_bgra.shape[1]
    cache_key = (width, height, viewport.sequence)
    cached = _ERP_ROI_MASK_CACHE.get(cache_key)
    if cached is not None:
        mask, edge = cached
    else:
        normals = np.asarray(viewport.erp_edge_normals, dtype=np.float32)
        if normals.shape != (4, 3):
            return False

        lengths = np.linalg.norm(normals, axis=1)
        if np.any(lengths < 1e-6):
            return False

        normals = normals / lengths[:, None]
        directions = _get_erp_direction_grid(width, height)
        dots = np.tensordot(directions, normals, axes=([2], [1]))
        mask = np.all(dots >= -1e-5, axis=2)
        if not np.any(mask):
            return False

        edge = np.zeros_like(mask)
        vertical = mask[1:, :] ^ mask[:-1, :]
        edge[1:, :] |= vertical
        edge[:-1, :] |= vertical
        edge |= mask ^ np.roll(mask, 1, axis=1)
        edge |= mask ^ np.roll(mask, -1, axis=1)

        _ERP_ROI_MASK_CACHE.clear()
        _ERP_ROI_MASK_CACHE[cache_key] = (mask, edge)

    src_rgb = color[:3].astype(np.float32)
    dst = frame_bgra[..., :3].astype(np.float32)
    dst[mask] = dst[mask] * 0.82 + src_rgb * 0.18
    frame_bgra[..., :3] = dst.astype(np.uint8)
    frame_bgra[..., 3] = 255

    t = max(1, int(thickness))
    if t > 1:
        expanded = edge.copy()
        radius = max(1, t // 2)
        for offset in range(1, radius + 1):
            expanded |= np.roll(edge, offset, axis=0)
            expanded |= np.roll(edge, -offset, axis=0)
            expanded |= np.roll(edge, offset, axis=1)
            expanded |= np.roll(edge, -offset, axis=1)
        edge = expanded

    frame_bgra[edge] = color
    return True


def _draw_viewport_roi(frame_bgra: np.ndarray, viewport: UnityViewportMetadata, thickness: int = 4) -> None:
    if not viewport.plane_intersection:
        return

    height, width = frame_bgra.shape[0], frame_bgra.shape[1]
    if height < 2 or width < 2:
        return

    corners = list(viewport.uv_polygon)
    if len(corners) < 3:
        return

    # BGRA red
    color = np.array([0, 0, 255, 255], dtype=np.uint8)
    t = max(1, int(thickness))

    is_equirectangular = viewport.uv_projection == "EquirectangularSphere"
    if is_equirectangular and viewport.erp_frustum_valid:
        if _draw_equirectangular_frustum_roi(frame_bgra, viewport, t, color):
            return

    ordered = _unwrap_polygon_u(corners) if is_equirectangular else corners
    u_shifts = (-1.0, 0.0, 1.0) if is_equirectangular else (0.0,)

    for u_shift in u_shifts:
        shifted = [(u + u_shift, v) for u, v in ordered]
        if is_equirectangular and viewport.contains_north_pole and not viewport.contains_south_pole:
            shifted = shifted + [(shifted[-1][0], 1.0), (1.0, 1.0), (0.0, 1.0), (shifted[0][0], 1.0)]
        elif is_equirectangular and viewport.contains_south_pole and not viewport.contains_north_pole:
            shifted = shifted + [(shifted[-1][0], 0.0), (1.0, 0.0), (0.0, 0.0), (shifted[0][0], 0.0)]

        clipped = _clip_polygon_unit_square(shifted)
        if len(clipped) < 3:
            continue

        pts = []
        for u, v in clipped:
            x = int(round(_clamp01(u) * (width - 1)))
            y = int(round((1.0 - _clamp01(v)) * (height - 1)))
            pts.append((x, y))

        if len(pts) < 3:
            continue

        _fill_polygon(frame_bgra, pts, color, alpha=0.18)

        for i in range(len(pts)):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % len(pts)]
            steps = max(abs(x1 - x0), abs(y1 - y0), 1)
            for s in range(steps + 1):
                a = s / steps
                x = int(round(x0 + (x1 - x0) * a))
                y = int(round(y0 + (y1 - y0) * a))
                x_min = max(0, x - t // 2)
                x_max = min(width, x + (t + 1) // 2)
                y_min = max(0, y - t // 2)
                y_max = min(height, y + (t + 1) // 2)
                frame_bgra[y_min:y_max, x_min:x_max] = color


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
