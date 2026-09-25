import unittest
from unittest.mock import patch
from study_sender import StudyEngine, SilentNdiMedia, packed_uyvy_pixels
from fractions import Fraction
import av
import numpy as np

CLIP = dict(video_id='clip_test', sha256='0'*64, width=3840,height=2160,
            fps_rational='25',filename='test.mp4',has_audio=False)
TRIAL = dict(trial_id='trial1',stream_epoch='epoch1',clip=CLIP)

class FakeMedia:
    def __init__(self,*args): self.index=0;self.sent=[];self.closed=False
    def next_frame(self):
        if self.index>=3: return None
        f=dict(frame_id=self.index,pts_s=self.index*.04);self.index+=1;return f
    def send(self,frame,metadata): self.sent.append((frame,metadata))
    def close(self): self.closed=True

class StudyTests(unittest.TestCase):
    def setUp(self):
        self.out=[];self.now=0
        self.engine=StudyEngine('/tmp',self.out.append,FakeMedia,lambda:self.now)
        self.inspection=patch('study_sender.inspect_clip',return_value=CLIP)
        self.inspection.start();self.addCleanup(self.inspection.stop)
    def command(self,phase,id=None,trial=TRIAL):
        self.engine.command(dict(version=1,type='command',command_id=id or phase,phase=phase,trial=trial))
    def test_once_and_deduplicated_play(self):
        self.command('prepare');media=self.engine.media
        self.command('play');self.command('play')
        self.now=.04;self.engine.tick();self.now=.08;self.engine.tick();self.engine.tick()
        for _ in range(10):self.engine.tick()
        content=[entry for entry in media.sent if 'terminal_hold="0"' in entry[1]]
        self.assertEqual(len(content),3)
        self.assertEqual(sum(m['type']=='ended' for m in self.out),1)
        self.assertIn('frame_id="2"',media.sent[-1][1])
        self.assertEqual(self.engine.state,'Ended')
    def test_pause_preserves_media_clock(self):
        self.command('prepare');self.command('play');self.command('pause')
        self.now=10;self.engine.tick();self.assertEqual(len(self.engine.media.sent),1)
        self.command('resume');self.engine.tick();self.assertEqual(len(self.engine.media.sent),1)
        self.now=10.05;self.engine.tick();self.assertEqual(len(self.engine.media.sent),2)
    def test_catalog_mismatch_fails_before_media_open(self):
        self.command('prepare',trial={**TRIAL,'clip':{**CLIP,'sha256':'1'*64}})
        self.assertEqual(self.out[-1]['status'],'failed');self.assertIsNone(self.engine.media)
    def test_no_silent_audio_discard(self):
        with patch('study_sender.inspect_clip',return_value={**CLIP,'has_audio':True}):self.command('prepare')
        self.assertEqual(self.out[-1]['status'],'failed')
    def test_path_escape_rejected(self):
        self.command('prepare',trial={**TRIAL,'clip':{**CLIP,'filename':'../private.mp4'}})
        self.assertEqual(self.out[-1]['status'],'failed')
    def test_abort_closes_sender_and_late_play_cannot_restart(self):
        self.command('prepare');media=self.engine.media;self.command('abort');self.command('play')
        self.assertTrue(media.closed);self.assertEqual(self.out[-1]['status'],'failed')

    def end_clip(self):
        self.command('prepare');media=self.engine.media;self.command('play')
        self.now=.04;self.engine.tick();self.now=.08;self.engine.tick();self.engine.tick()
        return media

    def test_terminal_hold_preserves_id_pts_and_does_not_decode_or_loop(self):
        media=self.end_clip();self.now=.12;self.engine.tick()
        self.assertEqual(media.index,3)
        frame,xml=media.sent[-1]
        self.assertEqual(frame['frame_id'],2);self.assertEqual(frame['pts_s'],.08)
        self.assertIn('terminal_hold="1"',xml)
        self.assertFalse(media.closed)
        self.command('release');self.assertTrue(media.closed)
        count=len(media.sent);self.now=1;self.engine.tick();self.assertEqual(len(media.sent),count)

    def test_terminal_hold_has_hard_timeout_when_coordinator_never_releases(self):
        media=self.end_clip();self.now=11;self.engine.tick()
        self.assertTrue(media.closed);self.assertEqual(self.engine.state,'Aborted')
        self.assertEqual(self.out[-1]['type'],'fault')

    def test_terminal_hold_pacing_does_not_burst(self):
        media=self.end_clip();self.engine.tick();count=len(media.sent)
        for _ in range(100):self.engine.tick()
        self.assertEqual(len(media.sent),count)

    def test_bounded_telemetry_reports_sender_progress_without_changing_media(self):
        self.command('prepare');self.command('play');self.now=.04;self.engine.tick()
        telemetry=[m for m in self.out if m['type']=='telemetry']
        self.assertEqual(len(telemetry),1)
        self.assertEqual(telemetry[0]['payload']['sender_state'],'Playing')
        self.assertEqual(telemetry[0]['payload']['frame_id'],-1)
        for _ in range(100): self.engine.tick()
        self.assertEqual(sum(m['type']=='telemetry' for m in self.out),1)
        self.now=.51;self.engine.tick()
        self.assertEqual(sum(m['type']=='telemetry' for m in self.out),2)

class PackedVideoTests(unittest.TestCase):
    def test_real_pyav_conversion_removes_stride_padding(self):
        for width in (18,32):
            source=av.VideoFrame.from_ndarray(np.zeros((6,width,3),dtype=np.uint8),format='rgb24')
            packed=source.reformat(format='uyvy422')
            actual=packed_uyvy_pixels(source)
            plane=packed.planes[0]
            rows=np.frombuffer(plane,dtype=np.uint8).reshape(6,plane.line_size)
            self.assertEqual(actual.shape,(6,width*2))
            self.assertTrue(actual.flags.c_contiguous)
            np.testing.assert_array_equal(actual,rows[:,:width*2])
            del source, packed, plane, rows
            self.assertEqual(actual.nbytes,6*width*2)

    def test_real_media_frame_path_preserves_identity_and_eof(self):
        source=av.VideoFrame.from_ndarray(np.zeros((6,18,3),dtype=np.uint8),format='rgb24')
        source.pts=12;source.time_base=Fraction(1,30)
        media=SilentNdiMedia.__new__(SilentNdiMedia)
        media.frames=iter([source]);media.index=0;media.first_pts=None
        result=media.next_frame()
        self.assertEqual(result['frame_id'],0)
        self.assertEqual(result['pts_s'],0)
        self.assertEqual(result['pixels'].shape,(6,36))
        self.assertIsNone(media.next_frame())

if __name__=='__main__': unittest.main()
