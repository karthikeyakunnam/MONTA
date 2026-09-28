"""End-to-end Layers 3–7 through the pipeline and the master LangGraph."""

import json

import pytest

from memory.narrative_memory import InMemoryNarrativeStore, InMemoryPreferenceStore, NarrativeLearner
from orchestration.agents.intelligence import LRUIntelligenceCache, VideoIntelligenceTeam, VisionAnalyzer
from orchestration.agents.story import DEFAULT_LIBRARY, StoryArchitect, TimelineValidator
from orchestration.graphs.master_graph import build_master_graph
from orchestration.pipeline import MontaPipeline
from services.context_composer import ContextComposer
from services.prompt_engine import IntentEngine
from shared.contracts.clip import ClipSource
from shared.contracts.director import TaskStatus
from shared.contracts.evaluation import StoryAttempt, StoryJudgement
from shared.contracts.story import StoryPlan
from shared.contracts.vocab import Genre
from shared.exceptions import PipelineError
from tests.conftest import ScriptedProvider, SyntheticMediaBackend

NIKE = "make this feel like nike ad slow start then huge motivation ending use dark colors and aggressive cuts"


def vision_json(activity: str, emotion: str, energy: float, role: str) -> str:
    return json.dumps({
        "activities": [activity], "objects": ["barbell"], "people": ["athlete"], "camera_type": "phone", "shot_type": "medium",
        "scene_type": "gym", "emotion": emotion, "emotion_confidence": 0.8, "energy_estimate": energy,
        "story_role_candidates": [{"role": role, "confidence": 0.85}], "visual_tags": ["gym"], "quality_issues": [],
        "reasoning": f"{activity} in a gym",
    })


GYM = {
    "c1": ("static", vision_json("sitting on bench in gym", "dramatic", 2, "struggle")),
    "c2": ("pan", vision_json("chalking hands", "intense", 4.5, "preparation")),
    "c3": ("pan", vision_json("squat", "motivational", 6, "progress")),
    "c4": ("action", vision_json("deadlift", "intense", 8, "intensity")),
    "c5": ("action", vision_json("deadlift lockout", "aggressive", 9, "peak")),
    "c6": ("static", vision_json("flexing in mirror", "inspiring", 7.5, "hero")),
}


class ByClipVision(ScriptedProvider):
    """Answers vision requests based on the clip_id in the prompt, so concurrency order doesn't matter."""

    def __init__(self, answers: dict[str, str]):
        super().__init__([])
        self.answers = answers

    async def generate(self, messages, config=None):
        self.requests.append(messages)
        text = messages[-1].text
        clip_id = next(cid for cid in self.answers if f'"clip_id": "{cid}"' in text)
        from shared.providers.base import ModelResponse
        return ModelResponse(text=self.answers[clip_id], provider=self.name, model=self.model)


def build(media, *, vision=True, memory=None) -> MontaPipeline:
    memory = memory or InMemoryNarrativeStore()
    validator = TimelineValidator()
    provider = ByClipVision({cid: v for cid, (_, v) in GYM.items()}) if vision else None
    return MontaPipeline(
        intent_engine=IntentEngine(),
        composer=ContextComposer(memory=memory, preferences=InMemoryPreferenceStore(), learner=NarrativeLearner(memory),
                                 pattern_ids=DEFAULT_LIBRARY.ids),
        media=media,
        team=VideoIntelligenceTeam(media, vision=VisionAnalyzer(provider) if provider else None, cache=LRUIntelligenceCache()),
        architect=StoryArchitect(validator=validator),
        validator=validator,
        max_concurrency=4,
    )


def sources(ids):
    return [ClipSource(clip_id=c, path=f"/footage/{c}.mp4") for c in ids]


