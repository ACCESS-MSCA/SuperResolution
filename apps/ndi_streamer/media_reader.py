from __future__ import annotations
import math
import threading
import time
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

import numpy as np

try:
    import av
    from av.audio.resampler import AudioResampler
    from av.codec.hwaccel import HWAccel
except ImportError as exc:  # pragma: no cover - depends on local environment.
    av = None
    AudioResampler = None
    HWAccel = None
    _PYAV_IMPORT_ERROR = exc
else:
    _PYAV_IMPORT_ERROR = None


DEFAULT_FPS = Fraction(30, 1)
_AV_TIME_BASE = 1_000_000.0
_AUDIO_LAYOUT_BY_CHANNELS = {1: "mono", 2: "stereo"}
_DECODE_ERROR_LOG_FIRST = 5
_DECODE_ERROR_LOG_INTERVAL = 30
_MAX_CONSECUTIVE_DECODE_ERRORS = 120
_VIDEO_DECODE_RECOVERY_SKIP_SECONDS = 0.5


def _select_video_pixel_format(requested: str, source: str) -> str:
    requested = str(requested).lower()
    if requested != "auto":
        return requested
    return "i420" if str(source).lower() in ("yuv420p", "yuvj420p") else "uyvy422"


def _normalize_video_hwaccel(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return None if normalized in ("", "none", "off", "software") else normalized


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
    video_pixel_format: str
    source_video_codec: str
    source_video_pixel_format: str


@dataclass(frozen=True)
class MediaEvent:
    media_time_seconds: float


@dataclass(frozen=True)
class MediaVideoEvent(MediaEvent):
    frame_data: np.ndarray
    decode_ms: float = 0.0
    pack_ms: float = 0.0
    source_pixel_format: str = "unknown"
    decode_backend: str = "pyav-software"
    buffer_lease: object | None = None

    def release(self) -> None:
        lease = self.buffer_lease
        if lease is not None:
            lease.release()


@dataclass(frozen=True)
class MediaAudioEvent(MediaEvent):
    samples: np.ndarray


class _ReusableVideoBufferLease:
    def __init__(self, array: np.ndarray, release_callback) -> None:
        self.array = array
        self._release_callback = release_callback
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._release_callback(self.array)


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


def _is_pyav_decode_error(exc: Exception) -> bool:
    if av is None:
        return False
    ffmpeg_error = getattr(av, "FFmpegError", None)
    if ffmpeg_error is not None and isinstance(exc, ffmpeg_error):
        return True
    error_module = getattr(av, "error", None)
    invalid_data_error = getattr(error_module, "InvalidDataError", None) if error_module else None
    return invalid_data_error is not None and isinstance(exc, invalid_data_error)


class LoopingMediaReader:
    def __init__(
        self,
        video_path: str,
        audio_sample_rate: int = 48000,
        audio_channels: int = 2,
        decode_video: bool = True,
        decode_audio: bool = True,
        video_pixel_format: str = "bgra",
        video_hwaccel: str | None = None,
        video_hwaccel_fallback: bool = True,
    ) -> None:
        _require_pyav()
        if audio_channels not in _AUDIO_LAYOUT_BY_CHANNELS:
            raise ValueError("Only mono and stereo outputs are currently supported")

        self._video_path = str(video_path)
        self._audio_sample_rate = int(audio_sample_rate)
        self._audio_channels = int(audio_channels)
        self._decode_video = bool(decode_video)
        self._decode_audio = bool(decode_audio)
        self._video_pixel_format = str(video_pixel_format).lower()
        if self._video_pixel_format not in ("auto", "bgra", "uyvy422", "i420", "nv12"):
            raise ValueError(
                f"Unsupported decoded video pixel format: '{video_pixel_format}'."
            )
        self.video_hwaccel_requested = _normalize_video_hwaccel(video_hwaccel)
        self._video_hwaccel_fallback = bool(video_hwaccel_fallback)
        self._video_hwaccel_disabled = False
        self.hardware_decode_active = False
        self.video_decode_backend = "pyav-software"
        self.video_hwaccel_fallback_count = 0

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
        self._video_buffer_pool: list[np.ndarray] = []
        self._video_buffer_pool_lock = threading.Lock()
        self._video_buffer_pool_limit = 8
        self.video_buffer_allocations = 0
        self.video_buffer_reuses = 0

        self._pass_media_offset_seconds = 0.0
        self._pass_max_end_seconds = 0.0
        self._next_audio_time_seconds = 0.0
        self._next_video_time_seconds = 0.0
        self._pass_event_count = 0

        self.restart_count = 0
        self.seek_restart_count = 0
        self.reopen_restart_count = 0
        self.restart_ms_max = 0.0
        self.last_restart_ms = 0.0
        self.early_restart_count = 0
        self.video_decode_errors = 0
        self.audio_decode_errors = 0
        self.video_decode_recoveries = 0
        self._consecutive_video_decode_errors = 0
        self._consecutive_audio_decode_errors = 0

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
        if self._next_video_event is not None:
            self._next_video_event.release()
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
        source_video_codec = str(
            getattr(video_stream.codec_context, "name", None) or "unknown"
        )
        source_format = getattr(video_stream.codec_context, "format", None)
        source_video_pixel_format = str(
            getattr(source_format, "name", None) or source_format or "unknown"
        )
        if self._video_pixel_format == "auto":
            self._video_pixel_format = _select_video_pixel_format(
                self._video_pixel_format,
                source_video_pixel_format,
            )

        # The video cadence is the loop clock. Container/audio durations often
        # include AAC encoder padding; using the longest stream here inserts a
        # real hole at every loop boundary (44 ms in the ACCESS 4K test clip).
        duration_candidates = [_stream_duration_seconds(video_stream)]
        if total_frames > 0 and float(fps) > 0.0:
            duration_candidates.append(total_frames / float(fps))
        duration_seconds = max((value for value in duration_candidates if value > 0.0), default=0.0)
        if duration_seconds <= 0.0:
            duration_seconds = _container_duration_seconds(probe_container)
        if duration_seconds <= 0.0:
            duration_seconds = max(1.0 / float(fps), 0.001)

        audio_enabled = audio_stream is not None
        audio_max_samples_per_chunk = int(math.ceil(self._audio_sample_rate / max(float(fps), 1.0) * 4.0))
        audio_max_samples_per_chunk = max(audio_max_samples_per_chunk, 2048)

        probe_container.close()

        if self._decode_video:
            self._open_video_decoder_at(0.0, seek=False)
        else:
            self._video_container = None
            self._video_stream = None
            self._video_frames = None

        if audio_enabled and self._decode_audio:
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
        self._consecutive_video_decode_errors = 0
        self._consecutive_audio_decode_errors = 0
        self.restart_count += 1
        if not first_pass:
            self.reopen_restart_count += 1

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
            video_pixel_format=self._video_pixel_format,
            source_video_codec=source_video_codec,
            source_video_pixel_format=source_video_pixel_format,
        )

    def _seek_pass_to_start(self) -> None:
        if self._decode_video:
            if self._video_container is None or self._video_stream is None:
                raise RuntimeError("Video decoder is unavailable for loop seek")
            self._video_container.seek(0, backward=True, any_frame=False)
            self._video_stream.codec_context.flush_buffers()
            self._video_frames = iter(self._video_container.decode(video=0))

        if self._decode_audio and self._audio_stream is not None:
            if self._audio_container is None:
                raise RuntimeError("Audio decoder is unavailable for loop seek")
            self._audio_container.seek(0, backward=True, any_frame=False)
            self._audio_stream.codec_context.flush_buffers()
            self._audio_frames = iter(self._audio_container.decode(audio=0))
            self._audio_resampler = AudioResampler(
                format="fltp",
                layout=_AUDIO_LAYOUT_BY_CHANNELS[self._audio_channels],
                rate=self._audio_sample_rate,
            )

        self._audio_tail_flushed = False
        self._pending_audio_events.clear()
        self._next_video_event = None
        self._next_audio_event = None
        self._next_audio_time_seconds = 0.0
        self._next_video_time_seconds = 0.0
        self._pass_max_end_seconds = 0.0
        self._pass_event_count = 0
        self._consecutive_video_decode_errors = 0
        self._consecutive_audio_decode_errors = 0
        self.restart_count += 1
        self.seek_restart_count += 1

    def _restart_after_eof(self) -> None:
        if self._pass_event_count <= 0:
            raise RuntimeError(f"No decodable media events found in '{self._video_path}'.")

        pass_duration = max(self.info.duration_seconds, self._pass_max_end_seconds)
        if pass_duration <= 0.0:
            pass_duration = max(1.0 / max(float(self.info.fps), 1.0), 0.001)

        if self._pass_max_end_seconds + 0.050 < self.info.duration_seconds:
            self.early_restart_count += 1

        self._pass_media_offset_seconds += pass_duration
        restart_started = time.monotonic()
        try:
            self._seek_pass_to_start()
        except Exception as exc:
            print(f"[warn] loop seek failed; reopening decoder: {exc}")
            self._open_pass(first_pass=False)
        finally:
            restart_ms = (time.monotonic() - restart_started) * 1000.0
            self.last_restart_ms = restart_ms
            self.restart_ms_max = max(self.restart_ms_max, restart_ms)

    def _make_video_event(
        self,
        event_time: float,
        frame_data: np.ndarray,
        decode_ms: float,
        pack_ms: float,
        source_pixel_format: str,
        decode_backend: str,
        buffer_lease=None,
    ) -> MediaVideoEvent:
        self._pass_event_count += 1
        return MediaVideoEvent(
            self._pass_media_offset_seconds + event_time,
            frame_data,
            decode_ms=decode_ms,
            pack_ms=pack_ms,
            source_pixel_format=source_pixel_format,
            decode_backend=decode_backend,
            buffer_lease=buffer_lease,
        )

    def _acquire_video_buffer(self, shape: tuple[int, int]) -> _ReusableVideoBufferLease:
        with self._video_buffer_pool_lock:
            for index in range(len(self._video_buffer_pool) - 1, -1, -1):
                candidate = self._video_buffer_pool[index]
                if candidate.shape == shape and candidate.dtype == np.uint8:
                    self._video_buffer_pool.pop(index)
                    self.video_buffer_reuses += 1
                    return _ReusableVideoBufferLease(candidate, self._release_video_buffer)

        self.video_buffer_allocations += 1
        return _ReusableVideoBufferLease(
            np.empty(shape, dtype=np.uint8),
            self._release_video_buffer,
        )

    def _release_video_buffer(self, frame_data: np.ndarray) -> None:
        if not frame_data.flags.c_contiguous or frame_data.dtype != np.uint8:
            return
        with self._video_buffer_pool_lock:
            if len(self._video_buffer_pool) < self._video_buffer_pool_limit:
                self._video_buffer_pool.append(frame_data)

    def _make_audio_event(self, event_time: float, samples: np.ndarray) -> MediaAudioEvent:
        self._pass_event_count += 1
        return MediaAudioEvent(self._pass_media_offset_seconds + event_time, samples)

    def _open_video_decoder_at(self, target_seconds: float, seek: bool = True) -> None:
        video_container = self._video_container
        self._video_container = None
        self._video_stream = None
        self._video_frames = None
        if video_container is not None:
            video_container.close()

        open_kwargs = {}
        use_hwaccel = bool(
            self.video_hwaccel_requested and not self._video_hwaccel_disabled
        )
        if use_hwaccel:
            if HWAccel is None:
                raise RuntimeError("This PyAV build does not expose hardware decoding.")
            open_kwargs["hwaccel"] = HWAccel(
                self.video_hwaccel_requested,
                allow_software_fallback=False,
            )
        try:
            self._video_container = av.open(self._video_path, **open_kwargs)
        except Exception:
            if not use_hwaccel or not self._video_hwaccel_fallback:
                raise
            self._video_hwaccel_disabled = True
            self.video_hwaccel_fallback_count += 1
            self._video_container = av.open(self._video_path)
        self._video_stream = next(stream for stream in self._video_container.streams if stream.type == "video")
        try:
            self._video_stream.thread_type = "AUTO"
        except Exception:
            pass

        self.hardware_decode_active = bool(
            use_hwaccel
            and not self._video_hwaccel_disabled
            and getattr(self._video_stream.codec_context, "is_hwaccel", False)
        )
        if self.hardware_decode_active:
            self.video_decode_backend = f"pyav-{self.video_hwaccel_requested}"
        elif self.video_hwaccel_requested and self._video_hwaccel_disabled:
            self.video_decode_backend = "pyav-software-fallback"
        else:
            self.video_decode_backend = "pyav-software"

        if seek:
            offset = int(max(0.0, target_seconds) * _AV_TIME_BASE)
            self._video_container.seek(offset, any_frame=False, backward=False)
        self._video_frames = iter(self._video_container.decode(video=0))

    def _fallback_video_decoder_after_hw_error(self, exc: Exception) -> bool:
        if not self.hardware_decode_active or not self._video_hwaccel_fallback:
            return False
        backend = self.video_decode_backend
        self._video_hwaccel_disabled = True
        self.hardware_decode_active = False
        self.video_decode_backend = "pyav-software-fallback"
        self.video_hwaccel_fallback_count += 1
        print(
            f"[warn] {backend} decode failed; using explicit software fallback: {exc}"
        )
        self._open_video_decoder_at(self._next_video_time_seconds)
        return True

    def _recover_video_decoder_after_error(self) -> None:
        target_seconds = self._next_video_time_seconds + _VIDEO_DECODE_RECOVERY_SKIP_SECONDS
        self.video_decode_recoveries += 1
        print(
            "[warn] reopening video decoder after decode error "
            f"(recovery={self.video_decode_recoveries}, target={target_seconds:.3f}s)"
        )
        self._open_video_decoder_at(target_seconds)
        self._next_video_time_seconds = max(self._next_video_time_seconds, target_seconds)

    def _log_decode_error(self, stream_name: str, total: int, consecutive: int, exc: Exception) -> None:
        if total <= _DECODE_ERROR_LOG_FIRST or total % _DECODE_ERROR_LOG_INTERVAL == 0:
            print(
                f"[warn] {stream_name} decode error skipped "
                f"(total={total}, consecutive={consecutive}): {exc}"
            )

    def _read_next_video_event_in_pass(self) -> Optional[MediaVideoEvent]:
        if self._video_frames is None:
            return None

        decode_started = time.monotonic()
        while True:
            try:
                frame = next(self._video_frames)
                break
            except StopIteration:
                self._video_frames = None
                return None
            except Exception as exc:
                if not _is_pyav_decode_error(exc):
                    raise
                if self._fallback_video_decoder_after_hw_error(exc):
                    decode_started = time.monotonic()
                    continue
                self.video_decode_errors += 1
                self._consecutive_video_decode_errors += 1
                self._log_decode_error(
                    "video",
                    self.video_decode_errors,
                    self._consecutive_video_decode_errors,
                    exc,
                )
                if self._consecutive_video_decode_errors >= _MAX_CONSECUTIVE_DECODE_ERRORS:
                    raise RuntimeError(
                        "Too many consecutive video decode errors. "
                        f"The source may be corrupt or unsupported by this PyAV/FFmpeg build: '{self._video_path}'."
                    ) from exc
                self._recover_video_decoder_after_error()
                continue
        decode_ms = (time.monotonic() - decode_started) * 1000.0

        source_time = _frame_time_seconds(frame)
        if source_time is None:
            source_time = self._next_video_time_seconds
        event_time = max(source_time, self._next_video_time_seconds)

        source_pixel_format = str(
            getattr(getattr(frame, "format", None), "name", None) or "unknown"
        )
        pack_started = time.monotonic()
        buffer_lease = None
        try:
            if self._video_pixel_format == "bgra":
                frame_data = np.ascontiguousarray(
                    frame.to_ndarray(format="bgra"),
                    dtype=np.uint8,
                )
            elif self._video_pixel_format == "uyvy422":
                packed_frame = frame.reformat(format="uyvy422")
                plane = packed_frame.planes[0]
                rows = np.frombuffer(plane, dtype=np.uint8).reshape(
                    packed_frame.height,
                    plane.line_size,
                )
                frame_data = np.ascontiguousarray(
                    rows[:, : packed_frame.width * 2],
                    dtype=np.uint8,
                )
            elif self._video_pixel_format == "nv12":
                packed_frame = (
                    frame
                    if source_pixel_format == "nv12"
                    else frame.reformat(format="nv12")
                )
                width = packed_frame.width
                height = packed_frame.height
                y_plane = packed_frame.planes[0]
                uv_plane = packed_frame.planes[1]
                y_rows = np.frombuffer(y_plane, dtype=np.uint8).reshape(
                    height,
                    y_plane.line_size,
                )
                uv_rows = np.frombuffer(uv_plane, dtype=np.uint8).reshape(
                    height // 2,
                    uv_plane.line_size,
                )
                buffer_lease = self._acquire_video_buffer(
                    (height + height // 2, width)
                )
                frame_data = buffer_lease.array
                frame_data[:height, :] = y_rows[:, :width]
                frame_data[height:, :] = uv_rows[:, :width]
            else:
                frame_data = np.ascontiguousarray(
                    frame.to_ndarray(format="yuv420p"),
                    dtype=np.uint8,
                )
        except Exception as exc:
            if buffer_lease is not None:
                buffer_lease.release()
            if not _is_pyav_decode_error(exc):
                raise
            self.video_decode_errors += 1
            self._consecutive_video_decode_errors += 1
            self._log_decode_error(
                "video",
                self.video_decode_errors,
                self._consecutive_video_decode_errors,
                exc,
            )
            if self._consecutive_video_decode_errors >= _MAX_CONSECUTIVE_DECODE_ERRORS:
                raise RuntimeError(
                    "Too many consecutive video conversion errors. "
                    f"The source may be corrupt or unsupported by this PyAV/FFmpeg build: '{self._video_path}'."
                ) from exc
            self._recover_video_decoder_after_error()
            return self._read_next_video_event_in_pass()
        pack_ms = (time.monotonic() - pack_started) * 1000.0

        duration_seconds = 1.0 / max(float(self.info.fps), 1.0)
        self._next_video_time_seconds = event_time + duration_seconds
        self._pass_max_end_seconds = max(self._pass_max_end_seconds, self._next_video_time_seconds)
        self._consecutive_video_decode_errors = 0
        return self._make_video_event(
            event_time,
            frame_data,
            decode_ms,
            pack_ms,
            source_pixel_format,
            self.video_decode_backend,
            buffer_lease,
        )

    def _append_audio_outputs(self, outputs, start_time: float) -> None:
        current_time = start_time
        for output_frame in outputs or []:
            samples = _audio_frame_to_planar_float32(output_frame, self._audio_channels)
            sample_count = int(samples.shape[1])
            if sample_count <= 0:
                continue

            event_time = max(current_time, self._next_audio_time_seconds)
            # Trim codec padding at the video-defined loop boundary. Without
            # this, the next pass starts after the padded container duration
            # and the NDI receiver eventually underruns once per loop.
            loop_duration = self.info.duration_seconds
            remaining_samples = int(round(
                max(0.0, loop_duration - event_time) * self._audio_sample_rate
            ))
            if remaining_samples <= 0:
                continue
            if sample_count > remaining_samples:
                samples = np.ascontiguousarray(samples[:, :remaining_samples], dtype=np.float32)
                sample_count = remaining_samples

            duration_seconds = sample_count / float(self._audio_sample_rate)
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
            except Exception as exc:
                if not _is_pyav_decode_error(exc):
                    raise
                self.audio_decode_errors += 1
                self._consecutive_audio_decode_errors += 1
                self._log_decode_error(
                    "audio",
                    self.audio_decode_errors,
                    self._consecutive_audio_decode_errors,
                    exc,
                )
                if self._consecutive_audio_decode_errors >= _MAX_CONSECUTIVE_DECODE_ERRORS:
                    raise RuntimeError(
                        "Too many consecutive audio decode errors. "
                        f"The source may be corrupt or unsupported by this PyAV/FFmpeg build: '{self._video_path}'."
                    ) from exc
                continue

            source_time = _frame_time_seconds(frame)
            if source_time is None:
                source_time = self._next_audio_time_seconds
            current_time = max(source_time, self._next_audio_time_seconds)
            try:
                self._append_audio_outputs(self._audio_resampler.resample(frame), current_time)
            except Exception as exc:
                if not _is_pyav_decode_error(exc):
                    raise
                self.audio_decode_errors += 1
                self._consecutive_audio_decode_errors += 1
                self._log_decode_error(
                    "audio",
                    self.audio_decode_errors,
                    self._consecutive_audio_decode_errors,
                    exc,
                )
                if self._consecutive_audio_decode_errors >= _MAX_CONSECUTIVE_DECODE_ERRORS:
                    raise RuntimeError(
                        "Too many consecutive audio resample errors. "
                        f"The source may be corrupt or unsupported by this PyAV/FFmpeg build: '{self._video_path}'."
                    ) from exc
                continue
            if self._pending_audio_events:
                self._consecutive_audio_decode_errors = 0
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

    def read_audio_until(self, media_time_seconds: float) -> list[MediaAudioEvent]:
        events: list[MediaAudioEvent] = []
        horizon = float(media_time_seconds)

        while True:
            if self._next_audio_event is None:
                self._next_audio_event = self._read_next_audio_event_in_pass()

            if self._next_audio_event is None:
                return events

            if self._next_audio_event.media_time_seconds > horizon:
                return events

            events.append(self._next_audio_event)
            self._next_audio_event = None
