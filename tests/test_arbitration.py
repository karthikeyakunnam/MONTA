"""Multi-model arbitration — compare, score, select, record rationale."""

import json

import pytest

from orchestration.agents.intelligence import VideoIntelligenceTeam, VisionAnalyzer
from services.prompt_engine import IntentEngine
from shared.arbitration import INTENT_POLICY, VISION_POLICY, ReliabilityTable, arbitrate_outputs, run_arbitrated
from shared.arbitration.arbitrate import VISION_SELF_CONFIDENCE
from shared.calibration import DecisionRecord, InMemoryCalibrationStore, OutcomeRecord
from shared.contracts.clip import ClipSource, VisionObservation
from shared.contracts.vocab import Pace, ShotType
from shared.providers.base import Message
from shared.providers.errors import ProviderUnavailableError, StructuredOutputError
from tests.conftest import ScriptedProvider, SyntheticMediaBackend


def obs(**kw) -> str:
    base = {"activities": ["deadlift"], "objects": ["barbell"], "camera_type": "phone", "shot_type": "medium",
            "scene_type": "gym", "emotion": "intense", "emotion_confidence": 0.8, "energy_estimate": 8,
            "story_role_candidates": [{"role": "peak", "confidence": 0.8}], "reasoning": "x"}
    return json.dumps({**base, **kw})


def trusted(model_a: str, model_b: str, field: str) -> ReliabilityTable:
    t = ReliabilityTable()
    for _ in range(40):
        t.update(model_a, field, True)
        t.update(model_b, field, False)
    return t


async def test_reliability_overrules_self_confidence():
    qwen = ScriptedProvider([obs(shot_type="close_up")], name="qwen_vl")
    gem = ScriptedProvider([obs()], name="gemini")
    value, summary = await run_arbitrated([qwen, gem], [Message.user("x")], VisionObservation, VISION_POLICY,
                                          trusted("gemini:test-model", "qwen_vl:test-model", "shot_type"),
                                          self_confidence_fields=VISION_SELF_CONFIDENCE)
    assert value.shot_type == ShotType.MEDIUM
    rec = next(r for r in summary.records if r.field == "shot_type")
    assert rec.rule == "weighted_vote" and rec.winner == "medium" and rec.rationale
    assert {c.model_id for c in rec.candidates} == {"qwen_vl:test-model", "gemini:test-model"}
    assert summary.models_succeeded == ("qwen_vl:test-model", "gemini:test-model")


def test_field_rules():
    a = VisionObservation.model_validate(json.loads(obs(energy_estimate=6, activities=["deadlift", "chalk"])))
    b = VisionObservation.model_validate(json.loads(obs(energy_estimate=8, activities=["deadlift"])))
    c = VisionObservation.model_validate(json.loads(obs(energy_estimate=10, activities=["deadlift", "shout"])))
    merged, records = arbitrate_outputs({"m1": a, "m2": b, "m3": c}, VisionObservation, VISION_POLICY, ReliabilityTable())
    assert merged.energy_estimate == pytest.approx(8.0, abs=0.01)
    assert merged.activities == ("deadlift",), "minority items dropped"
    assert next(r for r in records if r.field == "shot_type").rule == "unanimous"


def test_tie_goes_to_most_reliable_model():
    a = VisionObservation.model_validate(json.loads(obs(shot_type="wide")))
    b = VisionObservation.model_validate(json.loads(obs(shot_type="close_up")))
    table = ReliabilityTable()
    for _ in range(3):
        table.update("b", "shot_type", True)
    merged, records = arbitrate_outputs({"a": a, "b": b}, VisionObservation, VISION_POLICY, table)
    rec = next(r for r in records if r.field == "shot_type")
    assert merged.shot_type == ShotType.CLOSE_UP and rec.rule in ("reliability_tiebreak", "weighted_vote")


async def test_single_survivor_and_total_failure():
    ok = ScriptedProvider([obs()], name="gemini")
    down = ScriptedProvider([ProviderUnavailableError("x")], name="qwen_vl")
    value, summary = await run_arbitrated([down, ok], [Message.user("x")], VisionObservation, VISION_POLICY, ReliabilityTable())
    assert summary.models_succeeded == ("gemini:test-model",) and summary.failures
    with pytest.raises(StructuredOutputError):
        await run_arbitrated([ScriptedProvider([ProviderUnavailableError("a")], name="a"),
                              ScriptedProvider([ProviderUnavailableError("b")], name="b")],
                             [Message.user("x")], VisionObservation, VISION_POLICY, ReliabilityTable())


async def test_reliability_learned_from_calibration_outcomes():
    store = InMemoryCalibrationStore()
    ds, os_ = [], []
    for i in range(20):
        for model, right in (("qwen:m", i % 2 == 0), ("gemini:m", True)):
            d = DecisionRecord(component="intent.llm", field="pace", model_id=model, predicted_value="fast",
                               raw_confidence=0.8, calibrated_confidence=0.8)
            ds.append(d)
            os_.append(OutcomeRecord(decision_id=d.decision_id, correct=right, source="golden"))
    await store.add_decisions(ds)
    await store.add_outcomes(os_)
    table = await ReliabilityTable.from_store(store, "intent.llm")
    assert table.accuracy("gemini:m", "pace") > 0.9 > table.accuracy("qwen:m", "pace") > 0.4
    assert table.accuracy("new:model", "pace") == 0.5


async def test_intent_engine_arbitrates_between_llms_and_records_rationale():
    def llm(pace, conf):
        return json.dumps({"pace": {"value": pace, "confidence": conf, "reasoning": f"says {pace}"}})
    qwen = ScriptedProvider([llm("fast", 0.9)], name="qwen", vision=False)
    gem = ScriptedProvider([llm("slow", 0.9)], name="gemini", vision=False)
    engine = IntentEngine([qwen, gem], reliability=trusted("gemini:test-model", "qwen:test-model", "pace"))
    intent = await engine.analyze("make an edit")
    assert intent.pace.value == Pace.SLOW
    assert intent.arbitration is not None and intent.arbitration.models_succeeded == ("qwen:test-model", "gemini:test-model")
    assert any(r.field == "pace" for r in intent.arbitration.records)


async def test_vision_team_records_arbitration_in_provenance():
    media = SyntheticMediaBackend({"a": "action"})
    team = VideoIntelligenceTeam(media, vision=VisionAnalyzer([ScriptedProvider([obs()], name="qwen_vl"),
                                                              ScriptedProvider([obs(scene_type="stadium")], name="gemini")]))
    ci = await team.analyze(await media.probe(ClipSource(clip_id="a", path="/a.mp4")))
    assert ci.provenance.arbitration is not None and ci.provenance.vision_model.startswith("arbitrated:")
    assert any(r.field == "scene_type" for r in ci.provenance.arbitration.records)


def test_intent_policy_covers_all_llm_fields():
    from services.prompt_engine.llm_extractor import LLMIntent

    assert set(LLMIntent.model_fields) <= set(INTENT_POLICY)
