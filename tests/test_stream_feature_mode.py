import ctypes
import threading
import unittest
import numpy as np
from types import SimpleNamespace

from ndi_native import NativeNdiSender, NDIlib_video_frame_v2_t
from stream_video import _stream_capabilities_metadata


class StreamFeatureModeTests(unittest.TestCase):
    def test_previous_async_metadata_survives_until_next_send_returns(self):
        sender=NativeNdiSender.__new__(NativeNdiSender)
        sender._running=True; sender._sender_ptr=None
        sender._video_send_lock=threading.Lock()
        sender._video_frame=NDIlib_video_frame_v2_t(xres=2,yres=2)
        sender._video_bytes_per_pixel=2; sender._video_pixel_format='uyvy422'
        sender._video_async_metadata=b'old'; sender._video_metadata_bytes=b'new'
        observed=[]
        sender._runtime=SimpleNamespace(lib=SimpleNamespace(NDIlib_send_send_video_async_v2=lambda *_:observed.append(sender._video_async_metadata)))
        sender.write_video(np.zeros(8,dtype=np.uint8))
        self.assertEqual(observed,[b'old'])
        self.assertEqual(sender._video_async_metadata,b'new')

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
