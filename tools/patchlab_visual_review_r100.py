"""Reproducible visual candidates only; never rewrites the active study library."""
from pathlib import Path
from fractions import Fraction
import json
import sys
import av
import numpy as np
from apps.patchlab import calibration_clip as calibration


def png(file, image):
    with av.open(str(file), "w", format="image2") as output:
        stream = output.add_stream("png", rate=1)
        stream.width, stream.height = image.shape[1], image.shape[0]
        stream.pix_fmt = "rgb24"
        for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")):
            output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


def main():
    destination = Path(sys.argv[1])
    destination.mkdir(parents=True, exist_ok=True)
    path = calibration.target_path(Fraction(30))
    record = path[0]
    report = {"active_catalog_changed": False, "trajectory_identical": True,
              "candidate_only": True, "artistic_approval": "pending", "clips": {}}
    for version in ("legacy", "smooth-v2"):
        image = calibration.render(7680, 4320, record["uv"], calibration.TARGET_DIAMETER,
                                   record["target_scale"], calibration.pulse(0), version)
        png(destination / f"calibration-{version}-8k-frame0.png", image)
        # Native-resolution crop: makes quantization visible without display rescaling.
        png(destination / f"calibration-{version}-8k-crop.png", image[300:1000, 1500:2600])
        output = destination / f"calibration-{version}-1080-review.mp4"
        metadata = calibration.generate(output, 1920, 1080, Fraction(30),
                                        bitrate_mbps=40, encoder="hevc_videotoolbox",
                                        visual_version=version,
                                        progress=lambda done, total: print(version, done, total, flush=True))
        report["clips"][version] = {key: metadata[key] for key in
                                   ("sha256", "frame_count", "encoding", "visual_version")}
    flare = Path(sys.argv[2])
    with av.open(str(flare)) as video:
        frame = next(video.decode(video=0))
        png(destination / "flare-first-frame-reference.png", frame.to_ndarray(format="rgb24"))
    (destination / "visual-candidates.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
