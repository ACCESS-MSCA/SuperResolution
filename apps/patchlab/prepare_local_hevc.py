"""Make an explicit 8K HEVC playback copy without replacing the H.264 master.

Apple's hardware H.264 path rejects the supplied 7680x4320 level-6 clip.
The local copy retains its geometry, every frame and the original 30 Hz PTS.
NDI continues to select the untouched master. Run with the pinned PyAV venv.
"""
from fractions import Fraction
import argparse
import json
from pathlib import Path
import tempfile
import av
from .study_catalog import inspect_clip


def prepare(directory: Path, manifest_path: Path, video_id: str) -> dict:
    manifest = json.loads(manifest_path.read_text())
    clip = next(item for item in manifest['clips'] if item['video_id'] == video_id)
    native = next(item for item in clip['renditions'] if item['id'] == 'native')
    source = directory / native['filename']
    if inspect_clip(source)['sha256'] != clip['source_sha256']:
        raise ValueError('Master hash mismatch')
    destination = directory / '_local' / video_id / 'native_hevc.mp4'
    destination.parent.mkdir(parents=True, exist_ok=True)
    fps = Fraction(native['fps_rational'])
    time_base = Fraction(fps.denominator, fps.numerator)
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.mp4', delete=False) as tmp:
        staging = Path(tmp.name)
    try:
        with av.open(str(source)) as input_media, av.open(str(staging), 'w', options={'movflags': '+faststart'}) as output:
            encoder = output.add_stream('hevc_videotoolbox', rate=fps)
            encoder.width, encoder.height = native['width'], native['height']
            encoder.pix_fmt = 'nv12'
            encoder.bit_rate = 60_000_000
            encoder.codec_context.codec_tag = 'hvc1'
            encoder.options = {'allow_sw': '0', 'realtime': '0', 'profile': 'main', 'bf': '0'}
            count = 0
            for frame in input_media.decode(video=0):
                frame = frame.reformat(format='nv12')
                frame.pts, frame.time_base = count, time_base
                for packet in encoder.encode(frame):
                    output.mux(packet)
                count += 1
                if count % 150 == 0:
                    print(f'Encoded {count}/{native["frame_count"]}', flush=True)
            for packet in encoder.encode():
                output.mux(packet)
        if count != native['frame_count']:
            raise ValueError('Input frame count changed')
        with av.open(str(staging)) as check:
            stream = check.streams.video[0]
            if stream.codec_context.name != 'hevc':
                raise ValueError('Expected HEVC')
            decoded = 0
            for frame in check.decode(video=0):
                if abs(float(frame.pts * frame.time_base - decoded * time_base)) > 0.00001:
                    raise ValueError('Local rendition PTS mismatch')
                decoded += 1
            if decoded != count:
                raise ValueError('Output frame count changed')
        actual = inspect_clip(staging)
        if any(actual[key] != native[key] for key in ('width', 'height', 'fps_rational')):
            raise ValueError('Output geometry changed')
        staging.replace(destination)
        destination.chmod(0o644)
    finally:
        staging.unlink(missing_ok=True)
    local = dict(filename=destination.relative_to(directory).as_posix(), sha256=actual['sha256'],
                 source_sha256=clip['source_sha256'], width=native['width'], height=native['height'],
                 fps_rational=native['fps_rational'], frame_count=count, codec='hevc',
                 encoder='hevc_videotoolbox', requested_bitrate=60_000_000,
                 reason='8K native local playback uses HEVC hardware decoding; NDI master remains H.264')
    native['local_playback'] = local
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(local, indent=2))
    return local


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parents[2] / 'media/patchlab/clips')
    parser.add_argument('--manifest', type=Path, default=Path(__file__).resolve().parents[3] / 'ACCESS_VisionOS_Metal/STUDY_CONTRACTS/renditions.json')
    parser.add_argument('--video-id', default='clip_17434fb41814bfe0')
    args = parser.parse_args()
    prepare(args.directory, args.manifest, args.video_id)
