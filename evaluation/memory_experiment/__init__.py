"""
MONTA — Narrative Memory Experiment
=====================================
Memory stays on only if it measurably helps.

* ``MemoryExperiment`` — deterministic per-user arm assignment (memory / control).
  The ContextComposer gives control-arm users neutral pattern priors, no history
  and no cross-creator successes; explicit settings are still honoured.
* ``offline`` — paired comparison on golden projects: the same project designed
  with and without a memory store, scored by the independent Story Judge.
* ``online`` — outcome analysis of real published stories by arm: user
  satisfaction (rating), completion, engagement, with bootstrap confidence
  intervals and a permutation test. ``decide`` turns that into keep / disable /
  keep-collecting.
"""

from evaluation.memory_experiment.assignment import MemoryExperiment
from evaluation.memory_experiment.offline import OfflineMemoryReport, run_offline
from evaluation.memory_experiment.online import OnlineMemoryReport, analyze_online, decide

__all__ = ["MemoryExperiment", "OfflineMemoryReport", "OnlineMemoryReport", "analyze_online", "decide", "run_offline"]
