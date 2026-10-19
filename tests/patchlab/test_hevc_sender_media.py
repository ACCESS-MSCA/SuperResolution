import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch
from apps.patchlab.calibration_clip import generate,target_path
from apps.patchlab.study_catalog import inspect_clip
from apps.patchlab.study_sender import SilentNdiMedia

class HevcSenderMediaTests(unittest.TestCase):
    def test_hardware_hevc_decode_preserves_pts_and_packed_ndi_rows(self):
        with tempfile.TemporaryDirectory() as directory,patch('apps.patchlab.calibration_clip.target_path',return_value=target_path(Fraction(25))[:6]):
            source=Path(directory)/'clip.mp4';generate(source,320,180,Fraction(25),encoder='libx265',bitrate_mbps=1)
            clip=inspect_clip(source)
            # Exercise the actual HEVC decoder and UYVY packing, but never
            # advertise a source or interfere with the live headset stream.
            with patch('packages.ndi.native.NativeNdiSender') as native:
                media=SilentNdiMedia(source,clip)
                try:
                    rows=[]
                    while (frame:=media.next_frame()) is not None:
                        self.assertEqual(frame['pixels'].shape,(180,640))
                        self.assertTrue(frame['pixels'].flags.c_contiguous)
                        media.send(frame,'<access_study_frame />');rows.append(frame)
                    self.assertEqual([r['frame_id'] for r in rows],list(range(6)))
                    for i,row in enumerate(rows):self.assertAlmostEqual(row['pts_s'],i/25)
                    self.assertEqual(native.return_value.write_video.call_count,6)
                finally:media.close()
                native.return_value.close.assert_called_once()

if __name__=='__main__':unittest.main()
