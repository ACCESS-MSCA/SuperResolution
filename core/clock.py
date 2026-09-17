"""Clock and pacing helpers for deterministic frame scheduling."""

from __future__ import annotations

import time


class MonotonicFrameClock:
    """Paces a loop at a target FPS using an accumulated monotonic schedule."""

    def __init__(self, fps: float):
        fps = float(fps)
        if fps <= 0:
            raise ValueError("fps must be > 0")
        self._frame_duration = 1.0 / fps
        self._next_frame_time = time.monotonic()

    def reset(self) -> None:
        """Resync schedule to current monotonic time."""
        self._next_frame_time = time.monotonic()

    def wait_next(self) -> float:
        """
        Wait until the next frame deadline.

        Returns:
            overrun_seconds: 0.0 when on time (or slept), positive when late.
        """
        self._next_frame_time += self._frame_duration
        now = time.monotonic()
        sleep_for = self._next_frame_time - now

        if sleep_for > 0:
            time.sleep(sleep_for)
            return 0.0

        # Overrun: resync so drift does not accumulate indefinitely.
        self._next_frame_time = now
        return -sleep_for
