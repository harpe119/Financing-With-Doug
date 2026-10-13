"""Render and package the visual forecast explainer, without captions.

Usage from the repository root:
    python scripts/video/render.py --preview
    python scripts/video/render.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build" / "video"


def render(preview=False, video="monte_carlo"):
    progress = video == "progress"
    scene = "ProgressExplainer" if progress else "MonteCarloExplainer"
    timeline_name = "progress_timeline.json" if progress else "timeline.json"
    BUILD.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, "-m", "manim", "--renderer", "cairo",
        "--resolution", "960,540" if preview else "1920,1080",
        "--fps", "15" if preview else "30", "--disable_caching",
        "--media_dir", str(BUILD / "media"), "-o", f"{video}_render",
        str(Path(__file__).with_name(f"{video}.py")), scene,
    ], check=True, cwd=ROOT)
    timeline = json.loads((BUILD / timeline_name).read_text(encoding="utf-8"))
    chapters = [";FFMETADATA1", "title=How Doug measures progress" if progress else "title=How Doug builds your financial forecast",
                "comment=Fictional data calculated with the Doug forecast engine. Silent visual explanation, without captions."]
    for i, chapter in enumerate(timeline["chapters"]):
        end = (timeline["chapters"][i + 1]["start"]
               if i + 1 < len(timeline["chapters"]) else timeline["duration"])
        chapters.extend(["[CHAPTER]", "TIMEBASE=1/1000",
                         f"START={round(chapter['start'] * 1000)}",
                         f"END={round(end * 1000)}", f"title={chapter['title']}"])
    metadata = BUILD / f"{video}_chapters.ffmetadata"
    metadata.write_text("\n".join(chapters) + "\n", encoding="utf-8")
    matches = list((BUILD / "media" / "videos").rglob(f"{video}_render.mp4"))
    # There may be both preview and final renders. Select by expected size.
    quality_folder = "540p15" if preview else "1080p30"
    source = next(p for p in matches if p.parent.name == quality_folder)
    target = (BUILD / f"{video}_preview.mp4" if preview
              else ROOT / "assets" / f"{video}.mp4")
    temporary = target.with_name(target.stem + ".new.mp4")
    subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "warning",
        "-i", str(source), "-i", str(metadata),
        "-map", "0:v:0", "-map_metadata", "1", "-map_chapters", "1",
        "-c:v", "copy", "-an", "-sn",
        "-movflags", "+faststart", str(temporary),
    ], check=True)
    temporary.replace(target)
    if not preview:
        # A same-name sidecar can be loaded automatically by video players.
        (ROOT / "assets" / f"{video}.srt").unlink(missing_ok=True)
    print(f"Rendered {timeline['duration']:.1f} seconds: {target}")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="Render a small review copy in build/video.")
    parser.add_argument("--video", choices=("monte_carlo", "progress"), default="monte_carlo")
    args = parser.parse_args()
    render(args.preview, args.video)
