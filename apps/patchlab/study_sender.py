"""Isolated, one-pass silent planar study sender. Does not alter the 8K pipeline."""
import argparse
from collections import OrderedDict
from fractions import Fraction
import json
import os
from pathlib import Path
import queue
import sys
import time
import threading
from xml.etree.ElementTree import Element, tostring
import numpy as np

from .study_catalog import inspect_clip, inspect_clip_metadata
from .prepare_renditions import decode_options


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def file_signature(path):
    info = path.stat()
    if not path.is_file():
        raise ValueError('Clip is not a regular file')
    return dict(dev=str(info.st_dev), ino=str(info.st_ino), size=str(info.st_size),
                mtimeNs=str(info.st_mtime_ns), ctimeNs=str(info.st_ctime_ns))


class VerifiedClipCache:
    """Process-local integrity results; changes force a fresh full SHA check.

    Startup attestations come only from the authenticated local coordinator,
    which hashed these same files before binding the station. They are accepted
    only when device/inode/size/nanosecond mtime+ctime still match on this Mac.
    Nothing is persisted or inferred from the filename/catalog alone.
    """
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.entries = {}

    def path(self, filename):
        path = (self.directory / filename).resolve()
        if not path.is_relative_to(self.directory) or path == self.directory:
            raise ValueError('Clip outside catalog directory')
        return path

    def inspect(self, path):
        path = self.path(path)
        before = file_signature(path)
        entry = self.entries.get(path)
        if entry and entry[0] == before:
            return dict(entry[1]), True
        self.entries.pop(path, None)
        actual = inspect_clip(path)
        if file_signature(path) != before:
            raise ValueError('Clip changed during integrity verification')
        self.entries[path] = (before, dict(actual))
        return actual, False

    def seed(self, verified):
        loaded = 0
        for item in verified:
            path = self.path(item['filename'])
            try:
                before = file_signature(path)
            except (OSError, ValueError):
                continue  # A missing/replaced file will fail a later prepare safely.
            digest = item.get('sha256', '')
            if before != item.get('file_signature') or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                continue  # Changed/stale attestations never bypass hashing.
            try:
                actual = {**inspect_clip_metadata(path), 'sha256':digest, 'video_id':'clip_' + digest[:16]}
            except (OSError, ValueError):
                continue
            if file_signature(path) != before:
                continue
            self.entries[path] = (before, actual)
            loaded += 1
        return loaded


def packed_uyvy_pixels(frame):
    """Extract packed rows using the same path as the established media reader.

    PyAV can reformat to UYVY but cannot export it through to_ndarray. A plane
    can have alignment padding: NDI expects exactly width * 2 bytes per row.
    """
    packed = frame.reformat(format='uyvy422')
    plane = packed.planes[0]
    rows = np.frombuffer(plane, dtype=np.uint8).reshape(packed.height, plane.line_size)
    return np.ascontiguousarray(rows[:, :packed.width * 2], dtype=np.uint8)


