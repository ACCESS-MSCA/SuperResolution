import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch
from apps.patchlab.calibration_clip import generate,target_path,inspect_clip
from apps.patchlab.prepare_delivery import transcode
from apps.patchlab.study_catalog import inspect_clip as study_info

class DeliveryTests(unittest.TestCase):
    def test_hevc_delivery_keeps_original_and_exact_timeline(self):
        with tempfile.TemporaryDirectory() as directory,patch('apps.patchlab.calibration_clip.target_path',return_value=target_path(Fraction(25))[:6]):
            source=Path(directory)/'original.mp4'
            generate(source,320,180,Fraction(25),encoder='libx265',bitrate_mbps=1)
            original=study_info(source)
            output=Path(directory)/'delivery.mp4'
            result=transcode(source,output,1)
            actual=inspect_clip(output)
            self.assertEqual((actual['codec'],actual['profile'],actual['pixel_format'],actual['codec_tag']),('hevc','Main','yuv420p','hvc1'))
            self.assertEqual(result['frame_count'],6)
            self.assertEqual(result['source_sha256'],original['sha256'])
            self.assertEqual(inspect_clip(source)['sha256'],original['sha256'])
            for key in ('width','height','fps_rational','duration_s'):
                self.assertEqual(result[key],original[key])
            with self.assertRaises(FileExistsError):transcode(source,output,1)
            for rate in (0,-1,float('nan'),float('inf')):
                with self.assertRaises(ValueError):transcode(source,Path(directory)/'invalid.mp4',rate)

if __name__=='__main__':unittest.main()
