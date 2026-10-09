"""Generate the Patchlab head-gain calibration clip and its exact target path.

A small glowing orb moves over a blue background with soft, static nebula clouds through fixation, step
(jump) and smooth-pursuit segments. Jumps go to pseudo-random positions in
random directions with random holds, and the pursuit is a smooth 2D wander, so
the viewer cannot anticipate the next move; a fixed seed keeps the clip
identical for every participant. The orb has a bright core with a white
centre point, a soft halo that pulses gently, and briefly enlarges after each
jump, shrinking back over ARRIVAL_S to pull the eye to its centre. Positions are defined as fractions of
the frame, so the angular amplitude scales with the screen condition. The
sidecar JSON lists the target UV for every frame (bottom-left origin, matching
STUDY_CONTRACTS) and is the gaze-proxy reference for analysis.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import tempfile
from typing import Callable

import av
import numpy as np


def frame_count(path: Path) -> int:
    with av.open(str(path)) as container:
        return sum(1 for _ in container.decode(video=0))


def inspect_clip(path: Path) -> dict:
    """Clip ID, SHA-256 and geometry, as in SuperResolution's study_catalog."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    with av.open(str(path)) as container:
        video = container.streams.video[0]
        return dict(video_id="clip_" + digest.hexdigest()[:16], sha256=digest.hexdigest(),
                    width=video.width, height=video.height, fps_rational=str(video.average_rate))


SCHEMA_VERSION = 3
BACKGROUND_TOP = (46, 62, 112)     # Medium-dark blue gradient, sRGB ...
BACKGROUND_BOTTOM = (32, 44, 86)
NEBULA_RGB = ((92, 62, 140), (40, 120, 140))  # ... with soft purple and teal clouds.
NEBULA_STRENGTH = 0.45             # Peak blend towards each cloud colour.
NEBULA_SCALE = 0.12                # Cloud blur sigma, fraction of frame width (no edges).
NEBULA_SEED = 7
CORE_RGB = (170, 225, 255)         # Pale blue core ...
CENTRE_RGB = (255, 255, 255)       # ... with a white centre point (CENTRE_FRAC of the core).
GLOW_RGB = (70, 160, 255)          # Soft blue halo.
CENTRE_FRAC = 0.35
GLOW_SIGMA = 1.5                   # Halo Gaussian sigma, in core radii.
GLOW_EXTENT = 5.0                  # Halo drawn out to this many core radii.
GLOW_PEAK = 0.85
PULSE_HZ = 1.0                     # Halo brightness pulses between 1 − PULSE_DEPTH and 1.
PULSE_DEPTH = 0.3
ARRIVAL_SCALE = 1.8                # Orb size right after a jump, shrinking to 1 ...
ARRIVAL_S = 0.3                    # ... over this many seconds (ease-out).
TARGET_DIAMETER = 0.012   # Core diameter, fraction of frame width (~0.8° at 70°).
# The path is pseudo-random but fixed by PATH_SEED, so every participant sees the
# same clip while no direction, position or timing can be anticipated.
PATH_SEED = 2026
JUMP_AMPLITUDES = (0.12, 0.25, 0.40)  # Jump lengths, fraction of width (~8°, 18°, 28° at 70°) ...
JUMPS_PER_AMPLITUDE = 6               # ... each used this often, in shuffled order and random directions.
JUMP_HOLD_S = (0.8, 1.4)              # Each hold lasts a random time in this range.
X_LIMIT, Y_LIMIT = 0.42, 0.40         # Targets stay within ±X_LIMIT of width, ±Y_LIMIT of height.
PURSUIT_S = 24.0
PURSUIT_HZ = ((0.06, 0.13), (0.07, 0.16))  # Two sine components per axis (x, y), incommensurate;
                                          # low enough that the path reaches the edges at this speed.
PURSUIT_PEAK_SPEED = 0.26             # Frame widths/s (~21°/s at 70°).
PURSUIT_LIMIT = 0.85                  # Pursuit stays within this share of X_LIMIT / Y_LIMIT.
ASPECT = 16 / 9                       # Width / height, to keep jump lengths isotropic on screen.



@dataclass(frozen=True)
class Segment:
    name: str
    kind: str              # fixation | step | pursuit
    duration_s: float
    position: Callable[[float], tuple[float, float]]  # local time -> (x, y), centre-relative


def hold(x: float, y: float) -> Callable[[float], tuple[float, float]]:
    return lambda _t: (x, y)


