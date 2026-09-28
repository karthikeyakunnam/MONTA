"""
MONTA — Multi-Model Arbitration
=================================
Failover asks the next model only when one fails; arbitration asks several
models and decides between their answers.

    providers ─► generate_structured (concurrently, each schema-validated)
              ─► per-field arbitration using measured reliability
              ─► merged, re-validated output + ArbitrationSummary (full rationale)

See ``arbitrate.py`` for field rules and ``reliability.py`` for how model
trust is learned from calibration outcomes.
"""

from shared.arbitration.arbitrate import INTENT_POLICY, VISION_POLICY, arbitrate_outputs, run_arbitrated
from shared.arbitration.reliability import ReliabilityTable

__all__ = ["INTENT_POLICY", "ReliabilityTable", "VISION_POLICY", "arbitrate_outputs", "run_arbitrated"]
