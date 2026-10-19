"""Create an immutable HEVC delivery copy; never replace the study original."""
import argparse
from fractions import Fraction
from pathlib import Path
import tempfile
import json
import sys
import av
from .study_catalog import inspect_clip
from .calibration_clip import frame_count


def transcode(source: Path, output: Path, bitrate_mbps: float = 60) -> dict:
    if output.exists():
        raise FileExistsError("Choose a new delivery filename; historical files are immutable")
    original = inspect_clip(source)
    if original["has_audio"]:
        raise ValueError("This study conversion is for silent clips only")
    if bitrate_mbps <= 0 or not bitrate_mbps < float("inf"):
        raise ValueError("Positive finite bitrate required")
    fps = Fraction(original["fps_rational"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".hevc-",suffix=".mp4",dir=output.parent,delete=False) as tmp:
        staging = Path(tmp.name)
    hardware = sys.platform == "darwin"
    kwargs = {}
    if hardware:
        from av.codec.hwaccel import HWAccel
        kwargs["hwaccel"] = HWAccel("videotoolbox",allow_software_fallback=False)
    count = 0
    try:
        with av.open(str(source),**kwargs) as media, av.open(str(staging),"w",options={"movflags":"+faststart"}) as result:
            encoder = result.add_stream("hevc_videotoolbox" if hardware else "libx265",rate=fps)
            encoder.width,encoder.height = original["width"],original["height"]
            encoder.pix_fmt = "nv12" if hardware else "yuv420p"
            encoder.bit_rate = round(bitrate_mbps*1_000_000)
            encoder.codec_context.codec_tag = "hvc1"
            encoder.options = {"constant_bit_rate":"1","realtime":"0","allow_sw":"0","profile":"main","bf":"0"} if hardware else {"preset":"fast","profile":"main","x265-params":"bframes=0:pools=4"}
            for frame in media.decode(video=0):
                if frame.pts*frame.time_base != count/fps:
                    raise ValueError("Source PTS is not the expected CFR timeline; conversion refused")
                frame = frame.reformat(format=encoder.pix_fmt)
                frame.pts,frame.time_base = count,1/fps
                for packet in encoder.encode(frame):result.mux(packet)
                count += 1
                if count%150==0:print(f"Encoded {count}",file=sys.stderr,flush=True)
            for packet in encoder.encode():result.mux(packet)
        if frame_count(staging,fps,hardware=hardware)!=count:
            raise ValueError("Decoded frame count changed")
        with av.open(str(staging)) as check:
            context=check.streams.video[0].codec_context
            if (context.name,context.profile,context.format.name,context.codec_tag)!=("hevc","Main","yuv420p","hvc1"):
                raise ValueError("Wrong HEVC delivery format")
        staging.replace(output)
    finally:
        staging.unlink(missing_ok=True)
    delivery=inspect_clip(output)
    if any(delivery[key]!=original[key] for key in ("width","height","fps_rational","duration_s")):
        raise ValueError("Delivery geometry or duration changed")
    return {**delivery,"source_sha256":original["sha256"],"frame_count":count,
            "codec":"hevc","codec_tag":"hvc1","requested_bitrate_bps":round(bitrate_mbps*1_000_000)}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source",type=Path);parser.add_argument("output",type=Path)
    parser.add_argument("--bitrate-mbps",type=float,default=60)
    args=parser.parse_args()
    print(json.dumps(transcode(args.source,args.output,args.bitrate_mbps),indent=2))
