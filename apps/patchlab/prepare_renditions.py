"""Prepare content-addressed Patchlab media for NDI and on-device playback.

The canonical catalog remains the stimulus identity. Renditions are separate
physical files with the same decoded frame count, frame rate and aspect ratio.
Run from the SuperResolution repository with its pinned PyAV environment.
"""

from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import tempfile

import av

from .study_catalog import inspect_clip


PROFILES = (("2k", 2048, 1152), ("fhd", 1920, 1080))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def frame_count(path: Path) -> int:
    with av.open(str(path)) as container:
        return sum(1 for _ in container.decode(video=0))


def transcode(source: Path, destination: Path, width: int, height: int,
              expected_frames: int, fps: Fraction) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Never replace a verified existing rendition during an interrupted export.
    with tempfile.NamedTemporaryFile(prefix=".render-", suffix=".mp4",
                                 dir=destination.parent, delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        with av.open(str(source)) as input_media, av.open(str(staging), "w", options={"movflags": "+faststart"}) as output:
            encoder = output.add_stream("libx264", rate=fps)
            encoder.width, encoder.height = width, height
            encoder.pix_fmt = "yuv420p"
            encoder.options = {"preset": "veryfast", "crf": "18", "bf": "0"}
            count = 0
            for frame in input_media.decode(video=0):
                scaled = frame.reformat(width=width, height=height, format="yuv420p")
                scaled.pts = count
                scaled.time_base = Fraction(fps.denominator, fps.numerator)
                for packet in encoder.encode(scaled):
                    output.mux(packet)
                count += 1
            for packet in encoder.encode():
                output.mux(packet)
        if count != expected_frames or frame_count(staging) != expected_frames:
            raise ValueError(f"Frame count changed: {source.name} -> {destination.name}")
        actual = inspect_clip(staging)
        if (actual["width"], actual["height"], actual["fps_rational"]) != (width, height, str(fps)):
            raise ValueError(f"Rendition geometry/timebase mismatch: {destination}")
        staging.replace(destination)
        destination.chmod(0o644)
    finally:
        staging.unlink(missing_ok=True)


def prepare(directory: Path, catalog_path: Path, manifest_path: Path) -> dict:
    directory = directory.resolve()
    catalog = json.loads(catalog_path.read_text())
    prepared = []
    old = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    by_id = {item["video_id"]: item for item in old.get("clips", [])}
    for clip in catalog["clips"]:
        source = directory / clip["filename"]
        actual = inspect_clip(source)
        for field in ("video_id", "sha256", "width", "height", "fps_rational"):
            if actual[field] != clip[field]:
                raise ValueError(f"Canonical catalog mismatch: {source.name} / {field}")
        if actual["has_audio"]:
            raise ValueError(f"Patchlab requires silent clips: {source.name}")
        fps = Fraction(clip["fps_rational"])
        frames = frame_count(source)
        renditions = [dict(id="native", filename=source.name, sha256=clip["sha256"],
                           width=clip["width"], height=clip["height"],
                           fps_rational=clip["fps_rational"], frame_count=frames)]
        previous = {r["id"]: r for r in by_id.get(clip["video_id"], {}).get("renditions", [])}
        for label, width, height in PROFILES:
            if width > clip["width"] or height > clip["height"]:
                continue  # Never advertise an upscaled stimulus as a resolution upgrade.
            relative = f"_renditions/{clip['video_id']}/{label}.mp4"
            target = directory / relative
            old_rendition = previous.get(label, {})
            reusable = (target.is_file() and old_rendition.get("source_sha256") == clip["sha256"]
                        and old_rendition.get("sha256") == sha256(target)
                        and old_rendition.get("frame_count") == frames)
            if not reusable:
                transcode(source, target, width, height, frames, fps)
            # NamedTemporaryFile starts at 0600, and File.Copy preserves the
            # mode in the Xcode export. App-bundle media must be world-readable.
            target.chmod(0o644)
            metadata = inspect_clip(target)
            if (metadata["width"], metadata["height"], metadata["fps_rational"], frame_count(target)) != (width, height, str(fps), frames):
                raise ValueError(f"Invalid rendition: {target}")
            renditions.append(dict(id=label, filename=relative, sha256=metadata["sha256"],
                                   source_sha256=clip["sha256"], width=width, height=height,
                                   fps_rational=str(fps), frame_count=frames))
        prepared.append(dict(video_id=clip["video_id"], source_sha256=clip["sha256"], renditions=renditions))
    result = dict(schema_version=1, clips=prepared)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parents[2] / "media/patchlab/clips")
    parser.add_argument("--catalog", type=Path, default=Path(__file__).resolve().parents[3] / "ACCESS_VisionOS_Metal/STUDY_CONTRACTS/catalog.json")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parents[3] / "ACCESS_VisionOS_Metal/STUDY_CONTRACTS/renditions.json")
    args = parser.parse_args()
    result = prepare(args.directory, args.catalog, args.manifest)
    print(f"Prepared {len(result['clips'])} canonical clips and {sum(len(c['renditions']) for c in result['clips'])} physical renditions")


if __name__ == "__main__":
    main()
