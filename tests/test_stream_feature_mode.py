import ctypes
import threading
import unittest
from types import SimpleNamespace

from ndi_native import NativeNdiSender
from stream_video import _stream_capabilities_metadata


class StreamFeatureModeTests(unittest.TestCase):
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
