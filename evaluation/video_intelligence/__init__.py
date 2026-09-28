"""
MONTA — Layer 6 Video Intelligence Evaluation
===============================================
Measures ``VideoIntelligenceTeam`` on real (or generated) video with ground
truth labels:

* activity recall / precision / hit-rate (token-boundary matching)
* emotion, scene, shot type, camera motion, lighting accuracy + confusion matrices
* story-role top-1 / top-3 accuracy
* quality & energy rank correlation (Spearman) vs human 0–10 labels
* quality consistency under re-encoding (test–retest)
* failure analysis: crashes, vision degradation, worst errors

Entry points: ``evaluate`` (library), ``python -m evaluation.video_intelligence.run`` (CLI),
``generate_synthetic`` (labeled-by-construction smoke dataset via FFmpeg).
"""

from evaluation.video_intelligence.evaluate import VideoEvalReport, evaluate
from evaluation.video_intelligence.manifest import ClipLabels, ManifestEntry, load_manifest

__all__ = ["ClipLabels", "ManifestEntry", "VideoEvalReport", "evaluate", "load_manifest"]
