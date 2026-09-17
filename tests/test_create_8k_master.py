import unittest
from fractions import Fraction

from create_8k_uhd_master import (
    DEFAULT_CONTENT_HEIGHT,
    DEFAULT_HEIGHT,
    DEFAULT_PAD_Y,
    DEFAULT_WIDTH,
    _parse_rate,
    _resolve_geometry,
)


class Create8KUhdMasterTests(unittest.TestCase):
    def test_production_geometry_preserves_aspect_with_chroma_aligned_bars(self):
        self.assertEqual((DEFAULT_WIDTH, DEFAULT_HEIGHT), (7680, 4320))
        self.assertEqual(DEFAULT_CONTENT_HEIGHT, 4050)
        self.assertEqual(DEFAULT_PAD_Y, 134)
        self.assertEqual(DEFAULT_HEIGHT - DEFAULT_CONTENT_HEIGHT - DEFAULT_PAD_Y, 136)
        self.assertEqual(DEFAULT_WIDTH / DEFAULT_CONTENT_HEIGHT, 8192 / 4320)

    def test_rate_parser_supports_source_and_fraction(self):
        source_rate = Fraction(30000, 1001)
        self.assertEqual(_parse_rate("source", source_rate), source_rate)
        self.assertEqual(_parse_rate("24000/1001", source_rate), Fraction(24000, 1001))
        with self.assertRaises(ValueError):
            _parse_rate("0", source_rate)

    def test_geometry_accepts_native_uhd_without_rescaling(self):
        self.assertEqual(_resolve_geometry(8192, 4320), "scale-pad")
        self.assertEqual(_resolve_geometry(7680, 4320), "native-uhd")
        with self.assertRaises(RuntimeError):
            _resolve_geometry(3840, 2160)


if __name__ == "__main__":
    unittest.main()
