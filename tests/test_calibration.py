"""Confidence calibration — measurable, tracked, and 0.9 means ~90%."""

import random

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from services.prompt_engine import IntentEngine
from shared.calibration import (
    Calibrator,
    DecisionRecord,
    DecisionRecorder,
    InMemoryCalibrationStore,
    OutcomeRecord,
    SqlCalibrationStore,
    fit_calibration,
    reliability_report,
)
from shared.calibration.report import build_report


def overconfident(n: int, seed: int = 1):
    rng = random.Random(seed)
    raw = [rng.random() for _ in range(n)]
    return raw, [rng.random() < r ** 2 for r in raw]


def test_reliability_report_metrics():
    perfect = reliability_report([0.9] * 100, [True] * 90 + [False] * 10)
    assert perfect.ece == pytest.approx(0.0, abs=1e-9) and perfect.accuracy == 0.9
    bad = reliability_report([0.9] * 100, [True] * 50 + [False] * 50)
    assert bad.ece == pytest.approx(0.4) and bad.mce == pytest.approx(0.4)
    assert sum(bad.histogram) == 100 and bad.brier > perfect.brier


@pytest.mark.parametrize("n,method", [(3000, "isotonic"), (100, "platt"), (10, "identity")])
def test_fit_method_by_sample_size(n, method):
    raw, y = overconfident(n)
    assert fit_calibration("intent", "genre", raw, y).method == method


def test_isotonic_makes_09_mean_about_90_percent():
    raw, y = overconfident(6000)
    cmap = fit_calibration("intent", "genre", raw[:4000], y[:4000])
    test_raw, test_y = raw[4000:], y[4000:]
    before = reliability_report(test_raw, test_y)
    after = reliability_report([cmap.apply(r) for r in test_raw], test_y)
    assert before.ece > 0.1 and after.ece <= 0.05
    high = [(cmap.apply(r), t) for r, t in zip(test_raw, test_y) if 0.85 <= cmap.apply(r) <= 0.95]
    assert high and abs(sum(t for _, t in high) / len(high) - 0.9) < 0.08, "calibrated ~0.9 must be right ~90% of the time"
    assert all(cmap.apply(a) <= cmap.apply(b) for a, b in zip(sorted(test_raw), sorted(test_raw)[1:])), "monotone"


@pytest.fixture(params=["memory", "sql"])
async def store(request):
    if request.param == "memory":
        yield InMemoryCalibrationStore()
        return
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    s = SqlCalibrationStore(engine)
    await s.create_schema()
    yield s
    await engine.dispose()


async def test_store_tracks_decisions_and_latest_outcome(store):
    d = DecisionRecord(component="intent", field="pace", predicted_value="fast", raw_confidence=0.8, calibrated_confidence=0.8)
    await store.add_decisions([d])
    await store.add_outcomes([OutcomeRecord(decision_id=d.decision_id, correct=False, source="implicit_accept")])
    await store.add_outcomes([OutcomeRecord(decision_id=d.decision_id, correct=True, source="human_label")])
    rows = await store.labeled("intent", "pace")
    assert len(rows) == 1 and rows[0].outcome.correct and rows[0].weight == 1.0


async def test_recorder_calibrator_roundtrip_on_intent(tmp_path):
    store = InMemoryCalibrationStore()
    recorder = DecisionRecorder(store)
    raw, y = overconfident(400)
    for r, t in zip(raw, y):
        did = await recorder.record_scalar(component="intent", field="pace", value="fast", confidence=r)
        await recorder.record_outcome(did, correct=t, source="golden")
    calibrator = await Calibrator.refit(store)
    path = tmp_path / "cal.json"
    calibrator.save(path)
    loaded = Calibrator.load(path)
    intent = await IntentEngine(calibrator=loaded).analyze("fast gym edit")
    assert intent.pace.raw_confidence is not None
    assert intent.pace.confidence == pytest.approx(loaded.apply("intent", "pace", intent.pace.raw_confidence))
    assert intent.pace.confidence < intent.pace.raw_confidence, "overconfident history pulls confidence down"
    ids = await recorder.record_intent(intent, project_id="p")
    assert set(ids) >= {"genre", "emotion", "pace", "target_platform"}


async def test_calibration_report_holdout():
    store = InMemoryCalibrationStore()
    raw, y = overconfident(8000)
    ds = [DecisionRecord(decision_id=f"d{i:06d}", component="vision", field="emotion", predicted_value="intense",
                         raw_confidence=r, calibrated_confidence=r) for i, r in enumerate(raw)]
    await store.add_decisions(ds)
    await store.add_outcomes([OutcomeRecord(decision_id=d.decision_id, correct=t, source="golden") for d, t in zip(ds, y)])
    report = await build_report(store)
    f = report["fields"]["vision.emotion"]
    assert f["held_out"] > 0 and f["calibrated"]["ece"] < f["raw"]["ece"] and f["meets_target"]
