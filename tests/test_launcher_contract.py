from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = ROOT / "Launchers"


def read_launcher(name: str) -> str:
    return (LAUNCHERS / name).read_text(encoding="utf-8")


class LauncherContractTests(unittest.TestCase):
    def test_universal_launcher_owns_the_production_contract(self):
        launcher = read_launcher("Stream_NDI_Default_8K.command")

        required = (
            'NDI_TRANSPORT="${NDI_TRANSPORT:-single-tcp}"',
            'NDI_AUDIO_PREROLL_MS:-0',
            'NDI_VIDEO_PREFETCH_FRAMES:-6',
            'NDI_VIDEO_PIXEL_FORMAT:-nv12',
            'NDI_VIDEO_HWACCEL:-videotoolbox',
            'NDI_PRELOAD_AUDIO:-1',
            'NDI_DIAGNOSTICS:-1',
            "ndi_prepare_transport",
            "ndi_prepare_python",
            "--preload-audio",
            "--video-prefetch-frames",
            "--audio-file",
        )
        for token in required:
            self.assertIn(token, launcher)

    def test_every_public_media_profile_delegates_to_the_universal_launcher(self):
        direct_profiles = (
            "Stream_NDI_Default.command",
            "Stream_NDI_LowRes_WAN.command",
            "Stream_NDI_Ghost_Towns_8K24.command",
            "Stream_NDI_Operation_High_Mast_8K30.command",
            "Stream_NDI_Solar_Helicity_8K24.command",
        )
        for name in direct_profiles:
            launcher = read_launcher(name)
            self.assertIn('exec "$SCRIPT_DIR/Stream_NDI_Default_8K.command"', launcher)
            self.assertNotIn("./stream_video.py", launcher)

        roi_launcher = read_launcher("Stream_NDI_Default_8K_ROI.command")
        self.assertIn("NDI_ROI_FEEDBACK=1", roi_launcher)
        self.assertIn('exec "$SCRIPT_DIR/Stream_NDI_Default_8K.command"', roi_launcher)

    def test_ghost_profile_requires_optimized_video_and_audio_pair(self):
        launcher = read_launcher("Stream_NDI_Ghost_Towns_8K24.command")

        self.assertIn("Ghost_Towns_8K_UHD_23_836fps_HEVC_AAC.mp4", launcher)
        self.assertIn("Ghost_Towns_8K_AAC_48k_Stereo.m4a", launcher)
        self.assertIn('export NDI_AUDIO_PATH="$OPTIMIZED_AUDIO"', launcher)
        self.assertIn('NDI_VIDEO_PREFETCH_FRAMES:-6', launcher)

    def test_default_1080p_profile_no_longer_uses_legacy_rudp_bgra_path(self):
        launcher = read_launcher("Stream_NDI_Default.command")

        self.assertIn("Videos/big_buck_bunny.mp4", launcher)
        self.assertIn("NDI_ROI_FEEDBACK", launcher)
        self.assertNotIn("ndi_prepare_python", launcher)
        self.assertNotIn("./stream_video.py", launcher)


if __name__ == "__main__":
    unittest.main()
