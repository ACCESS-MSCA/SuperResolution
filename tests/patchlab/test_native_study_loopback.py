"""Opt-in real HEVC -> async Study sender -> libndi receiver integration.

Runs on a tiny generated fixture in a private discovery group only. It neither
contacts an AVP/coordinator nor advertises ACCESS_PATCHLAB, and is not evidence
of headset/network throughput. Enable with ACCESS_NDI_NATIVE_QA=1.
"""
import ctypes as C
from fractions import Fraction
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4
from xml.etree.ElementTree import fromstring

from apps.patchlab.calibration_clip import generate, target_path
from apps.patchlab.study_catalog import inspect_clip
from apps.patchlab.study_sender import SilentNdiMedia, StudyEngine
from packages.ndi.native import get_ndi_runtime, NDIlib_video_frame_v2_t


class Source(C.Structure):
    _fields_ = [('name', C.c_char_p), ('url', C.c_char_p)]


class FindSettings(C.Structure):
    _fields_ = [('local', C.c_bool), ('groups', C.c_char_p), ('extra_ips', C.c_char_p)]


class RecvSettings(C.Structure):
    _fields_ = [('source', Source), ('color', C.c_int), ('bandwidth', C.c_int),
                ('fields', C.c_bool), ('name', C.c_char_p)]


def receive_api(lib):
    lib.NDIlib_find_create_v2.argtypes = [C.POINTER(FindSettings)]
    lib.NDIlib_find_create_v2.restype = C.c_void_p
    lib.NDIlib_find_get_current_sources.argtypes = [C.c_void_p, C.POINTER(C.c_uint32)]
    lib.NDIlib_find_get_current_sources.restype = C.POINTER(Source)
    lib.NDIlib_find_destroy.argtypes = [C.c_void_p]
    lib.NDIlib_find_destroy.restype = None
    lib.NDIlib_recv_create_v3.argtypes = [C.POINTER(RecvSettings)]
    lib.NDIlib_recv_create_v3.restype = C.c_void_p
    lib.NDIlib_recv_capture_v3.argtypes = [C.c_void_p, C.POINTER(NDIlib_video_frame_v2_t), C.c_void_p, C.c_void_p, C.c_uint32]
    lib.NDIlib_recv_capture_v3.restype = C.c_int
    lib.NDIlib_recv_free_video_v2.argtypes = [C.c_void_p, C.POINTER(NDIlib_video_frame_v2_t)]
    lib.NDIlib_recv_free_video_v2.restype = None
    lib.NDIlib_recv_destroy.argtypes = [C.c_void_p]
    lib.NDIlib_recv_destroy.restype = None


