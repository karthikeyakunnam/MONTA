"""
MONTA — Layer 6 Evaluation CLI
================================
    python -m evaluation.video_intelligence.run --manifest data/manifest.jsonl --out l6_report.json [--consistency]

Uses the production FFmpeg backend and, when configured, the environment's
vision providers (MONTA_VISION_PROVIDERS / MONTA_ARBITRATION).
"""

import argparse
import asyncio
from pathlib import Path

from evaluation.video_intelligence.evaluate import evaluate, summarize
from evaluation.video_intelligence.manifest import load_manifest
from orchestration.agents.intelligence import FFmpegMediaBackend, VideoIntelligenceTeam, VisionAnalyzer
from shared.providers.registry import build_providers


async def _main(args) -> None:
    providers = build_providers() if args.with_vision else None
    vision_src = providers.vision_for_analysis if providers else None
    media = FFmpegMediaBackend(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
    team = VideoIntelligenceTeam(media, vision=VisionAnalyzer(vision_src) if vision_src else None)
    report = await evaluate(load_manifest(args.manifest), team, consistency=args.consistency, ffmpeg=args.ffmpeg)
    Path(args.out).write_text(report.model_dump_json(indent=2))
    print(summarize(report))
    if providers:
        await providers.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", default="l6_report.json")
    ap.add_argument("--consistency", action="store_true")
    ap.add_argument("--with-vision", action="store_true")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    asyncio.run(_main(ap.parse_args()))
