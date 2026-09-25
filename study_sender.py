"""Isolated, one-pass silent planar study sender. Does not alter the 8K pipeline."""
import argparse
from collections import OrderedDict
from fractions import Fraction
import json
import os
from pathlib import Path
import queue
import time
from xml.etree.ElementTree import Element, tostring
import numpy as np

from study_catalog import inspect_clip


def packed_uyvy_pixels(frame):
    """Extract packed rows using the same path as the established media reader.

    PyAV can reformat to UYVY but cannot export it through to_ndarray. A plane
    can have alignment padding: NDI expects exactly width * 2 bytes per row.
    """
    packed = frame.reformat(format='uyvy422')
    plane = packed.planes[0]
    rows = np.frombuffer(plane, dtype=np.uint8).reshape(packed.height, plane.line_size)
    return np.ascontiguousarray(rows[:, :packed.width * 2], dtype=np.uint8)


class StudyEngine:
    """All media calls occur on one worker thread; control callbacks only enqueue."""
    def __init__(self, directory, emit, media_factory=None, clock=time.monotonic):
        self.directory = Path(directory).resolve()
        self.emit, self.clock = emit, clock
        self.media_factory = media_factory or SilentNdiMedia
        self.media = self.trial = None
        self.state = 'Idle'
        self.results = OrderedDict()
        self.next_frame = None
        self.anchor = self.pause_started = 0.0
        self.last_frame_id = -1
        self.last_frame = None
        self.hold_until = self.next_hold = 0.0
        self.next_telemetry = 0.0
        self.content_frames_sent = 0

    def identity(self):
        return {k: self.trial[k] for k in ('trial_id', 'stream_epoch')}

    def command(self, msg):
        if msg.get('version') != 1 or msg.get('type') != 'command':
            raise ValueError('Unsupported command envelope')
        phase, key = msg['phase'], (msg['command_id'], msg['phase'])
        if key in self.results:
            self.emit(self.results[key]); return
        incoming = msg.get('trial')
        try:
            if phase == 'prepare':
                if self.state not in ('Idle', 'Completed', 'Aborted'):
                    raise ValueError('Previous trial is not closed')
                clip = incoming['clip']
                path = (self.directory / clip['filename']).resolve()
                if path.parent != self.directory:
                    raise ValueError('Clip outside catalog directory')
                actual = inspect_clip(path)
                for field in ('video_id', 'sha256', 'width', 'height', 'fps_rational'):
                    if actual[field] != clip[field]:
                        raise ValueError('Catalog does not match original file')
                if actual['has_audio']:
                    raise ValueError('This study adapter currently supports the supplied silent clips only')
                self.trial = incoming
                self.media = self.media_factory(path, actual)
                self.next_frame = None
                self.last_frame_id = -1
                self.last_frame = None
                self.content_frames_sent = 0
                self.state = 'Ready'
            else:
                if not incoming or not self.trial or any(incoming[k] != self.trial[k] for k in ('trial_id','stream_epoch')):
                    raise ValueError('Stale trial or epoch')
                if phase == 'play' and self.state == 'Ready':
                    self.next_frame = self.media.next_frame()
                    if self.next_frame is None:
                        raise ValueError('Empty video')
                    self.anchor = self.clock() - self.next_frame['pts_s']
                    self.state = 'Playing'
                    self.tick()  # ACK after first send, not after merely receiving Play.
                elif phase == 'pause' and self.state in ('Playing', 'Ended'):
                    self.pause_started = self.clock()
                    if self.state != 'Ended': self.state = 'Paused'
                elif phase == 'resume' and self.state == 'Paused':
                    self.anchor += self.clock() - self.pause_started
                    self.state = 'Playing'
                elif phase in ('release','finish','abort'):
                    if self.media: self.media.close()
                    self.media = self.next_frame = self.last_frame = None
                    self.state = 'Aborted' if phase == 'abort' else 'Completed'
                else:
                    raise ValueError('Invalid sender lifecycle transition')
            result = dict(version=1, type='ack', command_id=key[0], phase=phase,
                          status='completed', **self.identity())
        except Exception as exc:
            if self.media: self.media.close()
            self.media = self.next_frame = None
            self.state = 'Aborted'
            result = dict(version=1, type='ack', command_id=key[0], phase=phase,
                          status='failed', error=str(exc),
                          trial_id=(incoming or {}).get('trial_id'),
                          stream_epoch=(incoming or {}).get('stream_epoch'))
        self.results[key] = result
        if len(self.results) > 1000: self.results.popitem(last=False)
        self.emit(result)

    def tick(self):
        self.emit_telemetry()
        if self.state == 'Ended':
            if self.clock() >= self.hold_until:
                self.close()
                self.emit(dict(version=1,type='fault',error='Terminal frame acknowledgment timeout'))
                return
            if self.last_frame is not None and self.clock() >= self.next_hold:
                self.send_frame(self.last_frame, terminal_hold=True)
                self.next_hold = self.clock() + 1.0 / float(Fraction(self.trial['clip']['fps_rational']))
            return
        if self.state != 'Playing': return
        if self.next_frame is None:
            self.next_frame = self.media.next_frame()
            if self.next_frame is None:
                self.state = 'Ended'
                self.hold_until = self.clock() + 10.0
                self.next_hold = self.clock()
                self.emit(dict(version=1, type='ended', last_frame_id=self.last_frame_id, **self.identity()))
                return  # Never seek to zero or silently loop.
        frame = self.next_frame
        if self.clock() < self.anchor + frame['pts_s']: return
        self.send_frame(frame)
        self.last_frame_id = frame['frame_id']
        self.last_frame = frame
        self.next_frame = None

    def send_frame(self, frame, terminal_hold=False):
        # Same content frame/PTS, explicitly marked transport tail. Not a loop.
        attrs = dict(schema_version='1', **self.identity(), terminal_hold='1' if terminal_hold else '0',
                     video_id=self.trial['clip']['video_id'],
                     frame_id=str(frame['frame_id']), pts_s=format(frame['pts_s'], '.9f'))
        metadata = tostring(Element('access_study_frame', attrs), encoding='unicode')
        self.media.send(frame, metadata)
        if not terminal_hold: self.content_frames_sent += 1

    def emit_telemetry(self):
        if not self.trial: return
        now = self.clock()
        if now < self.next_telemetry: return
        self.next_telemetry = now + .5
        clip = self.trial.get('clip', {}) if self.trial else {}
        payload = dict(sender_state=self.state, frame_id=self.last_frame_id,
                       pts_s=self.last_frame['pts_s'] if self.last_frame else -1,
                       frames_sent=self.content_frames_sent,
                       clip_duration_s=clip.get('duration_s', 0),
                       video_id=clip.get('video_id'))
        if self.trial: payload.update(self.identity())
        self.emit(dict(version=1, type='telemetry', payload=payload))

    def close(self):
        if self.media: self.media.close()
        self.media = self.next_frame = self.last_frame = None
        self.state = 'Aborted'


