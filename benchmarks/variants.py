"""
MONTA — Benchmark Variants
============================
A variant is a named way to build the Layer 3–7 stack. Built-ins:

* ``current``          — this code, deterministic (no model calls). Always runnable.
* ``current_no_memory``— this code with an empty narrative memory (memory ablation).
* ``env_models``       — this code with the providers configured in the environment
                         (MONTA_TEXT_PROVIDERS / MONTA_ARBITRATION / MONTA_JUDGE_PROVIDERS /
                         MONTA_STORY_REFINER). Compare runs with different env to benchmark
                         alternative models (e.g. qwen vs gemini vs consensus).

Previous versions are compared through stored reports (``--baseline``), because a
report is the frozen output of that version on the same dataset hash.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass

from evaluation.golden import LayerStack
from evaluation.story_judge import CompositeStoryJudge, HeuristicStoryJudge, LLMStoryJudge
from memory.narrative_memory import InMemoryNarrativeStore, InMemoryPreferenceStore, NarrativeLearner
from orchestration.agents.story import DEFAULT_LIBRARY, StoryArchitect, StoryRefiner
from services.context_composer import ContextComposer
from services.prompt_engine import IntentEngine
from shared.contracts.memory import NarrativeMemoryRecord


@dataclass(frozen=True)
class Variant:
    name: str
    description: str
    build: Callable[[], LayerStack]
    uses_models: bool = False


def _composer(memory) -> ContextComposer:
    return ContextComposer(memory=memory, preferences=InMemoryPreferenceStore(), learner=NarrativeLearner(memory),
                           pattern_ids=DEFAULT_LIBRARY.ids)


def _current(history: list[NarrativeMemoryRecord] | None = None) -> LayerStack:
    memory = InMemoryNarrativeStore(history or [])
    return LayerStack(IntentEngine(), _composer(memory), StoryArchitect(), HeuristicStoryJudge())


def _env_models() -> LayerStack:
    from shared.providers.registry import build_providers

    providers = build_providers()
    text = providers.text_for_extraction
    refiner = StoryRefiner(providers.text) if providers.text and os.getenv("MONTA_STORY_REFINER", "").lower() in ("1", "true") else None
    judge = HeuristicStoryJudge()
    if providers.judge is not None:
        forbidden = [refiner.model_id] if refiner else []
        judge = CompositeStoryJudge([(HeuristicStoryJudge(), 0.5), (LLMStoryJudge(providers.judge, forbidden_model_ids=forbidden), 0.5)])
    memory = InMemoryNarrativeStore()
    return LayerStack(IntentEngine(text), _composer(memory), StoryArchitect(refiner=refiner), judge)


VARIANTS: dict[str, Variant] = {
    "current": Variant("current", "working tree, deterministic, no model calls", _current),
    "current_no_memory": Variant("current_no_memory", "working tree with empty narrative memory", lambda: _current([])),
    "env_models": Variant("env_models", "working tree with providers from the environment", _env_models, uses_models=True),
}