def packed_nv12_pixels(frame):
    """Preserve decoder Y/UV samples; strip only row padding (no 8K 422 convert)."""
    packed = frame if frame.format.name == 'nv12' else frame.reformat(format='nv12')
    width, height = packed.width, packed.height
    output = np.empty((height + height // 2, width), dtype=np.uint8)
    for plane, rows, start in ((packed.planes[0], height, 0), (packed.planes[1], height // 2, height)):
        source = np.frombuffer(plane, dtype=np.uint8).reshape(rows, plane.line_size)
        output[start:start + rows] = source[:, :width]
    return output


class FramePrefetch:
    """Bounded lossless decode queue. EOF/errors ordered after all real frames.

    The producer owns its decoder, including finalization. Cancellation never
    closes a PyAV container concurrently with decode and never drops study frames.
    """
    def __init__(self, iterator, close, capacity=6):
        self.queue = queue.Queue(maxsize=capacity)
        self.stop = threading.Event()
        self.iterator, self.finalize = iterator, close
        self.thread = threading.Thread(target=self.run, daemon=True, name='Patchlab video prefetch')
        self.thread.start()

    def put(self, item):
        while not self.stop.is_set():
            try:
                self.queue.put(item, timeout=.1)
                return True
            except queue.Full:
                pass
        return False

    def run(self):
        try:
            for frame in self.iterator:
                if not self.put((frame, None)):
                    return
            self.put((None, None))
        except Exception as exc:
            self.put((None, exc))
        finally:
            self.finalize()

    def next(self):
        if self.stop.is_set():
            raise RuntimeError('Prefetch stopped')
        frame, error = self.queue.get(timeout=10)
        if error is not None:
            raise error
        return frame

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)


class StudyEngine:
    """Control callbacks enqueue; the media worker submits ordered frames.

    SilentNdiMedia delegates decode/packing to its bounded prefetch owner. Only
    that producer touches the decoder; the media worker owns NDI submission.
    """
    def __init__(self, directory, emit, media_factory=None, clock=time.monotonic):
        self.directory = Path(directory).resolve()
        self.emit, self.clock = emit, clock
        self.media_factory = media_factory or SilentNdiMedia
        self.media = self.trial = None
        self.state = 'Idle'
        self.results = OrderedDict()
        self.next_frame = self.first_frame = None
        self.anchor = self.pause_started = 0.0
        self.last_frame_id = -1
        self.last_frame = None
        self.hold_until = self.next_hold = 0.0
        self.next_telemetry = 0.0
        self.content_frames_sent = 0
        self.verified_clips = VerifiedClipCache(self.directory)

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
                # A failed prepare must still retain the exact attempted identity
                # so an explicit discard can stop/acknowledge that attempt.
                self.trial = incoming
                clip = incoming['clip']
                rendition = incoming.get('media') or clip
                if rendition.get('mode', 'ndi') != 'ndi':
                    raise ValueError('The NDI sender only accepts NDI trials')
                path = self.verified_clips.path(rendition['filename'])
                started = time.monotonic()
                actual, cached = self.verified_clips.inspect(path)
                for field in ('sha256', 'width', 'height', 'fps_rational'):
                    if actual[field] != rendition[field]:
                        raise ValueError('Rendition does not match selected file')
                if rendition is clip and actual['video_id'] != clip['video_id']:
                    raise ValueError('Catalog does not match original file')
                if actual['has_audio']:
                    raise ValueError('This study adapter currently supports the supplied silent clips only')
                self.media = self.media_factory(path, actual)
                self.next_frame = self.media.next_frame()
                if self.next_frame is None:
                    raise ValueError('Empty video')
                self.first_frame, self.next_frame = self.next_frame, None
                self.last_frame_id = -1
                self.last_frame = None
                self.content_frames_sent = 0
                self.state = 'Ready'
                self.send_frame(self.first_frame, terminal_hold=True)
                self.next_hold = self.clock() + 1.0 / float(Fraction(self.trial['clip']['fps_rational']))
                print(f'[PATCHLAB PREPARE] ready seconds={time.monotonic()-started:.3f} integrity={"cached" if cached else "full_sha256"}', file=sys.stderr, flush=True)
            else:
                if phase in ('release', 'finish', 'abort') and self.trial is None and self.media is None and self.state == 'Idle':
                    if not incoming or not incoming.get('trial_id') or not incoming.get('stream_epoch'):
                        raise ValueError('Missing terminal command identity')
                    # A restarted process owns no source. Acknowledge cleanup,
                    # never prepare, prime, decode or resume the interrupted clip.
                    self.trial = incoming
                if not incoming or not self.trial or any(incoming[k] != self.trial[k] for k in ('trial_id','stream_epoch')):
                    raise ValueError('Stale trial or epoch')
                if phase == 'prime' and self.state == 'Ready':
                    self.state = 'Armed'
                    self.send_frame(self.first_frame)
                    self.last_frame_id = self.first_frame['frame_id']
                    self.last_frame = self.first_frame
                    self.next_hold = self.clock() + 1.0 / float(Fraction(self.trial['clip']['fps_rational']))
                elif phase == 'advance' and self.state == 'Armed':
                    # Unity has presented and sampled frame zero. Only now may
                    # the source PTS advance, so no clip onset is lost in NDI.
                    self.anchor = self.clock() - self.first_frame['pts_s']
                    self.state = 'Playing'
                elif phase == 'pause' and self.state in ('Playing', 'Ended'):
                    self.pause_started = self.clock()
                    if self.state != 'Ended': self.state = 'Paused'
                elif phase == 'resume' and self.state == 'Paused':
                    self.anchor += self.clock() - self.pause_started
                    self.state = 'Playing'
                elif phase in ('release','finish','abort'):
                    if self.media: self.media.close()
                    self.media = self.next_frame = self.first_frame = self.last_frame = None
                    self.state = 'Aborted' if phase == 'abort' else 'Completed'
                else:
                    raise ValueError('Invalid sender lifecycle transition')
            result = dict(version=1, type='ack', command_id=key[0], phase=phase,
                          status='completed', **self.identity())
        except Exception as exc:
            if self.media: self.media.close()
            self.media = self.next_frame = self.first_frame = None
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
        if self.state in ('Ready', 'Armed'):
            if self.clock() >= self.next_hold:
                self.send_frame(self.first_frame, terminal_hold=self.state == 'Ready',
                                count_content=False)
                self.next_hold = self.clock() + 1.0 / float(Fraction(self.trial['clip']['fps_rational']))
            return
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

    def send_frame(self, frame, terminal_hold=False, count_content=True):
        # Same content frame/PTS, explicitly marked transport tail. Not a loop.
        attrs = dict(schema_version='1', **self.identity(), terminal_hold='1' if terminal_hold else '0',
                     video_id=self.trial['clip']['video_id'],
                     frame_id=str(frame['frame_id']), pts_s=format(frame['pts_s'], '.9f'))
        metadata = tostring(Element('access_study_frame', attrs), encoding='unicode')
        self.media.send(frame, metadata)
        if not terminal_hold and count_content: self.content_frames_sent += 1

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
                       video_id=clip.get('video_id'),
                       media_mode=self.trial.get('media', {}).get('mode', 'ndi'),
                       resolution=self.trial.get('media', {}).get('resolution', 'native'))
        payload.update(getattr(self.media, 'stats', {}))
        if self.trial: payload.update(self.identity())
        self.emit(dict(version=1, type='telemetry', payload=payload))

    def close(self):
        if self.media: self.media.close()
        self.media = self.next_frame = self.first_frame = self.last_frame = None
        self.state = 'Aborted'


class SilentNdiMedia:
    def __init__(self, path, clip, *, ndi_name='ACCESS_PATCHLAB', ndi_groups=''):
        import av
        from packages.ndi.native import NativeNdiSender
        # HEVC delivery masters use VideoToolbox on macOS. Refuse a silent
        # software fallback that could make a correct 8K stream appear stalled.
        self.container = av.open(str(path), **decode_options(path))
        self.sender = None
        self.prefetch = None
        self.stats = dict(sender_pixel_format='nv12', sender_prefetch_capacity=6,
                          sender_decode_pack_ms=0, sender_submit_ms=0)
        try:
            self.frames = iter(self.container.decode(video=0))
            self.index, self.first_pts = 0, None
            self.sender = NativeNdiSender(ndi_name, clip['width'], clip['height'],
                Fraction(clip['fps_rational']), ndi_groups=ndi_groups, clock_video=False, video_pixel_format='nv12')
            self.sender.open()
            self.prefetch = FramePrefetch(self.decoded_frames(), self.container.close)
        except Exception:
            self.close(); raise

    def next_frame(self):
        return self.prefetch.next()

    def decoded_frames(self):
        while True:
            started = time.perf_counter()
            frame = next(self.frames, None)
            if frame is None:
                return
            if frame.pts is None or frame.time_base is None:
                raise ValueError('Missing source frame PTS')
            pts = float(frame.pts * frame.time_base)
            if self.first_pts is None:
                self.first_pts = pts
            result = dict(frame_id=self.index, pts_s=pts-self.first_pts, pixels=packed_nv12_pixels(frame))
            self.stats['sender_decode_pack_ms'] = (time.perf_counter() - started) * 1000
            self.index += 1
            yield result

    def send(self, frame, metadata):
        started = time.perf_counter()
        self.sender.set_video_metadata(metadata)
        self.sender.write_video(frame['pixels'])
        self.stats['sender_submit_ms'] = (time.perf_counter() - started) * 1000
        self.stats['sender_prefetch_ready'] = self.prefetch.queue.qsize()

    def close(self):
        if self.sender: self.sender.close()
        if self.prefetch: self.prefetch.close()
        else: self.container.close()


def main():
    import threading
    import websocket
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--directory',
        type=Path,
        default=REPOSITORY_ROOT / 'media/patchlab/clips',
    )
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
            elif msg.get('type') == 'state' and 'verified_media' in msg:
                commands.put_nowait(dict(type='verified_library', files=msg['verified_media']))
            elif msg.get('type') == 'error': disconnected.set(); sock.close()
        except Exception:
            disconnected.set(); sock.close()
    def closed(_sock, code, _reason):
        print(f'[PATCHLAB TRANSPORT] closed code={code}', file=sys.stderr, flush=True)
        disconnected.set()
    def failed(_sock, error):
        # Exception class only: never credentials or message/header payloads.
        print(f'[PATCHLAB TRANSPORT] error={type(error).__name__}', file=sys.stderr, flush=True)
        disconnected.set()
    ws = websocket.WebSocketApp(args.coordinator, on_open=opened, on_message=message,
        on_close=closed, on_error=failed)
    network = threading.Thread(target=lambda:ws.run_forever(suppress_origin=True,ping_interval=5,ping_timeout=3), daemon=True)
    network.start()
    try:
        while not disconnected.is_set():
            try:
                item = commands.get(timeout=0.002)
                if item.get('type') == 'verified_library':
                    count = engine.verified_clips.seed(item['files'])
                    print(f'[PATCHLAB LIBRARY] Reused {count} startup integrity checks', file=sys.stderr, flush=True)
                else:
                    engine.command(item)
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