async def test_full_run_with_vision():
    media = SyntheticMediaBackend({cid: prof for cid, (prof, _) in GYM.items()})
    result = await build(media).run(project_id="p1", user_id="u1", prompt=NIKE, clips=sources(GYM))

    assert result.report.status == "succeeded", result.report.reasoning
    assert result.intent.genre.value == Genre.FITNESS, "footage must revise the weak 'sports' guess from 'nike'"
    assert result.story is not None and result.validation is not None
    assert set(result.context_pack.clip_intelligence) == set(GYM)
    assert result.story.hero_clip_id in {"c5", "c6"}, "hero must be one of the clips vision labelled peak/hero"
    assert result.story.timeline[0].start == 0
    assert [t.task_id for t in result.plan.execution_plan][-3:] == ["reconcile_intent", "design_story", "validate_story"]


async def test_signal_only_run_is_degraded_but_complete():
    media = SyntheticMediaBackend({cid: prof for cid, (prof, _) in GYM.items()})
    result = await build(media, vision=False).run(project_id="p1", user_id="u1", prompt=NIKE, clips=sources(GYM))
    assert result.story is not None
    assert all(c.provenance.vision_model is None for c in result.context_pack.clip_intelligence.values())
    assert result.story.story_score.confidence < 0.6


async def test_bad_clips_are_isolated():
    media = SyntheticMediaBackend({cid: prof for cid, (prof, _) in GYM.items()}, fail={"c2"})
    result = await build(media).run(project_id="p1", user_id="u1", prompt=NIKE, clips=sources(GYM))
    assert [f.clip_id for f in result.probe_failures] == ["c2"]
    assert "c2" not in result.story.selected_clip_ids
    assert result.story is not None


async def test_all_clips_unreadable_raises():
    media = SyntheticMediaBackend({}, fail={"a", "b"})
    with pytest.raises(PipelineError, match="no clip could be read"):
        await build(media).run(project_id="p", user_id="u", prompt="x", clips=sources(["a", "b"]))


async def test_duplicate_clip_ids_rejected():
    with pytest.raises(PipelineError, match="unique"):
        await build(SyntheticMediaBackend({})).probe(sources(["a", "a"]))


async def test_master_graph_retries_never_reduce_story_quality():
    media = SyntheticMediaBackend({cid: prof for cid, (prof, _) in GYM.items()})
    pipeline = build(media)
    direct = await pipeline.run(project_id="p1", user_id="u1", prompt=NIKE, clips=sources(GYM))
    graph = build_master_graph(pipeline).compile()
    state = await graph.ainvoke({
        "project_id": "p1", "user_id": "u1", "raw_prompt": NIKE,
        "clips": [{"clip_id": c, "path": f"/footage/{c}.mp4"} for c in GYM],
    })
    final = StoryPlan.model_validate(state["story_plan"])
    final_judgement = StoryJudgement.model_validate(state["story_judgement"])
    attempts = [StoryAttempt.model_validate(a) for a in state["story_attempts"]]

    # Layer 12 is a stub (critic_score 0) so every retry runs — the worst case for this bug.
    assert state["retry_count"] == 3 and len(attempts) == 4
    assert all(final_judgement.rank_key >= a.judgement.rank_key for a in attempts), "final story must be the best seen"
    initial = attempts[0]
    assert initial.story_pattern == direct.story.story_pattern
    assert final_judgement.rank_key >= initial.judgement.rank_key, "retries may improve the story, never degrade it"
    kept = [a for a in attempts if a.kept]
    assert final.story_pattern == kept[-1].story_pattern
    assert sum(a.kept for a in attempts) >= 1 and all(a.reason for a in attempts)
    assert state["story_acts"] and state["story_timeline"][0]["start"] == 0


async def test_execution_events_stream_progress():
    events = []
    media = SyntheticMediaBackend({cid: prof for cid, (prof, _) in GYM.items()})
    pipeline = build(media)
    pipeline.director.executor._on_event = events.append
    await pipeline.run(project_id="p", user_id="u", prompt=NIKE, clips=sources(GYM))
    succeeded = {e.task_id for e in events if e.status == TaskStatus.SUCCEEDED}
    assert {"design_story", "validate_story"} <= succeeded and sum(t.startswith("analyze_clip:") for t in succeeded) == 6
