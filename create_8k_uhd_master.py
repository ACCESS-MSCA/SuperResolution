#!/usr/bin/env python3
"""Create the hardware-decodable 8K UHD master used by the NDI quality gate."""

from __future__ import annotations

import argparse
from fractions import Fraction
from pathlib import Path
import sys
import time

import av


DEFAULT_WIDTH = 7680
DEFAULT_CONTENT_HEIGHT = 4050
DEFAULT_HEIGHT = 4320
# NV12/4:2:0 requires an even chroma-aligned vertical offset. This keeps the
# exact 4050-line scale, leaving 134 pixels above and 136 below.
DEFAULT_PAD_Y = ((DEFAULT_HEIGHT - DEFAULT_CONTENT_HEIGHT) // 2) & ~1
DEFAULT_BITRATE = 160_000_000


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert an 8192x4320 H.264 source into a 7680x4320 HEVC master. "
            "The whole image is preserved at 7680x4050 with chroma-aligned black bars."
        )
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--bitrate-mbps", type=float, default=160.0)
    parser.add_argument(
        "--fps",
        default="source",
        help="Output frame rate as an integer/fraction (for example 24000/1001) or 'source'",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def _parse_rate(value: str, source_rate: Fraction) -> Fraction:
    if value.strip().lower() == "source":
        return source_rate
    rate = Fraction(value)
    if rate <= 0:
        raise ValueError("--fps must be positive")
    return rate


def _validate_source(source: Path) -> tuple[Fraction, int, float]:
    if not source.is_file():
        raise FileNotFoundError(source)

    with av.open(str(source)) as container:
        if not container.streams.video:
            raise RuntimeError("Source does not contain a video stream")
        stream = container.streams.video[0]
        if stream.codec_context.width != 8192 or stream.codec_context.height != 4320:
            raise RuntimeError(
                "This production recipe expects an 8192x4320 source; got "
                f"{stream.codec_context.width}x{stream.codec_context.height}"
            )
        rate = stream.average_rate
        if rate is None:
            raise RuntimeError("Source frame rate is unavailable")
        duration = float(container.duration / av.time_base) if container.duration else 0.0
        return Fraction(rate), int(stream.frames or 0), duration


def _drain_video_filter(graph, video_out, output_container) -> int:
    frames_encoded = 0
    while True:
        try:
            filtered = graph.vpull()
        except (av.error.BlockingIOError, av.error.EOFError):
            break
        for encoded in video_out.encode(filtered):
            output_container.mux(encoded)
        frames_encoded += 1
    return frames_encoded


def create_master(
    source: Path,
    output: Path,
    bitrate: int,
    fps: str,
    overwrite: bool,
) -> None:
    source_rate, source_frames, duration_seconds = _validate_source(source)
    rate = _parse_rate(fps, source_rate)
    expected_frames = (
        source_frames
        if rate == source_rate
        else int(round(duration_seconds * float(rate)))
    )
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    temporary = output.with_name(f".{output.name}.partial")
    if temporary.exists():
        temporary.unlink()

    input_container = av.open(str(source))
    video_in = input_container.streams.video[0]
    audio_in = input_container.streams.audio[0] if input_container.streams.audio else None

    graph = av.filter.Graph()
    source_filter = graph.add_buffer(template=video_in)
    filters = [source_filter]
    if rate != source_rate:
        filters.append(
            graph.add(
                "fps",
                f"fps={rate.numerator}/{rate.denominator}:round=near",
            )
        )
    scale_filter = graph.add(
        "scale",
        f"w={DEFAULT_WIDTH}:h={DEFAULT_CONTENT_HEIGHT}:flags=lanczos",
    )
    pad_filter = graph.add(
        "pad",
        f"w={DEFAULT_WIDTH}:h={DEFAULT_HEIGHT}:x=0:y={DEFAULT_PAD_Y}:color=black",
    )
    format_filter = graph.add("format", "pix_fmts=nv12")
    sink_filter = graph.add("buffersink")
    filters.extend((
        scale_filter,
        pad_filter,
        format_filter,
        sink_filter,
    ))
    graph.link_nodes(*filters)
    graph.configure()

    output_container = av.open(str(temporary), mode="w", format="mp4")
    video_out = output_container.add_stream(
        "hevc_videotoolbox",
        rate=rate,
        options={
            "constant_bit_rate": "1",
            "realtime": "0",
        },
    )
    video_out.width = DEFAULT_WIDTH
    video_out.height = DEFAULT_HEIGHT
    video_out.pix_fmt = "nv12"
    video_out.bit_rate = bitrate
    video_out.codec_context.codec_tag = "hvc1"
    audio_out = (
        output_container.add_stream_from_template(audio_in)
        if audio_in is not None
        else None
    )

    started = time.monotonic()
    frames_encoded = 0
    last_progress_frame = 0
    streams = [video_in] + ([audio_in] if audio_in is not None else [])

    try:
        for packet in input_container.demux(streams):
            if audio_in is not None and packet.stream.index == audio_in.index:
                if packet.pts is None:
                    continue
                packet.stream = audio_out
                output_container.mux(packet)
                continue

            for frame in packet.decode():
                graph.vpush(frame)
                frames_encoded += _drain_video_filter(
                    graph,
                    video_out,
                    output_container,
                )
                if frames_encoded == 1 or frames_encoded - last_progress_frame >= 30:
                    total = expected_frames if expected_frames > 0 else "?"
                    print(
                        f"[8K MASTER] frames={frames_encoded}/{total} "
                        f"elapsed={time.monotonic() - started:.1f}s",
                        flush=True,
                    )
                    last_progress_frame = frames_encoded

        graph.vpush(None)
        frames_encoded += _drain_video_filter(graph, video_out, output_container)
        for encoded in video_out.encode(None):
            output_container.mux(encoded)
        output_container.close()
        input_container.close()
        temporary.replace(output)
    except BaseException:
        output_container.close()
        input_container.close()
        if temporary.exists():
            temporary.unlink()
        raise

    print(
        f"[8K MASTER] completed frames={frames_encoded} "
        f"elapsed={time.monotonic() - started:.1f}s output={output}",
        flush=True,
    )


def main() -> int:
    args = _parse_args()
    if args.bitrate_mbps <= 0:
        raise ValueError("--bitrate-mbps must be positive")
    create_master(
        args.source.expanduser().resolve(),
        args.output.expanduser().resolve(),
        int(round(args.bitrate_mbps * 1_000_000)),
        args.fps,
        args.overwrite,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[8K MASTER] cancelled", file=sys.stderr)
        raise SystemExit(130)
