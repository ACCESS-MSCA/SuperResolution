#!/usr/bin/env python3
"""Create a compact 48 kHz stereo AAC sidecar for deterministic NDI preload."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

import av


DEFAULT_BITRATE = 320_000
DEFAULT_SAMPLE_RATE = 48_000


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract and normalize the first audio stream as AAC 48 kHz stereo. "
            "The sidecar lets the NDI sender preload audio without scanning a "
            "multi-gigabyte interleaved 8K master."
        )
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--bitrate-kbps", type=float, default=320.0)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def create_audio_sidecar(
    source: Path,
    output: Path,
    bitrate: int = DEFAULT_BITRATE,
    overwrite: bool = False,
) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output}")
    if bitrate <= 0:
        raise ValueError("bitrate must be positive")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.partial{output.suffix}")
    if temporary.exists():
        temporary.unlink()

    input_container = av.open(str(source))
    audio_in = input_container.streams.audio[0] if input_container.streams.audio else None
    if audio_in is None:
        input_container.close()
        raise RuntimeError(f"Source does not contain audio: {source}")

    output_container = av.open(str(temporary), mode="w", format="ipod")
    audio_out = output_container.add_stream("aac", rate=DEFAULT_SAMPLE_RATE)
    audio_out.layout = "stereo"
    audio_out.bit_rate = int(bitrate)
    resampler = av.AudioResampler(
        format="fltp",
        layout="stereo",
        rate=DEFAULT_SAMPLE_RATE,
    )

    started = time.monotonic()
    decoded_frames = 0
    try:
        for packet in input_container.demux(audio_in):
            for frame in packet.decode():
                decoded_frames += 1
                for resampled in resampler.resample(frame):
                    for encoded in audio_out.encode(resampled):
                        output_container.mux(encoded)

        for resampled in resampler.resample(None):
            for encoded in audio_out.encode(resampled):
                output_container.mux(encoded)
        for encoded in audio_out.encode(None):
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
        f"[AUDIO SIDECAR] completed frames={decoded_frames} "
        f"elapsed={time.monotonic() - started:.1f}s output={output}",
        flush=True,
    )


def main() -> int:
    args = _parse_args()
    if args.bitrate_kbps <= 0:
        raise ValueError("--bitrate-kbps must be positive")
    create_audio_sidecar(
        args.source.expanduser().resolve(),
        args.output.expanduser().resolve(),
        bitrate=int(round(args.bitrate_kbps * 1000.0)),
        overwrite=args.overwrite,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[AUDIO SIDECAR] cancelled", file=sys.stderr)
        raise SystemExit(130)
