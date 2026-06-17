from __future__ import annotations

import atexit
import ctypes
import ctypes.util
import threading
from pathlib import Path
from typing import Optional

import numpy as np


NDI_VIDEO_FOURCC_BGRA = 1095911234
NDI_AUDIO_FOURCC_FLTP = 1884572742
NDI_FRAME_FORMAT_PROGRESSIVE = 1
NDI_SEND_TIMECODE_SYNTHESIZE = 9223372036854775807


class NDIlib_send_create_t(ctypes.Structure):
    _fields_ = [
        ("p_ndi_name", ctypes.c_char_p),
        ("p_groups", ctypes.c_char_p),
        ("clock_video", ctypes.c_bool),
        ("clock_audio", ctypes.c_bool),
    ]


class NDIlib_video_frame_v2_t(ctypes.Structure):
    _fields_ = [
        ("xres", ctypes.c_int),
        ("yres", ctypes.c_int),
        ("FourCC", ctypes.c_int),
        ("frame_rate_N", ctypes.c_int),
        ("frame_rate_D", ctypes.c_int),
        ("picture_aspect_ratio", ctypes.c_float),
        ("frame_format_type", ctypes.c_int),
        ("timecode", ctypes.c_int64),
        ("p_data", ctypes.POINTER(ctypes.c_uint8)),
        ("line_stride_in_bytes", ctypes.c_int),
        ("p_metadata", ctypes.c_char_p),
        ("timestamp", ctypes.c_int64),
    ]


class NDIlib_audio_frame_v3_t(ctypes.Structure):
    _fields_ = [
        ("sample_rate", ctypes.c_int),
        ("no_channels", ctypes.c_int),
        ("no_samples", ctypes.c_int),
        ("timecode", ctypes.c_int64),
        ("FourCC", ctypes.c_int),
        ("p_data", ctypes.POINTER(ctypes.c_uint8)),
        ("channel_stride_in_bytes", ctypes.c_int),
        ("p_metadata", ctypes.c_char_p),
        ("timestamp", ctypes.c_int64),
    ]


class NDIlib_metadata_frame_t(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_int),
        ("timecode", ctypes.c_int64),
        ("p_data", ctypes.c_void_p),
    ]


class _NdiRuntime:
    def __init__(self) -> None:
        self.lib = self._load_lib()
        self._configure_api()
        ok = bool(self.lib.NDIlib_initialize())
        if not ok:
            raise RuntimeError("NDIlib_initialize failed.")
        atexit.register(self._destroy)

    def _load_lib(self):
        candidates = []
        for name in ("libndi", "ndi", "libndi.dylib", "Processing.NDI.Lib.x64"):
            found = ctypes.util.find_library(name)
            if found:
                candidates.append(found)

        try:
            import cyndilib  # Optional legacy fallback.

            bundled = Path(cyndilib.__file__).resolve().parent / ".dylibs" / "libndi.dylib"
            if bundled.exists():
                candidates.append(str(bundled))
        except Exception:
            pass

        candidates.extend([
            "/usr/local/lib/libndi.dylib",
            "libndi.dylib",
            "libndi",
            "ndi",
            "Processing.NDI.Lib.x64",
        ])

        seen = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                return ctypes.CDLL(candidate)
            except OSError:
                continue

        raise RuntimeError(
            "Could not load NDI runtime library. Install NDI runtime and ensure libndi is visible."
        )

    def _configure_api(self) -> None:
        self.lib.NDIlib_initialize.argtypes = []
        self.lib.NDIlib_initialize.restype = ctypes.c_bool

        self.lib.NDIlib_destroy.argtypes = []
        self.lib.NDIlib_destroy.restype = None

        self.lib.NDIlib_version.argtypes = []
        self.lib.NDIlib_version.restype = ctypes.c_char_p

        self.lib.NDIlib_send_create.argtypes = [ctypes.POINTER(NDIlib_send_create_t)]
        self.lib.NDIlib_send_create.restype = ctypes.c_void_p

        self.lib.NDIlib_send_destroy.argtypes = [ctypes.c_void_p]
        self.lib.NDIlib_send_destroy.restype = None

        self.lib.NDIlib_send_send_video_v2.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(NDIlib_video_frame_v2_t),
        ]
        self.lib.NDIlib_send_send_video_v2.restype = None

        self.lib.NDIlib_send_send_audio_v3.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(NDIlib_audio_frame_v3_t),
        ]
        self.lib.NDIlib_send_send_audio_v3.restype = None

        self.lib.NDIlib_send_capture.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(NDIlib_metadata_frame_t),
            ctypes.c_uint32,
        ]
        self.lib.NDIlib_send_capture.restype = ctypes.c_int

        self.lib.NDIlib_send_free_metadata.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(NDIlib_metadata_frame_t),
        ]
        self.lib.NDIlib_send_free_metadata.restype = None

    def _destroy(self) -> None:
        lib = getattr(self, "lib", None)
        if lib is not None:
            lib.NDIlib_destroy()


