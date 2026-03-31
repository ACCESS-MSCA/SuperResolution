"""
Stream a video file as an NDI source using cyndilib.
Other apps on the network (OBS, NDI Monitor, etc.) can receive it.

Usage:
    python stream_video.py                      # streams big_buck_bunny.mp4
    python stream_video.py <path_to_video>
    python stream_video.py <path_to_video> --dual
"""

import sys
import time
from fractions import Fraction
import cv2

from utils import draw_square, make_sender



def stream_video(video_path: str, source_name: str = "StreamNDI", dual: bool = False):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: cannot open video '{video_path}'")
        sys.exit(1)

    # use Fraction so cyndilib's set_frame_rate gets a numerator/denominator,
    # limit_denominator(1001) correctly handles rates like 23.976 and 29.97
    fps = Fraction(cap.get(cv2.CAP_PROP_FPS) or 30.0).limit_denominator(1001)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"Source  : {video_path}")
    print(f"Size    : {width}x{height} @ {float(fps):.2f} fps  ({total_frames} frames)")

    # plain stream — always created
    sender_plain, _ = make_sender(source_name, width, height, fps)
    print(f"NDI name: '{source_name}'")

    # overlay stream — only created when --dual is requested
    if dual:
        overlay_name = source_name + "-Square"
        sender_overlay, _ = make_sender(overlay_name, width, height, fps)
        print(f"NDI name: '{overlay_name}'")

    print("Press Ctrl-C to stop.\n")

    frame_duration = 1.0 / float(fps)
    frame_idx = 0

    # start the plain sender via context manager; start the overlay sender manually
    # so both share the same try/finally teardown block
    with sender_plain:
        if dual:
            sender_overlay.__enter__()
        try:
            while True:
                ret, bgr = cap.read()
                if not ret:
                    # end of file — seek back to start and loop
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue

                t0 = time.monotonic()

                # OpenCV delivers BGR; NDI requires BGRA
                bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
                flat = bgra.flatten()

                # send the plain frame
                sender_plain.write_video(flat)

                if dual:
                    # copy so the plain frame is never modified
                    bgra_sq = bgra.copy()
                    draw_square(bgra_sq, frame_idx)
                    sender_overlay.write_video(bgra_sq.flatten())

                frame_idx += 1

                # sleep the remainder of the frame interval to maintain target FPS
                elapsed = time.monotonic() - t0
                sleep_for = frame_duration - elapsed
                if sleep_for > 0:
                    time.sleep(sleep_for)
        finally:
            # ensure the overlay sender is shut down even on Ctrl-C or error
            if dual:
                sender_overlay.__exit__(None, None, None)


if __name__ == "__main__":
    args = sys.argv[1:]
    dual = "--dual" in args
    args = [a for a in args if a != "--dual"]

    video = args[0] if args else "big_buck_bunny.mp4"
    stream_video(video, dual=dual)