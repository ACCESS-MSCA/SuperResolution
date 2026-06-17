from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

import numpy as np

try:
    import av
    from av.audio.resampler import AudioResampler
except ImportError as exc:  # pragma: no cover - depends on local environment.
    av = None
    AudioResampler = None
    _PYAV_IMPORT_ERROR = exc
else:
    _PYAV_IMPORT_ERROR = None


DEFAULT_FPS = Fraction(30, 1)
_AV_TIME_BASE = 1_000_000.0
_AUDIO_LAYOUT_BY_CHANNELS = {1: "mono", 2: "stereo"}


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    fps: Fraction
    total_video_frames: int
    duration_seconds: float
    audio_enabled: bool
    audio_sample_rate: int
    audio_channels: int
    audio_max_samples_per_chunk: int


@dataclass(frozen=True)
class MediaEvent:
    media_time_seconds: float


@dataclass(frozen=True)
class MediaVideoEvent(MediaEvent):
    frame_bgra: np.ndarray


@dataclass(frozen=True)
class MediaAudioEvent(MediaEvent):
    samples: np.ndarray


def _require_pyav() -> None:
    if av is None:
        raise RuntimeError(
            "PyAV is not installed. Install requirements.txt before running the streamer."
        ) from _PYAV_IMPORT_ERROR


def _parse_fps(value) -> Fraction:
    if value in (None, 0, "0", "0/0"):
        return DEFAULT_FPS
    try:
        return Fraction(value).limit_denominator(1001)
    except Exception:
        return DEFAULT_FPS


def _stream_duration_seconds(stream) -> float:
    duration = getattr(stream, "duration", None)
    time_base = getattr(stream, "time_base", None)
    if duration is None or time_base is None:
        return 0.0
    try:
        return max(0.0, float(duration * time_base))
    except Exception:
        return 0.0


def _container_duration_seconds(container) -> float:
    duration = getattr(container, "duration", None)
    if duration is None:
        return 0.0
    try:
        return max(0.0, float(duration) / _AV_TIME_BASE)
    except Exception:
        return 0.0


def _frame_time_seconds(frame) -> Optional[float]:
    frame_time = getattr(frame, "time", None)
    if frame_time is not None:
        try:
            return max(0.0, float(frame_time))
        except Exception:
            pass

    pts = getattr(frame, "pts", None)
    time_base = getattr(frame, "time_base", None)
    if pts is None or time_base is None:
        return None

    try:
        return max(0.0, float(pts * time_base))
    except Exception:
        return None


def _audio_frame_to_planar_float32(frame, channels: int) -> np.ndarray:
    data = np.asarray(frame.to_ndarray(), dtype=np.float32)
    if data.ndim == 1:
        return np.ascontiguousarray(data.reshape(1, -1), dtype=np.float32)
    if data.ndim != 2:
        raise ValueError(f"Unexpected audio ndarray shape: {data.shape}")

    if data.shape[0] == channels:
        return np.ascontiguousarray(data, dtype=np.float32)
    if data.shape[1] == channels:
        return np.ascontiguousarray(data.T, dtype=np.float32)
    if channels == 1:
        return np.ascontiguousarray(data.reshape(1, -1), dtype=np.float32)

    raise ValueError(f"Could not normalize audio frame shape {data.shape} for {channels} channels")


