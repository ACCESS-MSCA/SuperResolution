"""Read-only, content-addressed inventory of study clips (no decoding/conversion)."""
from pathlib import Path
import argparse
import hashlib
import json
import av


def inspect_clip(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError(f'No video stream: {path.name}')
        video = container.streams.video[0]
        duration = float(video.duration * video.time_base) if video.duration is not None else container.duration / av.time_base
        return dict(video_id='clip_' + digest.hexdigest()[:16], sha256=digest.hexdigest(),
                    filename=path.name, width=video.width, height=video.height,
                    fps=float(video.average_rate), fps_rational=str(video.average_rate),
                    duration_s=duration, has_audio=bool(container.streams.audio),
                    projection='planar', playback='once', condition='unassigned',
                    rights_status='confirm_before_participant_collection')


def catalog(directory):
    return {'schema_version': 1, 'clips': [inspect_clip(p) for p in sorted(Path(directory).glob('*.mp4'))]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(catalog(args.directory), indent=2, allow_nan=False))