_runtime_lock = threading.Lock()
_runtime_instance: Optional[_NdiRuntime] = None


def get_ndi_runtime() -> _NdiRuntime:
    global _runtime_instance
    if _runtime_instance is not None:
        return _runtime_instance
    with _runtime_lock:
        if _runtime_instance is None:
            _runtime_instance = _NdiRuntime()
    return _runtime_instance


class NativeNdiSender:
    def __init__(
        self,
        ndi_name: str,
        width: int,
        height: int,
        fps,
        ndi_groups: str = "",
        clock_video: bool = True,
        clock_audio: bool = False,
    ) -> None:
        self._runtime = get_ndi_runtime()
        self._ndi_name = str(ndi_name)
        self._ndi_groups = str(ndi_groups)
        self._name_bytes = self._ndi_name.encode("utf-8")
        self._groups_bytes = self._ndi_groups.encode("utf-8") if self._ndi_groups else None

        self._create = NDIlib_send_create_t(
            p_ndi_name=self._name_bytes,
            p_groups=self._groups_bytes,
            clock_video=bool(clock_video),
            clock_audio=bool(clock_audio),
        )
        self._sender_ptr = ctypes.c_void_p()
        self._running = False

        self._video_frame = NDIlib_video_frame_v2_t(
            xres=int(width),
            yres=int(height),
            FourCC=NDI_VIDEO_FOURCC_BGRA,
            frame_rate_N=int(fps.numerator),
            frame_rate_D=int(fps.denominator),
            picture_aspect_ratio=0.0,
            frame_format_type=NDI_FRAME_FORMAT_PROGRESSIVE,
            timecode=NDI_SEND_TIMECODE_SYNTHESIZE,
            p_data=None,
            line_stride_in_bytes=int(width) * 4,
            p_metadata=None,
            timestamp=0,
        )
        self._audio_frame: Optional[NDIlib_audio_frame_v3_t] = None

    @property
    def native_sender_ptr(self) -> int:
        return int(self._sender_ptr.value or 0)

    @property
    def name(self) -> str:
        return self._ndi_name

    def configure_audio(self, sample_rate: int, channels: int, max_samples: int = 0) -> None:
        del max_samples
        self._audio_frame = NDIlib_audio_frame_v3_t(
            sample_rate=int(sample_rate),
            no_channels=int(channels),
            no_samples=0,
            timecode=NDI_SEND_TIMECODE_SYNTHESIZE,
            FourCC=NDI_AUDIO_FOURCC_FLTP,
            p_data=None,
            channel_stride_in_bytes=0,
            p_metadata=None,
            timestamp=0,
        )

    def open(self) -> None:
        if self._running:
            return
        ptr = self._runtime.lib.NDIlib_send_create(ctypes.byref(self._create))
        if not ptr:
            raise RuntimeError("NDIlib_send_create failed.")
        self._sender_ptr = ctypes.c_void_p(ptr)
        self._running = True

    def close(self) -> None:
        if not self._running:
            return
        ptr = self._sender_ptr
        self._sender_ptr = ctypes.c_void_p()
        self._running = False
        if ptr:
            self._runtime.lib.NDIlib_send_destroy(ptr)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def write_video(self, data) -> None:
        if not self._running:
            raise RuntimeError("Sender must be opened before writing video.")

        frame_data = np.ascontiguousarray(data, dtype=np.uint8)
        self._video_frame.p_data = frame_data.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        self._runtime.lib.NDIlib_send_send_video_v2(
            self._sender_ptr,
            ctypes.byref(self._video_frame),
        )

    def write_audio(self, data) -> None:
        if not self._running:
            raise RuntimeError("Sender must be opened before writing audio.")
        if self._audio_frame is None:
            raise RuntimeError("Audio must be configured before writing audio.")

        audio_data = np.ascontiguousarray(data, dtype=np.float32)
        if audio_data.ndim != 2:
            raise ValueError("Audio data must have shape (channels, samples).")

        self._audio_frame.no_channels = int(audio_data.shape[0])
        self._audio_frame.no_samples = int(audio_data.shape[1])
        self._audio_frame.channel_stride_in_bytes = int(audio_data.shape[1]) * 4
        self._audio_frame.p_data = audio_data.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        self._runtime.lib.NDIlib_send_send_audio_v3(
            self._sender_ptr,
            ctypes.byref(self._audio_frame),
        )
