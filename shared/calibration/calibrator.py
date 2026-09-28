"""
MONTA — Runtime Calibration & Decision Recording
==================================================
* ``Calibrator`` holds fitted maps and rewrites confidences on contracts
  (``IntentAnalysis`` core fields, clip emotion, story confidence), keeping the
  uncalibrated value in ``raw_confidence``.
* ``DecisionRecorder`` logs every calibratable decision so outcomes can be
  joined later (golden labels, human labels, user overrides).
* ``Calibrator.refit(store)`` rebuilds every map from labeled history.
"""

import json
from collections.abc import Iterable
from pathlib import Path

from shared.calibration.fit import CalibrationMap, fit_calibration
from shared.calibration.records import DecisionRecord, OutcomeRecord
from shared.calibration.store import CalibrationStore, group_by_field
from shared.contracts.base import revalidate
from shared.contracts.clip import ClipIntelligence
from shared.contracts.intent import IntentAnalysis

INTENT_FIELDS = ("genre", "emotion", "pace", "target_platform", "color_grade", "caption_style", "music_style")


def _value(v) -> str:
    return str(getattr(v, "value", v))


class Calibrator:
    def __init__(self, maps: Iterable[CalibrationMap] = ()):
        self.maps: dict[tuple[str, str], CalibrationMap] = {(m.component, m.field): m for m in maps}

    # ---------------------------------------------------------------- persistence
    @classmethod
    def load(cls, path: str | Path) -> "Calibrator":
        data = json.loads(Path(path).read_text())
        return cls(CalibrationMap.model_validate(m) for m in data["maps"])

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({"maps": [m.model_dump(mode="json") for m in self.maps.values()]}, indent=2))

    @classmethod
    async def refit(cls, store: CalibrationStore, version: int = 1) -> "Calibrator":
        maps = []
        for (component, field), rows in group_by_field(await store.labeled()).items():
            maps.append(fit_calibration(component, field, [r.decision.raw_confidence for r in rows],
                                        [r.outcome.correct for r in rows], [r.weight for r in rows], version=version))
        return cls(maps)

    # ---------------------------------------------------------------- application
    def apply(self, component: str, field: str, raw: float) -> float:
        m = self.maps.get((component, field))
        return m.apply(raw) if m else raw

    def calibrate_intent(self, intent: IntentAnalysis) -> IntentAnalysis:
        updates = {}
        for field in INTENT_FIELDS:
            f = getattr(intent, field)
            if f is None or ("intent", field) not in self.maps:
                continue
            raw = f.raw_confidence if f.raw_confidence is not None else f.confidence
            updates[field] = revalidate(f, confidence=self.apply("intent", field, raw), raw_confidence=raw)
        return revalidate(intent, **updates) if updates else intent

    def calibrate_clip(self, clip: ClipIntelligence) -> ClipIntelligence:
        if clip.emotion is None or ("vision", "emotion") not in self.maps:
            return clip
        return revalidate(clip, emotion_confidence=self.apply("vision", "emotion", clip.emotion_confidence))


class DecisionRecorder:
    """Writes DecisionRecords; returns their ids so callers can attach outcomes later."""

    def __init__(self, store: CalibrationStore):
        self.store = store

    async def record_intent(self, intent: IntentAnalysis, *, project_id: str | None = None) -> dict[str, str]:
        records = []
        for field in INTENT_FIELDS:
            f = getattr(intent, field)
            if f is None:
                continue
            records.append(DecisionRecord(
                component="intent", field=field, predicted_value=_value(f.value),
                raw_confidence=f.raw_confidence if f.raw_confidence is not None else f.confidence,
                calibrated_confidence=f.confidence, versions=",".join(intent.extractors), project_id=project_id,
            ))
        await self.store.add_decisions(records)
        return {r.field: r.decision_id for r in records}

    async def record_candidate(self, *, component: str, field: str, model_id: str, value: str, confidence: float,
                               project_id: str | None = None) -> str:
        """Record one model's candidate during arbitration (grades per-model reliability)."""
        rec = DecisionRecord(component=component, field=field, model_id=model_id, predicted_value=value,
                             raw_confidence=confidence, calibrated_confidence=confidence, project_id=project_id)
        await self.store.add_decisions([rec])
        return rec.decision_id

    async def record_scalar(self, *, component: str, field: str, value: str, confidence: float,
                            versions: str = "", project_id: str | None = None) -> str:
        rec = DecisionRecord(component=component, field=field, predicted_value=value, raw_confidence=confidence,
                             calibrated_confidence=confidence, versions=versions, project_id=project_id)
        await self.store.add_decisions([rec])
        return rec.decision_id

    async def record_outcome(self, decision_id: str, *, correct: bool, source: str, observed_value: str | None = None) -> None:
        await self.store.add_outcomes([OutcomeRecord(decision_id=decision_id, correct=correct, source=source,
                                                     observed_value=observed_value)])

    async def record_override(self, decision_id: str, *, predicted: str, chosen: str) -> None:
        """The creator changed a field in the UI: the prediction was wrong unless they re-picked it."""
        await self.record_outcome(decision_id, correct=predicted == chosen, source="user_override", observed_value=chosen)
