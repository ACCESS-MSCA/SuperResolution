"""
Stream a video file as an NDI source using cyndilib.
Other apps on the network (OBS, NDI Monitor, etc.) can receive it.

Usage:
    python stream_video.py
    python stream_video.py <path_to_video>
    python stream_video.py <path_to_video> --dual
"""

import sys
import time

import numpy as np
from cyndilib.audio_frame import AudioSendFrame

from ffmpeg import decode_audio_to_array, probe_video, read_exact, start_video_decoder
from utils import draw_square, make_sender


def stream_video(video_path: str, source_name: str = "StreamNDI", dual: bool = False):
    width, height, fps, total_frames = probe_video(video_path)
    fps_float = float(fps)

    print(f"Source  : {video_path}")
    print(f"Size    : {width}x{height} @ {fps_float:.3f} fps ({total_frames} frames)")

    # NDI audio settings
    audio_sample_rate = 48000
    audio_channels = 2
    samples_per_frame_exact = audio_sample_rate / max(fps_float, 1.0)
    audio_samples_per_frame = max(1, int(round(samples_per_frame_exact)))
    max_audio_samples = audio_samples_per_frame

    # Create main sender
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

    # Decode source audio once and stream it deterministically with frame pacing.
    audio_data = decode_audio_to_array(video_path, audio_sample_rate, audio_channels)
    audio_enabled = audio_data is not None and audio_data.shape[1] > 0

    if audio_enabled:
        af_plain = AudioSendFrame(max_num_samples=max_audio_samples)
        af_plain.sample_rate = audio_sample_rate
        af_plain.num_channels = audio_channels
        sender_plain.set_audio_frame(af_plain)
    else:
        print("[warn] could not start audio decoder; sending video only.")

    # Optional second sender with overlay
    sender_overlay = None
    if dual:
        overlay_name = f"{source_name}-Square"
        sender_overlay, _ = make_sender(overlay_name, width, height, fps)
        print(f"NDI name: '{overlay_name}'")
        if audio_enabled:
            af_overlay = AudioSendFrame(max_num_samples=max_audio_samples)
            af_overlay.sample_rate = audio_sample_rate
            af_overlay.num_channels = audio_channels
            sender_overlay.set_audio_frame(af_overlay)

    print("Press Ctrl-C to stop.\n")

    frame_duration = 1.0 / fps_float
    frame_idx = 0
    dropped_timing_count = 0

    audio_pos = 0
    total_audio_samples = int(audio_data.shape[1]) if audio_enabled else 0

    frame_bytes = width * height * 4
    video_proc = start_video_decoder(video_path)
    if video_proc.stdout is None:
        print("Error: failed to start video decoder stdout pipe.")
        sys.exit(1)

    # Monotonic scheduled clock:
    # avoids accumulating drift better than "sleep(frame_duration - elapsed)"
    next_frame_time = time.monotonic()

    with sender_plain:
        if sender_overlay is not None:
            sender_overlay.__enter__()

        try:
            while True:
                raw = read_exact(video_proc.stdout, frame_bytes)
                if raw is None:
                    # Decoder ended unexpectedly; restart and continue.
                    if video_proc.poll() is None:
                        video_proc.terminate()
                    video_proc = start_video_decoder(video_path)
                    if video_proc.stdout is None:
                        print("Error: failed to restart video decoder.")
                        break

                    next_frame_time = time.monotonic()
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
                        # Copy only for overlay path so the plain frame stays untouched.
                        bgra_sq = np.array(bgra, copy=True)
                        draw_square(bgra_sq, frame_idx)
                        overlay_frame = bgra_sq.ravel()
                        sender_overlay.write_video_and_audio(overlay_frame, audio_frame)
                else:
                    # Async send reduces chance the Python loop blocks on I/O.
                    sender_plain.write_video_async(plain_frame)
                    if sender_overlay is not None:
                        bgra_sq = np.array(bgra, copy=True)
                        draw_square(bgra_sq, frame_idx)
                        overlay_frame = bgra_sq.ravel()
                        sender_overlay.write_video_async(overlay_frame)

                frame_idx += 1

                # Schedule next frame using an accumulated target time.
                next_frame_time += frame_duration
                now = time.monotonic()
                sleep_for = next_frame_time - now

                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    dropped_timing_count += 1
                    if dropped_timing_count % 120 == 0:
                        print(
                            f"[warn] timing late {dropped_timing_count} times "
                            f"(latest overrun: {-sleep_for*1000:.2f} ms)"
                        )
                    next_frame_time = now

        except KeyboardInterrupt:
            print("\nStopped by user.")

        finally:
            if video_proc.poll() is None:
                video_proc.terminate()
            if sender_overlay is not None:
                sender_overlay.__exit__(None, None, None)


if __name__ == "__main__":
    args = sys.argv[1:]
    dual = "--dual" in args
    args = [a for a in args if a != "--dual"]

    video = args[0] if args else "Videos/big_buck_bunny.mp4"
    stream_video(video, dual=dual)