class LoopingMediaReader:
    def __init__(self, video_path: str, audio_sample_rate: int = 48000, audio_channels: int = 2) -> None:
        _require_pyav()
        if audio_channels not in _AUDIO_LAYOUT_BY_CHANNELS:
            raise ValueError("Only mono and stereo outputs are currently supported")

        self._video_path = str(video_path)
        self._audio_sample_rate = int(audio_sample_rate)
        self._audio_channels = int(audio_channels)

        self._video_container = None
        self._audio_container = None
        self._video_stream = None
        self._audio_stream = None
        self._video_frames = None
        self._audio_frames = None
        self._audio_resampler = None
        self._audio_tail_flushed = False

        self._pending_audio_events: list[MediaAudioEvent] = []
        self._next_video_event: Optional[MediaVideoEvent] = None
        self._next_audio_event: Optional[MediaAudioEvent] = None

        self._pass_media_offset_seconds = 0.0
        self._pass_max_end_seconds = 0.0
        self._next_audio_time_seconds = 0.0
        self._next_video_time_seconds = 0.0
        self._pass_event_count = 0

        self.restart_count = 0
        self.early_restart_count = 0

        self.info = self._open_pass(first_pass=True)

    def close(self) -> None:
        video_container = self._video_container
        audio_container = self._audio_container

        self._video_container = None
        self._audio_container = None
        self._video_stream = None
        self._audio_stream = None
        self._video_frames = None
        self._audio_frames = None
        self._audio_resampler = None
        self._audio_tail_flushed = False
        self._pending_audio_events.clear()
        self._next_video_event = None
        self._next_audio_event = None

        if video_container is not None:
            video_container.close()
        if audio_container is not None and audio_container is not video_container:
            audio_container.close()

    def _open_pass(self, first_pass: bool) -> MediaInfo | None:
        self.close()

        probe_container = av.open(self._video_path)
        video_stream = next((stream for stream in probe_container.streams if stream.type == "video"), None)
        if video_stream is None:
            probe_container.close()
            raise RuntimeError(f"No video stream found in '{self._video_path}'.")

        audio_stream = next((stream for stream in probe_container.streams if stream.type == "audio"), None)

        try:
            video_stream.thread_type = "AUTO"
        except Exception:
            pass
        if audio_stream is not None:
            try:
                audio_stream.thread_type = "AUTO"
            except Exception:
                pass

        width = int(getattr(video_stream, "width", 0) or getattr(video_stream.codec_context, "width", 0) or 0)
        height = int(getattr(video_stream, "height", 0) or getattr(video_stream.codec_context, "height", 0) or 0)
        if width <= 0 or height <= 0:
            probe_container.close()
            raise RuntimeError(f"Invalid video size in '{self._video_path}'.")

        fps = _parse_fps(
            getattr(video_stream, "average_rate", None)
            or getattr(video_stream, "base_rate", None)
            or getattr(video_stream, "guessed_rate", None)
        )
        total_frames = int(getattr(video_stream, "frames", 0) or 0)

        duration_candidates = [
            _container_duration_seconds(probe_container),
            _stream_duration_seconds(video_stream),
        ]
        if audio_stream is not None:
            duration_candidates.append(_stream_duration_seconds(audio_stream))
        if total_frames > 0 and float(fps) > 0.0:
            duration_candidates.append(total_frames / float(fps))
        duration_seconds = max(duration_candidates) if duration_candidates else 0.0
        if duration_seconds <= 0.0:
            duration_seconds = max(1.0 / float(fps), 0.001)

        audio_enabled = audio_stream is not None
        audio_max_samples_per_chunk = int(math.ceil(self._audio_sample_rate / max(float(fps), 1.0) * 4.0))
        audio_max_samples_per_chunk = max(audio_max_samples_per_chunk, 2048)

        probe_container.close()

        self._video_container = av.open(self._video_path)
        self._video_stream = next(stream for stream in self._video_container.streams if stream.type == "video")
        try:
            self._video_stream.thread_type = "AUTO"
        except Exception:
            pass
        self._video_frames = iter(self._video_container.decode(video=0))

        if audio_enabled:
            self._audio_container = av.open(self._video_path)
            self._audio_stream = next(stream for stream in self._audio_container.streams if stream.type == "audio")
            try:
                self._audio_stream.thread_type = "AUTO"
            except Exception:
                pass
            self._audio_frames = iter(self._audio_container.decode(audio=0))
            self._audio_resampler = AudioResampler(
                format="fltp",
                layout=_AUDIO_LAYOUT_BY_CHANNELS[self._audio_channels],
                rate=self._audio_sample_rate,
            )
        else:
            self._audio_container = None
            self._audio_stream = None
            self._audio_frames = None
            self._audio_resampler = None

        self._audio_tail_flushed = False
        self._pending_audio_events.clear()
        self._next_video_event = None
        self._next_audio_event = None
        self._next_audio_time_seconds = 0.0
        self._next_video_time_seconds = 0.0
        self._pass_max_end_seconds = 0.0
        self._pass_event_count = 0
        self.restart_count += 1

        if not first_pass:
            return None

        return MediaInfo(
            width=width,
            height=height,
            fps=fps,
            total_video_frames=total_frames,
            duration_seconds=duration_seconds,
            audio_enabled=audio_enabled,
            audio_sample_rate=self._audio_sample_rate if audio_enabled else 0,
            audio_channels=self._audio_channels if audio_enabled else 0,
            audio_max_samples_per_chunk=audio_max_samples_per_chunk if audio_enabled else 0,
        )

    def _restart_after_eof(self) -> None:
        if self._pass_event_count <= 0:
            raise RuntimeError(f"No decodable media events found in '{self._video_path}'.")

        pass_duration = max(self.info.duration_seconds, self._pass_max_end_seconds)
        if pass_duration <= 0.0:
            pass_duration = max(1.0 / max(float(self.info.fps), 1.0), 0.001)

        if self._pass_max_end_seconds + 0.050 < self.info.duration_seconds:
            self.early_restart_count += 1

        self._pass_media_offset_seconds += pass_duration
        self._open_pass(first_pass=False)

    def _make_video_event(self, event_time: float, frame_bgra: np.ndarray) -> MediaVideoEvent:
        self._pass_event_count += 1
        return MediaVideoEvent(self._pass_media_offset_seconds + event_time, frame_bgra)

    def _make_audio_event(self, event_time: float, samples: np.ndarray) -> MediaAudioEvent:
        self._pass_event_count += 1
        return MediaAudioEvent(self._pass_media_offset_seconds + event_time, samples)

    def _read_next_video_event_in_pass(self) -> Optional[MediaVideoEvent]:
        if self._video_frames is None:
            return None

        try:
            frame = next(self._video_frames)
        except StopIteration:
            self._video_frames = None
            return None

        source_time = _frame_time_seconds(frame)
        if source_time is None:
            source_time = self._next_video_time_seconds
        event_time = max(source_time, self._next_video_time_seconds)

        frame_bgra = np.ascontiguousarray(frame.to_ndarray(format="bgra"), dtype=np.uint8)
        duration_seconds = 1.0 / max(float(self.info.fps), 1.0)
        self._next_video_time_seconds = event_time + duration_seconds
        self._pass_max_end_seconds = max(self._pass_max_end_seconds, self._next_video_time_seconds)
        return self._make_video_event(event_time, frame_bgra)

    def _append_audio_outputs(self, outputs, start_time: float) -> None:
        current_time = start_time
        for output_frame in outputs or []:
            samples = _audio_frame_to_planar_float32(output_frame, self._audio_channels)
            sample_count = int(samples.shape[1])
            if sample_count <= 0:
                continue

            duration_seconds = sample_count / float(self._audio_sample_rate)
            event_time = max(current_time, self._next_audio_time_seconds)
            self._next_audio_time_seconds = event_time + duration_seconds
            self._pass_max_end_seconds = max(self._pass_max_end_seconds, self._next_audio_time_seconds)
            self._pending_audio_events.append(self._make_audio_event(event_time, samples))
            current_time = self._next_audio_time_seconds

    def _flush_audio_tail(self) -> None:
        if self._audio_resampler is None or self._audio_tail_flushed:
            return
        self._audio_tail_flushed = True
        self._append_audio_outputs(self._audio_resampler.resample(None), self._next_audio_time_seconds)

    def _read_next_audio_event_in_pass(self) -> Optional[MediaAudioEvent]:
        if self._pending_audio_events:
            return self._pending_audio_events.pop(0)
        if self._audio_frames is None:
            return None

        while True:
            try:
                frame = next(self._audio_frames)
            except StopIteration:
                self._audio_frames = None
                self._flush_audio_tail()
                if self._pending_audio_events:
                    return self._pending_audio_events.pop(0)
                return None

            source_time = _frame_time_seconds(frame)
            if source_time is None:
                source_time = self._next_audio_time_seconds
            current_time = max(source_time, self._next_audio_time_seconds)
            self._append_audio_outputs(self._audio_resampler.resample(frame), current_time)
            if self._pending_audio_events:
                return self._pending_audio_events.pop(0)

    def read_next(self) -> MediaEvent:
        while True:
            if self._next_video_event is None:
                self._next_video_event = self._read_next_video_event_in_pass()
            if self._next_audio_event is None:
                self._next_audio_event = self._read_next_audio_event_in_pass()

            if self._next_video_event is None and self._next_audio_event is None:
                self._restart_after_eof()
                continue

            if self._next_audio_event is None:
                event = self._next_video_event
                self._next_video_event = None
                return event

            if self._next_video_event is None:
                event = self._next_audio_event
                self._next_audio_event = None
                return event

            if self._next_video_event.media_time_seconds <= self._next_audio_event.media_time_seconds:
                event = self._next_video_event
                self._next_video_event = None
                return event

            event = self._next_audio_event
            self._next_audio_event = None
            return event
