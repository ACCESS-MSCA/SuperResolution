import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from apps.patchlab.calibration_clip import generate, target_path, inspect_clip


class CalibrationClipTests(unittest.TestCase):
    def test_hevc_generation_preserves_path_and_exact_timestamps(self):
        records = target_path(Fraction(30))[:6]
        with tempfile.TemporaryDirectory() as directory, patch(
            "apps.patchlab.calibration_clip.target_path", return_value=records
        ):
            output = Path(directory) / "calibration.mp4"
            sidecar = generate(output, 320, 180, Fraction(30), encoder="libx265", bitrate_mbps=1)
            info = inspect_clip(output)
            self.assertEqual((info["codec"], info["profile"], info["pixel_format"], info["codec_tag"]),
                             ("hevc", "Main", "yuv420p", "hvc1"))
            self.assertEqual(info["audio_streams"], 0)
            self.assertEqual(sidecar["frames"], records)
            self.assertEqual(sidecar["frame_count"], 6)
            self.assertEqual(sidecar["sha256"], info["sha256"])
            self.assertEqual(json.loads(output.with_suffix(".path.json").read_text()), sidecar)
            with self.assertRaises(FileExistsError):
                generate(output, 320, 180, Fraction(30))

    def test_invalid_delivery_parameters_do_not_publish_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "invalid.mp4"
            for width, height, fps, bitrate in [(321,180,30,160),(320,180,0,160),(320,180,30,0),
                                               (8192,4608,30,160),(320,180,30,float("nan"))]:
                with self.assertRaises(ValueError):
                    generate(output,width,height,Fraction(fps),bitrate_mbps=bitrate)
                self.assertFalse(output.exists())

    def test_existing_study_path_is_unchanged(self):
        old = Path(__file__).resolve().parents[2] / "media/patchlab/calibration/patchlab_calibration_8k30.path.json"
        if not old.exists():
            self.skipTest("Historical sidecar not installed")
        self.assertEqual(target_path(Fraction(30)), json.loads(old.read_text())["frames"])


if __name__ == "__main__":
    unittest.main()