def jumps(rng: np.random.Generator) -> list[tuple[float, float, float, float]]:
    """(x, y, amplitude, hold_s) per jump: each amplitude JUMPS_PER_AMPLITUDE
    times in shuffled order, each in a random direction that keeps the target on
    screen. The amplitude is measured on screen (x in widths, y converted)."""
    amplitudes = [float(a) for a in rng.permutation(np.repeat(JUMP_AMPLITUDES, JUMPS_PER_AMPLITUDE))]
    x = y = 0.0
    result = []
    for amplitude in amplitudes:
        for _ in range(1000):
            angle = rng.uniform(0, 2 * math.pi)
            nx = x + amplitude * math.cos(angle)
            ny = y + amplitude * math.sin(angle) * ASPECT
            if abs(nx) <= X_LIMIT and abs(ny) <= Y_LIMIT:
                break
        else:
            raise ValueError("No on-screen jump found")
        x, y = nx, ny
        result.append((round(x, 6), round(y, 6), amplitude, round(float(rng.uniform(*JUMP_HOLD_S)), 3)))
    return result


def pursuit_path(rng: np.random.Generator) -> Callable[[float], tuple[float, float]]:
    """Smooth 2D wander starting at the centre: two sines per axis with random
    weights, scaled so the peak on-screen speed is PURSUIT_PEAK_SPEED and the
    path stays within PURSUIT_LIMIT of the screen limits."""
    weights = rng.uniform(0.4, 1.0, size=(2, 2)) * rng.choice((-1, 1), size=(2, 2))

    def raw(t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return tuple(sum(w * np.sin(2 * np.pi * f * t) for w, f in zip(weights[axis], PURSUIT_HZ[axis]))
                     for axis in (0, 1))

    t = np.linspace(0, PURSUIT_S, 20001)
    rx, ry = raw(t)
    vx, vy = np.gradient(rx, t), np.gradient(ry, t)
    speed = np.hypot(vx, vy / ASPECT).max()  # y in heights → widths
    scale_x = scale_y = PURSUIT_PEAK_SPEED / speed
    scale_x = min(scale_x, PURSUIT_LIMIT * X_LIMIT / np.abs(rx).max())
    scale_y = min(scale_y, PURSUIT_LIMIT * Y_LIMIT / np.abs(ry).max())

    def position(local_t: float) -> tuple[float, float]:
        x, y = raw(np.array(local_t))
        return float(x) * scale_x, float(y) * scale_y

    return position


def schedule() -> list[Segment]:
    """Deterministic segment list. x is a fraction of width, y of height, y up."""
    rng = np.random.default_rng(PATH_SEED)
    segments = [Segment("fixation_start", "fixation", 2.0, hold(0.0, 0.0))]
    for index, (x, y, amplitude, hold_s) in enumerate(jumps(rng)):
        segments.append(Segment(f"jump_{index:02d}_{amplitude:.2f}", "step", hold_s, hold(x, y)))
    segments.append(Segment("fixation_mid", "fixation", 1.0, hold(0.0, 0.0)))
    segments.append(Segment("pursuit_2d", "pursuit", PURSUIT_S, pursuit_path(rng)))
    segments.append(Segment("fixation_end", "fixation", 1.0, hold(0.0, 0.0)))
    return segments


def arrival_scale(since_jump_s: float) -> float:
    """Orb size factor: ARRIVAL_SCALE at a jump, easing out to 1 over ARRIVAL_S."""
    remaining = 1.0 - min(since_jump_s / ARRIVAL_S, 1.0)
    return 1.0 + (ARRIVAL_SCALE - 1.0) * remaining ** 2


def pulse(t: float) -> float:
    """Halo brightness factor in [1 − PULSE_DEPTH, 1]."""
    return 1.0 - PULSE_DEPTH * (0.5 - 0.5 * math.cos(2 * math.pi * PULSE_HZ * t))


def target_path(fps: Fraction) -> list[dict]:
    """One record per frame; `onset` marks the first frame of each segment and
    `target_scale` the orb size factor (enlarged just after each jump)."""
    segments = schedule()
    total = sum(s.duration_s for s in segments)
    records, start, index, last_jump = [], 0.0, 0, 0.0
    for frame_id in range(math.ceil(total * fps - 1e-9)):
        t = frame_id / fps
        while t >= start + segments[index].duration_s - 1e-9 and index < len(segments) - 1:
            start += segments[index].duration_s
            index += 1
        segment = segments[index]
        x, y = segment.position(float(t) - start)
        uv = [round(0.5 + x, 6), round(0.5 + y, 6)]
        onset = not records or records[-1]["segment_index"] != index
        if onset and records and records[-1]["uv"] != uv:
            last_jump = float(t)
        records.append(dict(frame_id=frame_id, pts_s=round(float(t), 9), segment_index=index,
                            segment=segment.name, kind=segment.kind, onset=onset, uv=uv,
                            target_scale=round(arrival_scale(float(t) - last_jump), 4)))
    return records


def clouds(width: int, height: int, rng: np.random.Generator) -> np.ndarray:
    """Smooth noise in [0, 1]: Gaussian low-pass of white noise at 1/8 scale,
    bilinearly upsampled. Only large, soft shapes remain — nothing to fixate."""
    w, h = max(width // 8, 8), max(height // 8, 8)
    noise = rng.standard_normal((h, w))
    fy, fx = np.fft.fftfreq(h)[:, None], np.fft.fftfreq(w)[None, :]
    sigma = NEBULA_SCALE * w
    field = np.fft.ifft2(np.fft.fft2(noise) * np.exp(-2 * (np.pi * sigma) ** 2 * (fx ** 2 + fy ** 2))).real
    field = (field - field.mean()) / field.std()
    ys, xs = np.linspace(0, h - 1, height), np.linspace(0, w - 1, width)
    y0, x0 = np.floor(ys).astype(int), np.floor(xs).astype(int)
    y1, x1 = np.minimum(y0 + 1, h - 1), np.minimum(x0 + 1, w - 1)
    wy, wx = (ys - y0)[:, None], (xs - x0)[None, :]
    top = field[y0][:, x0] * (1 - wx) + field[y0][:, x1] * wx
    bottom = field[y1][:, x0] * (1 - wx) + field[y1][:, x1] * wx
    full = top * (1 - wy) + bottom * wy
    return np.clip((full - 0.3) / 1.7, 0.0, 1.0) ** 1.5


@lru_cache(maxsize=2)
def background(width: int, height: int) -> np.ndarray:
    """Blue gradient with soft purple/teal nebula clouds and fixed ±1 dither so
    the encoder does not band. Static and deterministic."""
    rng = np.random.default_rng(NEBULA_SEED)
    weight = np.linspace(0.0, 1.0, height)[:, None, None]
    top, bottom = np.array(BACKGROUND_TOP, float), np.array(BACKGROUND_BOTTOM, float)
    image = np.broadcast_to(top + (bottom - top) * weight, (height, width, 3)).copy()
    for colour in NEBULA_RGB:
        alpha = NEBULA_STRENGTH * clouds(width, height, rng)[..., None]
        image = image * (1 - alpha) + np.array(colour, float) * alpha
    dither = rng.integers(-1, 2, size=(height, width, 1))
    frame = np.clip(np.round(image + dither), 0, 255).astype(np.uint8)
    frame.setflags(write=False)
    return frame


def render(width: int, height: int, uv: list[float], diameter: float,
           scale: float = 1.0, glow: float = 1.0) -> np.ndarray:
    """Glowing orb (halo, core, white centre point), anti-aliased, on the gradient."""
    frame = background(width, height).copy()
    cx, cy = uv[0] * (width - 1), (1.0 - uv[1]) * (height - 1)
    radius = diameter * width / 2 * scale
    reach = radius * GLOW_EXTENT
    x0, x1 = max(int(cx - reach - 2), 0), min(int(cx + reach + 3), width)
    y0, y1 = max(int(cy - reach - 2), 0), min(int(cy + reach + 3), height)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    distance = np.hypot(xx - cx, yy - cy)[..., None]
    patch = frame[y0:y1, x0:x1].astype(float)
    layers = (
        (GLOW_RGB, glow * GLOW_PEAK * np.exp(-0.5 * (distance / (GLOW_SIGMA * radius)) ** 2)),
        (CORE_RGB, np.clip(radius + 0.5 - distance, 0.0, 1.0)),
        (CENTRE_RGB, np.clip(radius * CENTRE_FRAC + 0.5 - distance, 0.0, 1.0)),
    )
    for colour, alpha in layers:
        patch = patch * (1.0 - alpha) + np.array(colour, float) * alpha
    frame[y0:y1, x0:x1] = np.round(patch).astype(np.uint8)
    return frame


def generate(output: Path, width: int, height: int, fps: Fraction,
             diameter: float = TARGET_DIAMETER, crf: int = 18) -> dict:
    path = target_path(fps)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".calibration-", suffix=".mp4",
                                     dir=output.parent, delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        with av.open(str(staging), "w", options={"movflags": "+faststart"}) as container:
            stream = container.add_stream("libx264", rate=fps)
            stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
            stream.options = {"preset": "veryfast", "crf": str(crf), "bf": "0"}
            for record in path:
                image = render(width, height, record["uv"], diameter,
                               record["target_scale"], pulse(record["pts_s"]))
                frame = av.VideoFrame.from_ndarray(image, format="rgb24")
                frame = frame.reformat(format="yuv420p")
                frame.pts, frame.time_base = record["frame_id"], Fraction(fps.denominator, fps.numerator)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        if frame_count(staging) != len(path):
            raise ValueError("Encoded frame count does not match the target path")
        staging.replace(output)
        output.chmod(0o644)
    finally:
        staging.unlink(missing_ok=True)
    clip = inspect_clip(output)
    if (clip["width"], clip["height"], clip["fps_rational"]) != (width, height, str(fps)):
        raise ValueError("Calibration clip geometry/timebase mismatch")
    sidecar = dict(
        schema_version=SCHEMA_VERSION, kind="patchlab_calibration_target_path",
        video_id=clip["video_id"], sha256=clip["sha256"], filename=output.name,
        width=width, height=height, fps_rational=str(fps), frame_count=len(path),
        uv_origin="bottom_left", pixel_mapping="x=u*(width-1), y=(1-v)*(height-1)",
        target=dict(diameter_frac_width=diameter, style="glowing orb: pale blue core, white centre "
                    f"point ({CENTRE_FRAC} of core), Gaussian halo; size × target_scale per frame",
                    core_rgb=list(CORE_RGB), centre_rgb=list(CENTRE_RGB), glow_rgb=list(GLOW_RGB),
                    glow_sigma_core_radii=GLOW_SIGMA, glow_peak=GLOW_PEAK,
                    pulse_hz=PULSE_HZ, pulse_depth=PULSE_DEPTH,
                    arrival_scale=ARRIVAL_SCALE, arrival_s=ARRIVAL_S,
                    background_rgb_top=list(BACKGROUND_TOP), background_rgb_bottom=list(BACKGROUND_BOTTOM),
                    background_nebula_rgb=[list(c) for c in NEBULA_RGB],
                    background_nebula_strength=NEBULA_STRENGTH, background_nebula_seed=NEBULA_SEED),
        parameters=dict(path_seed=PATH_SEED, jump_amplitudes_frac_width=list(JUMP_AMPLITUDES),
                        jumps_per_amplitude=JUMPS_PER_AMPLITUDE, jump_hold_s_range=list(JUMP_HOLD_S),
                        limits_frac=[X_LIMIT, Y_LIMIT], pursuit_s=PURSUIT_S,
                        pursuit_hz=[list(f) for f in PURSUIT_HZ],
                        pursuit_peak_speed_widths_per_s=PURSUIT_PEAK_SPEED),
        segments=[dict(index=i, name=s.name, kind=s.kind, duration_s=s.duration_s)
                  for i, s in enumerate(schedule())],
        frames=path,
    )
    sidecar_path = output.with_suffix(".path.json")
    sidecar_path.write_text(json.dumps(sidecar, allow_nan=False) + "\n")
    return sidecar


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent
                        / "patchlab_calibration_4k30.mp4")
    parser.add_argument("--width", type=int, default=3840)
    parser.add_argument("--height", type=int, default=2160)
    parser.add_argument("--fps", type=Fraction, default=Fraction(30))
    parser.add_argument("--crf", type=int, default=18,
                        help="H.264 constant-rate-factor quality (lower is higher quality)")
    args = parser.parse_args()
    if not 0 <= args.crf <= 51:
        parser.error("--crf must be between 0 and 51")
    sidecar = generate(args.output, args.width, args.height, args.fps, crf=args.crf)
    print(f"{args.output.name}: {sidecar['frame_count']} frames, "
          f"{float(sidecar['frame_count'] / args.fps):.2f} s, {sidecar['video_id']}")


if __name__ == "__main__":
    main()
