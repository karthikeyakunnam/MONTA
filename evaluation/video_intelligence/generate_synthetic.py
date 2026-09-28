"""
MONTA — Synthetic Labeled Video Set
=====================================
Generates clips whose *measurable* properties are true by construction, so the
signal half of Layer 6 (lighting, camera motion, quality ordering) can be
evaluated with real FFmpeg decoding and no human labelling:

* lighting: normal / dark (brightness −0.35) / overexposed (brightness +0.45)
* camera motion: static / pan (moving crop of a large still) / shaky (sinusoidal jitter)
* quality: sharp vs heavily blurred (gblur σ=10) — labelled 8 vs 3

Semantic labels (activities, emotion, roles) cannot be synthesized honestly;
they require the human-labelled manifest described in ``manifest.py``.

    python -m evaluation.video_intelligence.generate_synthetic --out /tmp/monta_video_eval
"""

import argparse
import json
import subprocess
from pathlib import Path

SRC = "testsrc2=size=1280x720:rate=30"
BIG = "testsrc2=size=2560x720:rate=30"

CASES = [
    # clip_id, lavfi source, filter, labels
    ("static_good", SRC, "null", {"lighting": "good", "camera_motion": "static", "quality": 8.0}),
    ("static_dark", SRC, "eq=brightness=-0.35", {"lighting": "low", "camera_motion": "static"}),
    ("static_bright", SRC, "eq=brightness=0.45:contrast=0.6", {"lighting": "overexposed", "camera_motion": "static"}),
    ("static_blur", SRC, "gblur=sigma=10", {"camera_motion": "static", "quality": 3.0}),
    ("pan_right", BIG, "crop=1280:720:'min(1280,t*300)':0", {"lighting": "good", "camera_motion": "pan", "quality": 8.0}),
    ("shaky", BIG, "crop=1280:720:'600+40*sin(9*t)+25*sin(23*t)':0", {"lighting": "good", "camera_motion": "shaky"}),
]


def generate(out_dir: Path, ffmpeg: str = "ffmpeg", seconds: int = 4) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "manifest.jsonl"
    with manifest.open("w") as f:
        for clip_id, src, vf, labels in CASES:
            path = out_dir / f"{clip_id}.mp4"
            # testsrc2 carries a moving element; freeze it for static cases so "static" really means no motion.
            source = f"{src},trim=end_frame=1,loop=loop=-1:size=1:start=0" if clip_id.startswith("static") else src
            subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", source, "-t", str(seconds), "-vf", vf,
                            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "18", str(path)], check=True)
            f.write(json.dumps({"clip_id": clip_id, "path": path.name, "labels": labels, "source": "synthetic"}) + "\n")
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    args = ap.parse_args()
    print(generate(Path(args.out), args.ffmpeg))
