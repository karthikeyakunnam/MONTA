"""
MONTA — Golden Project Comparison
===================================
Runs Layers 3→7 on each golden project and grades the output against the
expert expectations. See ``compare.py``.
"""

from evaluation.golden.compare import GoldenCheck, GoldenReport, GoldenResult, LayerStack, run_golden, run_golden_project

__all__ = ["GoldenCheck", "GoldenReport", "GoldenResult", "LayerStack", "run_golden", "run_golden_project"]
