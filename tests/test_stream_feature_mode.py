import ctypes
import threading
import unittest
import numpy as np
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import patch

from ndi_native import (
    NDI_SEND_TIMECODE_SYNTHESIZE,
    NDI_VIDEO_FOURCC_I420,
    NDI_VIDEO_FOURCC_NV12,
    NativeNdiSender,
    NDIlib_audio_frame_v3_t,
    NDIlib_video_frame_v2_t,
)
from stream_video import (
    _VideoPrefetcher,
    _media_timecode,
    _should_emit_slow_event,
    _stream_capabilities_metadata,
)
from media_reader import (
    LoopingMediaReader,
    _normalize_video_hwaccel,
    _select_video_pixel_format,
)


class StreamFeatureModeTests(unittest.TestCase):
    def test_video_prefetch_can_fill_before_timeline_start(self):
        class Reader:
            value = 0

            def read_next(self):
                self.value += 1
                return self.value

        prefetcher = _VideoPrefetcher(Reader(), capacity=3)
        prefetcher.start()
        try:
            self.assertEqual(prefetcher.wait_until_ready(timeout=1.0), 3)
            event, _, _ = prefetcher.read_next()
            self.assertEqual(event, 1)
        finally:
            prefetcher.close()

    def test_video_prefetch_close_releases_queued_frame_leases(self):
        class Event:
            def __init__(self):
                self.release_count = 0

            def release(self):
                self.release_count += 1

        class Reader:
            def __init__(self):
                self.events = []

            def read_next(self):
                event = Event()
                self.events.append(event)
                return event

        reader = Reader()
        prefetcher = _VideoPrefetcher(reader, capacity=3)
        prefetcher.start()
        self.assertEqual(prefetcher.wait_until_ready(timeout=1.0), 3)
        prefetcher.close()

        self.assertGreaterEqual(len(reader.events), 3)
        self.assertTrue(all(event.release_count == 1 for event in reader.events))

    def test_auto_video_format_uses_i420_only_for_planar_420_sources(self):
        self.assertEqual(_select_video_pixel_format("auto", "yuv420p"), "i420")
        self.assertEqual(_select_video_pixel_format("auto", "yuvj420p"), "i420")
        self.assertEqual(_select_video_pixel_format("auto", "yuv422p"), "uyvy422")
        self.assertEqual(_select_video_pixel_format("auto", "unknown"), "uyvy422")
        self.assertEqual(_select_video_pixel_format("bgra", "yuv420p"), "bgra")

    def test_video_hwaccel_normalization_is_explicit(self):
        for value in (None, "", "none", "off", "software"):
            self.assertIsNone(_normalize_video_hwaccel(value))
        self.assertEqual(_normalize_video_hwaccel("VideoToolbox"), "videotoolbox")

    def test_slow_event_details_are_rate_limited_without_losing_counters(self):
        self.assertTrue(_should_emit_slow_event(1))
        self.assertTrue(_should_emit_slow_event(5))
        self.assertFalse(_should_emit_slow_event(6))
        self.assertTrue(_should_emit_slow_event(120))

    def test_previous_async_metadata_survives_until_next_send_returns(self):
        sender=NativeNdiSender.__new__(NativeNdiSender)
        sender._running=True; sender._sender_ptr=None
        sender._video_send_lock=threading.Lock()
        sender._video_frame=NDIlib_video_frame_v2_t(xres=2,yres=2)
        sender._video_expected_size=8; sender._video_pixel_format='uyvy422'
        sender._video_async_buffer=None; sender._video_async_release=None
        sender._video_async_metadata=b'old'; sender._video_metadata_bytes=b'new'
        observed=[]
        sender._runtime=SimpleNamespace(lib=SimpleNamespace(NDIlib_send_send_video_async_v2=lambda *_:observed.append(sender._video_async_metadata)))
        sender.write_video(np.zeros(8,dtype=np.uint8), timecode=123456789)
        self.assertEqual(observed,[b'old'])
        self.assertEqual(sender._video_async_metadata,b'new')
        self.assertEqual(sender._video_frame.timecode, 123456789)

    def test_previous_async_pixel_lease_is_released_after_next_submit(self):
        sender = NativeNdiSender.__new__(NativeNdiSender)
        sender._running = True
        sender._sender_ptr = None
        sender._video_send_lock = threading.Lock()
        sender._video_frame = NDIlib_video_frame_v2_t(xres=2, yres=2)
        sender._video_expected_size = 8
        sender._video_pixel_format = "uyvy422"
        sender._video_async_buffer = None
        sender._video_async_metadata = None
        sender._video_metadata_bytes = None
        released = []
        sender._video_async_release = lambda: released.append("previous")
        sender._runtime = SimpleNamespace(
            lib=SimpleNamespace(NDIlib_send_send_video_async_v2=lambda *_: None)
        )

        sender.write_video(
            np.zeros(8, dtype=np.uint8),
            release_callback=lambda: released.append("current"),
        )
        self.assertEqual(released, ["previous"])

        sender.write_video(np.zeros(8, dtype=np.uint8))
        self.assertEqual(released, ["previous", "current"])

    def test_final_async_pixel_lease_is_released_on_sender_close(self):
        sender = NativeNdiSender.__new__(NativeNdiSender)
        sender._running = True
        sender._sender_ptr = object()
        sender._video_send_lock = threading.Lock()
        sender._audio_send_lock = threading.Lock()
        sender._video_async_buffer = np.zeros(8, dtype=np.uint8)
        sender._video_async_metadata = None
        released = []
        sender._video_async_release = lambda: released.append("final")
        sender._runtime = SimpleNamespace(
            lib=SimpleNamespace(
                NDIlib_send_send_video_async_v2=lambda *_: None,
                NDIlib_send_destroy=lambda *_: None,
            )
        )

        sender.close()

        self.assertEqual(released, ["final"])

    def test_nv12_buffer_pool_reuses_released_allocation(self):
        reader = LoopingMediaReader.__new__(LoopingMediaReader)
        reader._video_buffer_pool = []
        reader._video_buffer_pool_lock = threading.Lock()
        reader._video_buffer_pool_limit = 8
        reader.video_buffer_allocations = 0
        reader.video_buffer_reuses = 0

        first = reader._acquire_video_buffer((6, 8))
        first_array = first.array
        first.release()
        first.release()
        second = reader._acquire_video_buffer((6, 8))

        self.assertIs(second.array, first_array)
        self.assertEqual(reader.video_buffer_allocations, 1)
        self.assertEqual(reader.video_buffer_reuses, 1)
        second.release()

    def test_native_sender_configures_i420_layout(self):
        with patch("ndi_native.get_ndi_runtime", return_value=SimpleNamespace()):
            sender = NativeNdiSender(
                "I420-Test",
                width=8,
                height=4,
                fps=Fraction(30, 1),
                video_pixel_format="i420",
            )

        self.assertEqual(sender._video_frame.FourCC, NDI_VIDEO_FOURCC_I420)
        self.assertEqual(sender._video_frame.line_stride_in_bytes, 8)
        self.assertEqual(sender._video_expected_size, 48)

    def test_native_sender_rejects_odd_i420_dimensions(self):
        with patch("ndi_native.get_ndi_runtime", return_value=SimpleNamespace()):
            with self.assertRaisesRegex(ValueError, "divisible by two"):
                NativeNdiSender(
                    "I420-Test",
                    width=7,
                    height=4,
                    fps=Fraction(30, 1),
                    video_pixel_format="i420",
                )

    def test_native_sender_configures_nv12_layout(self):
        with patch("ndi_native.get_ndi_runtime", return_value=SimpleNamespace()):
            sender = NativeNdiSender(
                "NV12-Test",
                width=8,
                height=4,
                fps=Fraction(24000, 1001),
                video_pixel_format="nv12",
            )

        self.assertEqual(sender._video_frame.FourCC, NDI_VIDEO_FOURCC_NV12)
        self.assertEqual(sender._video_frame.line_stride_in_bytes, 8)
        self.assertEqual(sender._video_expected_size, 48)

    def test_native_sender_assigns_explicit_audio_timecode(self):
        sender = NativeNdiSender.__new__(NativeNdiSender)
        sender._running = True
        sender._sender_ptr = None
        sender._audio_send_lock = threading.Lock()
        sender._audio_frame = NDIlib_audio_frame_v3_t()
        sender._runtime = SimpleNamespace(
            lib=SimpleNamespace(NDIlib_send_send_audio_v3=lambda *_: None)
        )

        sender.write_audio(np.zeros((2, 1024), dtype=np.float32), timecode=987654321)
        self.assertEqual(sender._audio_frame.timecode, 987654321)

        sender.write_audio(np.zeros((2, 1024), dtype=np.float32))
        self.assertEqual(sender._audio_frame.timecode, NDI_SEND_TIMECODE_SYNTHESIZE)

    def test_media_timecode_uses_one_shared_continuous_timeline(self):
        origin = 1_800_000_000_000_000_000
        self.assertEqual(_media_timecode(origin, 0.007, 0.007), origin)
        self.assertEqual(
            _media_timecode(origin, 0.007, 2.507),
            origin + 25_000_000,
        )

    def test_capability_metadata_announces_roi_state(self) -> None:
        self.assertEqual(
            _stream_capabilities_metadata(True),
            '<access_stream schema_version="1" roi_feedback="1" />',
        )
        self.assertEqual(
            _stream_capabilities_metadata(False),
            '<access_stream schema_version="1" roi_feedback="0" />',
        )

    def test_native_sender_keeps_video_metadata_alive(self) -> None:
        sender = NativeNdiSender.__new__(NativeNdiSender)
        sender._video_send_lock = threading.Lock()
        sender._video_frame = SimpleNamespace(p_metadata=None)
        sender._video_metadata_bytes = None

        sender.set_video_metadata('<access_stream roi_feedback="1" />')

        self.assertEqual(
            sender._video_metadata_bytes,
            b'<access_stream roi_feedback="1" />',
        )
        self.assertEqual(
            ctypes.string_at(sender._video_frame.p_metadata),
            sender._video_metadata_bytes,
        )

        sender.set_video_metadata(None)
        self.assertIsNone(sender._video_metadata_bytes)
        self.assertIsNone(sender._video_frame.p_metadata)


if __name__ == "__main__":
    unittest.main()
