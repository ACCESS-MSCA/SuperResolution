import math
from fractions import Fraction

import cv2
import numpy as np
from cyndilib.sender import Sender
from cyndilib.video_frame import VideoSendFrame
from cyndilib.wrapper.ndi_structs import FourCC


def draw_square(frame: np.ndarray, frame_idx: int) -> np.ndarray:
    """Draw a bouncing white square overlay on a BGRA frame (in-place)."""
    h, w = frame.shape[:2]
    size = min(h, w) // 6

    # two sine waves with different periods so x/y never repeat at the same time,
    # producing a Lissajous-style path across the full frame area
    cx = int((w - size) / 2 * (1 + math.sin(frame_idx * 2 * math.pi / 300))) + size // 2
    cy = int((h - size) / 2 * (1 + math.sin(frame_idx * 2 * math.pi / 220))) + size // 2
    x1, x2 = cx - size // 2, cx + size // 2
    y1, y2 = cy - size // 2, cy + size // 2

    # bicubic processing: downsample the ROI to 1/4 size then upsample back,
    # creating a pixelated/blurred effect that makes the region visually distinct
    roi = frame[y1:y2, x1:x2]
    small = cv2.resize(roi, (size // 4, size // 4), interpolation=cv2.INTER_CUBIC)
    frame[y1:y2, x1:x2] = cv2.resize(small, (size, size), interpolation=cv2.INTER_CUBIC)

    # draw the border on top of the processed region
    cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 255, 255, 255), thickness=3)
    return frame


def make_sender(source_name: str, width: int, height: int, fps: Fraction) -> tuple:
    """Create and configure a VideoSendFrame + Sender pair for one NDI output."""
    vf = VideoSendFrame()
    vf.set_resolution(width, height)
    vf.set_frame_rate(fps)  # must be a Fraction, not a float
    vf.set_fourcc(FourCC.BGRA)
    s = Sender(source_name)
    s.set_video_frame(vf)
    return s, vf