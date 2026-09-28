"""Story Judge — independent of the architect, discriminative, explainable, self-preference guarded."""

import ast
import json
from pathlib import Path

import pytest

from evaluation.story_judge import CompositeStoryJudge, HeuristicStoryJudge, LLMStoryJudge
from orchestration.agents.story import StoryArchitect
from shared.contracts.evaluation import JUDGE_DIMENSIONS
from shared.providers.errors import ProviderUnavailableError
from tests.conftest import ScriptedProvider
from tests.test_story import make_pack

ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> list[str]:
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append(node.module or "")
    return out


def test_judge_is_independent_of_the_story_architect():
    # The judge package may not import the architect at all.
    for path in (ROOT / "evaluation/story_judge").rglob("*.py"):
        bad = [n for n in _imports(path) if n.startswith("orchestration.agents.story")]
        assert not bad, f"{path.relative_to(ROOT)} imports {bad}"
    # Evaluation runners may *run* the architect as the system under test, but never score with its internals.
    for path in (ROOT / "evaluation").rglob("*.py"):
        bad = [n for n in _imports(path) if n in ("orchestration.agents.story.scoring", "orchestration.agents.story.assembly",
                                                   "orchestration.agents.story.selection")]
        assert not bad, f"{path.relative_to(ROOT)} imports architect internals {bad}"


async def test_judgement_schema_and_rationale():
    pack = make_pack()
    plan = await StoryArchitect().design(pack)
    j = await HeuristicStoryJudge().judge(plan, pack)
    for dim in JUDGE_DIMENSIONS:
        assert 0 <= getattr(j, f"{dim}_score") <= 10 and j.rationale[dim].rationale
    assert 0 <= j.overall_score <= 10 and 0 < j.confidence <= 1 and j.valid_plan == plan.validation.passed


async def test_judge_ranks_on_brief_story_above_off_brief_one():
    pack = make_pack()
    architect = StoryArchitect()
    on_brief = await architect.design(pack)
    off_brief = await architect.design(pack, avoid_patterns=["motivational_reel", "transformation", "fitness_reel"])
    judge = HeuristicStoryJudge()
    good, bad = await judge.judge(on_brief, pack), await judge.judge(off_brief, pack)
    assert good.overall_score > bad.overall_score + 0.5
    assert good.rank_key > bad.rank_key


def _llm_scores(score: float) -> str:
    return json.dumps({**{d: {"score": score, "rationale": f"cut 1 {d}"} for d in JUDGE_DIMENSIONS}, "confidence": 0.7})


async def test_llm_judge_cannot_judge_its_own_model():
    provider = ScriptedProvider([], vision=False)
    with pytest.raises(ValueError, match="may not judge its own output"):
        LLMStoryJudge(provider, forbidden_model_ids=[provider.model_id])


async def test_llm_and_composite_judges():
    pack = make_pack()
    plan = await StoryArchitect().design(pack)
    llm = LLMStoryJudge(ScriptedProvider([_llm_scores(6.0)], vision=False))
    j = await llm.judge(plan, pack)
    assert j.overall_score == pytest.approx(6.0) and j.judge_id.startswith("llm:")
    heuristic = await HeuristicStoryJudge().judge(plan, pack)
    composite = CompositeStoryJudge([(HeuristicStoryJudge(), 0.5), (LLMStoryJudge(ScriptedProvider([_llm_scores(6.0)], vision=False)), 0.5)])
    c = await composite.judge(plan, pack)
    assert c.overall_score == pytest.approx((heuristic.overall_score + 6.0) / 2, abs=0.01)
    assert len(c.components) == 2 and c.confidence <= max(heuristic.confidence, 0.7)


async def test_composite_survives_a_failing_llm_judge():
    pack = make_pack()
    plan = await StoryArchitect().design(pack)
    failing = LLMStoryJudge(ScriptedProvider([ProviderUnavailableError("down")], vision=False))
    c = await CompositeStoryJudge([(HeuristicStoryJudge(), 0.5), (failing, 0.5)]).judge(plan, pack)
    assert c.components == (HeuristicStoryJudge.judge_id,)