class SilentNdiMedia:
    def __init__(self, path, clip):
        import av
        from ndi_native import NativeNdiSender
        self.container = av.open(str(path))
        self.sender = None
        try:
            self.frames = iter(self.container.decode(video=0))
            self.index, self.first_pts = 0, None
            self.sender = NativeNdiSender('ACCESS_PATCHLAB', clip['width'], clip['height'],
                Fraction(clip['fps_rational']), clock_video=False, video_pixel_format='uyvy422')
            self.sender.open()
        except Exception:
            self.close(); raise

    def next_frame(self):
        frame = next(self.frames, None)
        if frame is None: return None
        if frame.pts is None or frame.time_base is None: raise ValueError('Missing source frame PTS')
        pts = float(frame.pts * frame.time_base)
        if self.first_pts is None: self.first_pts = pts
        result = dict(frame_id=self.index, pts_s=pts-self.first_pts,
                      pixels=packed_uyvy_pixels(frame))
        self.index += 1
        return result

    def send(self, frame, metadata):
        self.sender.set_video_metadata(metadata)
        self.sender.write_video(frame['pixels'])

    def close(self):
        if self.sender: self.sender.close()
        self.container.close()


def main():
    import threading
    import websocket
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(__file__).parent/'Videos/Prod')
    parser.add_argument('--coordinator', default='ws://127.0.0.1:8090/ws')
    args = parser.parse_args()
    commands, disconnected = queue.Queue(maxsize=32), threading.Event()
    def emit(msg): ws.send(json.dumps(msg, allow_nan=False))
    engine = StudyEngine(args.directory, emit)
    def opened(sock): sock.send(json.dumps(dict(version=1,type='register',role='python',token=os.environ.get('PATCHLAB_TOKEN',''))))
    def message(sock, raw):
        try:
            msg = json.loads(raw)
            if msg.get('type') == 'command': commands.put_nowait(msg)
            elif msg.get('type') == 'error': disconnected.set(); sock.close()
        except Exception:
            disconnected.set(); sock.close()
    ws = websocket.WebSocketApp(args.coordinator, on_open=opened, on_message=message,
        on_close=lambda *_: disconnected.set(), on_error=lambda *_: disconnected.set())
    network = threading.Thread(target=lambda:ws.run_forever(suppress_origin=True,ping_interval=5,ping_timeout=3), daemon=True)
    network.start()
    try:
        while not disconnected.is_set():
            try: engine.command(commands.get(timeout=0.002))
            except queue.Empty: pass
            engine.tick()
    except KeyboardInterrupt: pass
    except Exception:
        try: emit(dict(version=1,type='fault',error='Study sender failed; trial aborted'))
        except Exception: pass
        raise
    finally:
        engine.close(); ws.close(); network.join(timeout=3)


if __name__ == '__main__': main()
