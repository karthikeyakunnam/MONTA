"""
MONTA — Model Reliability
===========================
Per (model, field) accuracy as a Beta posterior, learned from calibration
outcomes on arbitration candidates (``DecisionRecord.model_id`` set). A model
with no history starts at the prior mean 0.5 with weak weight (Beta(2, 2)),
so one lucky answer cannot dominate and a new model is not ignored.
"""

from collections import defaultdict

from shared.calibration.store import CalibrationStore

PRIOR_ALPHA = 2.0
PRIOR_BETA = 2.0


class ReliabilityTable:
    def __init__(self, counts: dict[tuple[str, str], tuple[float, float]] | None = None):
        self._counts: dict[tuple[str, str], tuple[float, float]] = dict(counts or {})

    def accuracy(self, model_id: str, field: str) -> float:
        correct, wrong = self._counts.get((model_id, field), (0.0, 0.0))
        return (PRIOR_ALPHA + correct) / (PRIOR_ALPHA + PRIOR_BETA + correct + wrong)

    def samples(self, model_id: str, field: str) -> float:
        return sum(self._counts.get((model_id, field), (0.0, 0.0)))

    def update(self, model_id: str, field: str, correct: bool, weight: float = 1.0) -> None:
        c, w = self._counts.get((model_id, field), (0.0, 0.0))
        self._counts[(model_id, field)] = (c + weight * correct, w + weight * (not correct))

    @classmethod
    async def from_store(cls, store: CalibrationStore, component: str) -> "ReliabilityTable":
        counts: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for row in await store.labeled(component=component):
            if row.decision.model_id is None:
                continue
            k = (row.decision.model_id, row.decision.field)
            counts[k][0 if row.outcome.correct else 1] += row.weight
        return cls({k: (v[0], v[1]) for k, v in counts.items()})
