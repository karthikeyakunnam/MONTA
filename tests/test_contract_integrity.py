"""Critical bug #4 — contract bypass. Invalid values can never enter a contract."""

import copy
import pickle
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
from shared.contracts.base import FrozenDict, revalidate
from shared.contracts.explain import Explained
from shared.contracts.vocab import Pace
from tests.conftest import gym_clips, make_clip
from tests.test_story import make_pack

ROOT = Path(__file__).resolve().parents[1]
GUARDED = ["services/prompt_engine", "services/context_composer", "orchestration", "memory", "shared", "evaluation", "benchmarks"]


def test_no_model_copy_update_in_layers_3_to_7():
    offenders = []
    for d in GUARDED:
        for path in (ROOT / d).rglob("*.py"):
            text = path.read_text()
            for m in re.finditer(r"\.model_copy\(\s*update\s*=", text):
                line = text[: m.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line}")
    allowed = {"evaluation/video_intelligence/manifest.py"}  # path rewrite of a plain str field on a loader-local model
    offenders = [o for o in offenders if o.split(":")[0] not in allowed]
    assert not offenders, f"use shared.contracts.base.revalidate instead of model_copy(update=...): {offenders}"


def test_revalidate_rejects_invalid_enum_and_confidence():
    intent = LexicalIntentExtractor().extract("gym")
    with pytest.raises(ValidationError):
        revalidate(intent, overall_confidence=7.5)
    with pytest.raises(ValidationError):
        revalidate(intent, genre="not-a-genre")
    with pytest.raises(ValidationError):
        revalidate(intent.pace, confidence=1.3)
    with pytest.raises(ValueError, match="no fields"):
        revalidate(intent, unknown_field=1)
    ok = revalidate(intent, overall_confidence=0.4)
    assert ok.overall_confidence == 0.4 and intent.overall_confidence != 0.4 or ok is not intent


def test_explained_bounds_and_text_limits():
    with pytest.raises(ValidationError):
        Explained[Pace](value="warp", confidence=0.5, reasoning="x")
    with pytest.raises(ValidationError):
        Explained[Pace](value="slow", confidence=0.5, reasoning="x" * 5000)
    with pytest.raises(ValidationError):
        Explained[Pace](value="slow", confidence=0.5, reasoning="")
    cleaned = Explained[Pace](value="slow", confidence=0.5, reasoning="ok\x00\x07 fine")
    assert cleaned.reasoning == "ok fine"


def test_contract_mappings_are_immutable_but_serializable():
    pack = make_pack()
    assert isinstance(pack.clip_intelligence, FrozenDict)
    with pytest.raises(TypeError):
        pack.clip_intelligence["x"] = make_clip("x", 5)
    with pytest.raises(TypeError):
        pack.clip_intelligence.clear()
    # Graph state is persisted as JSON (pydantic cannot pickle parametrized generics such as Explained[Genre]);
    # the frozen mapping itself must still pickle and deep-copy for executors that need it.
    frozen = pickle.loads(pickle.dumps(pack.clip_intelligence))
    assert isinstance(frozen, FrozenDict) and frozen.keys() == pack.clip_intelligence.keys()
    assert copy.deepcopy(pack).clip_intelligence == pack.clip_intelligence
    again = type(pack).model_validate_json(pack.model_dump_json())
    assert set(again.clip_intelligence) == {c.clip_id for c in gym_clips()}


async def test_story_plan_assignments_are_immutable():
    from orchestration.agents.story import StoryArchitect

    plan = await StoryArchitect().design(make_pack())
    with pytest.raises(TypeError):
        plan.act_assignments["new_act"] = ("c1",)
