"""Verify immutable delivery files and every decoded CFR presentation timestamp."""
import argparse
import json
from fractions import Fraction
from pathlib import Path
import av
from .prepare_renditions import decode_options
from .study_catalog import inspect_clip

def verify(catalog: Path, renditions: Path, directory: Path):
    clips=json.loads(catalog.read_text())["clips"]
    entries={row["video_id"]:row for row in json.loads(renditions.read_text())["clips"]}
    results=[]
    for clip in clips:
        entry=entries[clip["video_id"]]
        if entry["source_sha256"]!=clip["sha256"]:raise ValueError("Canonical identity changed")
        for rendition in entry["renditions"]:
            file=directory/rendition["filename"]
            actual=inspect_clip(file)
            for key in ("sha256","width","height","fps_rational"):
                if actual[key]!=rendition[key]:raise ValueError(f"{file}: incorrect {key}")
            fps=Fraction(rendition["fps_rational"]);count=0
            with av.open(str(file)) as probe:
                codec=probe.streams.video[0].codec_context
                if (codec.name,codec.profile,codec.format.name,codec.codec_tag)!=("hevc","Main","yuv420p","hvc1") or probe.streams.audio:
                    raise ValueError(f"{file}: incompatible delivery codec or audio")
            with av.open(str(file),**decode_options(file)) as media:
                for frame in media.decode(video=0):
                    if frame.pts is None or frame.pts*frame.time_base!=count/fps:
                        raise ValueError(f"{file}: non-CFR or reordered PTS at frame {count}")
                    count+=1
            if count!=rendition["frame_count"]:raise ValueError(f"{file}: missing decoded frames")
            result={"video_id":clip["video_id"],"resolution":rendition["id"],"frame_count":count,
                    "sha256":actual["sha256"],"width":actual["width"],"height":actual["height"],"fps_rational":actual["fps_rational"],"codec":"HEVC Main 8-bit 4:2:0 hvc1","all_pts_verified":True}
            results.append(result);print(json.dumps(result),flush=True)
    return results

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog",type=Path,required=True);parser.add_argument("--renditions",type=Path,required=True)
    parser.add_argument("--directory",type=Path,required=True)
    args=parser.parse_args();verify(args.catalog,args.renditions,args.directory)
