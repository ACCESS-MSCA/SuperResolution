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

import numpy as np
from cyndilib.audio_frame import AudioSendFrame

from core import MonotonicFrameClock
from extensions.backchannel import MetadataDispatcher, NdiSenderBackchannelReceiver
from ffmpeg import decode_audio_to_array, probe_video, read_exact, start_video_decoder
from integrations.unity import UnityTransformLogHandler
from utils import draw_square, make_sender


def _configure_audio_frame(sender, sample_rate: int, channels: int, max_samples: int) -> None:
    af = AudioSendFrame(max_num_samples=max_samples)
    af.sample_rate = sample_rate
    af.num_channels = channels
    sender.set_audio_frame(af)


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

    with sender_plain:
        if sender_overlay is not None:
            sender_overlay.__enter__()

        if rx_metadata:
            try:
                backchannel = NdiSenderBackchannelReceiver(
                    sender_plain,
                    timeout_ms=0,
                    idle_sleep_seconds=0.001,
                    max_queue_size=1024,
                )
                backchannel.start()
                dispatcher = MetadataDispatcher(
                    handlers=[UnityTransformLogHandler()],
                    verbose_raw_xml=rx_metadata_verbose,
                    log_unhandled=True,
                )
                print("[info] backchannel receiver started")
            except Exception as exc:
                backchannel = None
                dispatcher = None
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

                # frombuffer(raw, ...) over bytes is read-only; cyndilib expects writable memory
                bgra = np.frombuffer(raw, dtype=np.uint8).copy().reshape((height, width, 4))
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

                if backchannel is not None and dispatcher is not None:
                    messages = backchannel.drain(max_messages=32)
                    if messages:
                        dispatcher.dispatch_many(messages)

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
