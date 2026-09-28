"""
MONTA — Confidence Calibration
================================
Makes "0.9 confidence" mean "right about 90% of the time".

    decision emitted ─► DecisionRecord (component, field, value, raw confidence, versions)
    truth arrives    ─► OutcomeRecord  (golden label, human label, user override, implicit accept)
    fit              ─► CalibrationMap per (component, field)  — isotonic, Platt, or identity
    runtime          ─► Calibrator.apply(raw) → calibrated; raw kept in Explained.raw_confidence
    report           ─► reliability curve, ECE / MCE / Brier, confidence histogram

Modules: ``records`` (schemas), ``store`` (in-memory + SQL), ``metrics``
(reliability statistics), ``fit`` (isotonic/Platt), ``calibrator`` (runtime
application + recorder), ``report`` (JSON report / CLI).
"""

from shared.calibration.calibrator import Calibrator, DecisionRecorder
from shared.calibration.fit import CalibrationMap, fit_calibration
from shared.calibration.metrics import reliability_report
from shared.calibration.records import DecisionRecord, OutcomeRecord
from shared.calibration.store import InMemoryCalibrationStore, SqlCalibrationStore

__all__ = [
    "CalibrationMap", "Calibrator", "DecisionRecord", "DecisionRecorder", "InMemoryCalibrationStore",
    "OutcomeRecord", "SqlCalibrationStore", "fit_calibration", "reliability_report",
]