@unittest.skipUnless(os.environ.get('ACCESS_NDI_NATIVE_QA') == '1', 'explicit isolated native NDI QA only')
class NativeStudyLoopbackTests(unittest.TestCase):
    def test_real_hevc_async_sender_preserves_preroll_frame_identity_and_final_tail(self):
        runtime = get_ndi_runtime()
        lib = runtime.lib
        receive_api(lib)
        unique = uuid4().hex
        group = 'ACCESS_PRIVATE_STUDY_QA_' + unique
        name = 'ACCESS_QA_STUDY_' + unique
        finder = lib.NDIlib_find_create_v2(C.byref(FindSettings(True, group.encode(), None)))
        self.assertTrue(finder)
        receiver = None
        engine = None
        observed = []
        packets = []
        try:
            with tempfile.TemporaryDirectory(prefix='access-native-study-') as directory:
                source = Path(directory) / 'fixture.mp4'
                # Only reduce fixture length; all media/NDI/decoder code is real.
                with patch('apps.patchlab.calibration_clip.target_path', return_value=target_path(Fraction(25))[:6]):
                    generate(source, 320, 180, Fraction(25), encoder='libx265', bitrate_mbps=1)
                clip = inspect_clip(source)
                self.assertAlmostEqual(clip['duration_s'], 6 / 25)
                trial = dict(trial_id='qa-' + unique, stream_epoch='epoch-' + unique, clip=clip)
                engine = StudyEngine(directory, packets.append,
                    lambda path, actual: SilentNdiMedia(path, actual, ndi_name=name, ndi_groups=group))
                def command(phase):
                    engine.command(dict(version=1, type='command', command_id=phase + unique, phase=phase, trial=trial))
                    self.assertEqual(packets[-1].get('status'), 'completed', packets[-1])
                command('prepare')
                self.assertEqual(engine.state, 'Ready')
                # Discovery may take a moment; exercise the actual repeated
                # frame-zero transport preroll while waiting, not a new sender.
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and not receiver:
                    engine.tick()
                    count = C.c_uint32()
                    sources = lib.NDIlib_find_get_current_sources(finder, C.byref(count))
                    for i in range(count.value):
                        if name.encode() in sources[i].name:
                            item = Source(sources[i].name, sources[i].url)
                            settings = RecvSettings(item, 100, 100, False, b'ACCESS isolated Study native QA')
                            receiver = lib.NDIlib_recv_create_v3(C.byref(settings))
                            break
                    time.sleep(.01)
                self.assertTrue(receiver, 'private-group Study source not discovered')

                def capture():
                    frame = NDIlib_video_frame_v2_t()
                    if lib.NDIlib_recv_capture_v3(receiver, C.byref(frame), None, None, 5) != 1:
                        return None
                    try:
                        self.assertEqual((frame.xres, frame.yres), (320, 180))
                        self.assertEqual((frame.frame_rate_N, frame.frame_rate_D), (25, 1))
                        self.assertTrue(frame.p_data)
                        metadata = fromstring(frame.p_metadata.decode())
                        self.assertEqual(metadata.tag, 'access_study_frame')
                        for field in ('trial_id', 'stream_epoch'):
                            self.assertEqual(metadata.attrib[field], trial[field])
                        self.assertEqual(metadata.attrib['video_id'], clip['video_id'])
                        result = dict(frame=int(metadata.attrib['frame_id']), pts=float(metadata.attrib['pts_s']),
                                      tail=metadata.attrib['terminal_hold'] == '1', fourcc=frame.FourCC)
                        observed.append(result)
                        return result
                    finally:
                        lib.NDIlib_recv_free_video_v2(receiver, C.byref(frame))

                deadline = time.monotonic() + 8
                first = None
                while time.monotonic() < deadline and first is None:
                    engine.tick()
                    first = capture()
                self.assertIsNotNone(first, 'discovered source delivered no actual native video')
                self.assertEqual((first['frame'], first['pts'], first['tail']), (0, 0, True))
                command('prime')
                command('advance')
                deadline = time.monotonic() + 5
                tail = None
                while time.monotonic() < deadline and tail is None:
                    engine.tick()
                    frame = capture()
                    if frame and frame['frame'] == 5 and frame['tail']:
                        tail = frame
                self.assertIsNotNone(tail, 'native receiver did not observe final held frame')
                content = [frame for frame in observed if not frame['tail']]
                self.assertEqual(sorted(set(frame['frame'] for frame in content)), list(range(6)))
                for frame in observed:
                    self.assertAlmostEqual(frame['pts'], frame['frame'] / 25)
                self.assertEqual(engine.content_frames_sent, 6)
                self.assertEqual(engine.state, 'Ended')
                command('release')
                self.assertIsNone(engine.media)
                print(json.dumps(dict(test='real-study-async-hevc-native-loopback',
                    content_frames=sorted(set(frame['frame'] for frame in content)),
                    received_frames=len(observed), received_fourcc=sorted(set(frame['fourcc'] for frame in observed)),
                    runtime=runtime.version_string(), private_discovery_group=True, headset_test=False)))
        finally:
            if engine:
                engine.close()
            if receiver:
                lib.NDIlib_recv_destroy(receiver)
            lib.NDIlib_find_destroy(finder)


if __name__ == '__main__':
    unittest.main()
