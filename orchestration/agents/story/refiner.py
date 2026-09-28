"""
MONTA — Story Refiner (Layer 7, optional LLM pass)
====================================================
"The model proposes, the validator disposes."

After the deterministic architect has built a valid plan, an LLM acting as
an elite editor may propose a different clip-to-act assignment (reorders,
swaps, a different hero). The proposal is schema-checked, then rebuilt and
re-validated by the same deterministic machinery; the architect adopts it
only if it has no more validation errors and does not lower the combined
score. The LLM can therefore improve taste but can never break a timeline.
"""

import json
from collections.abc import Mapping

from pydantic import BaseModel, Field

from orchestration.agents.story.selection import AdaptedPattern
from shared.contracts.clip import ClipIntelligence
from shared.contracts.context import ContextPack
from shared.providers.base import GenerationConfig, Message, ModelProvider
from shared.providers.structured import generate_structured, schema_prompt


class RefinementReasoning(BaseModel):
    summary: str = Field(..., min_length=1)
    changes: list[str] = []


class StoryRefinement(BaseModel):
    act_assignments: dict[str, list[str]]
    hero_clip_id: str
    reasoning: RefinementReasoning


SYSTEM_PROMPT = f"""You are the Story Architect of MONTA, thinking like a world-class film editor, advertising
director and short-form strategist. A deterministic system has drafted a story from a proven pattern.
Improve it only where an elite editor clearly would: stronger opening, cleaner escalation, a better hero shot,
better emotional progression. Keep the same acts, in the same order.

Constraints:
- Use only clip_ids from AVAILABLE CLIPS; each at most once; every act needs at least one clip.
- Order clips within each act as they should play.
- hero_clip_id must appear in the act listed as the hero act.
- If the draft is already right, return it unchanged and say why.

Return ONLY JSON matching:
{schema_prompt(StoryRefinement)}
"""


def build_messages(pack: ContextPack, adapted: AdaptedPattern, draft: Mapping[str, list[str]], hero: str,
                   clips: Mapping[str, ClipIntelligence]) -> list[Message]:
    payload = {
        "user_request": pack.user_prompt,
        "intent": {"genre": pack.intent.genre.value, "emotion": pack.intent.emotion.value, "pace": pack.intent.pace.value,
                   "arc": [b.model_dump(mode="json", include={"section", "pace", "emotion"}) for b in pack.intent.pacing_arc]},
        "pattern": adapted.pattern.name,
        "acts": [{"act_id": a.act_id, "purpose": a.template.purpose, "direction": a.template.energy_direction,
                  "target_energy": a.target_energy} for a in adapted.acts],
        "hero_act": adapted.acts[adapted.hero_act_index].act_id,
        "target_duration_s": adapted.target_duration_s,
        "draft": {"act_assignments": draft, "hero_clip_id": hero},
        "available_clips": [
            {"clip_id": c.clip_id, "energy": c.energy_score, "quality": c.quality_score,
             "emotion": c.emotion.value if c.emotion else None, "activities": list(c.activities)[:4],
             "shot_type": c.shot_type.value, "roles": [r.role.value for r in c.story_role_candidates][:4]}
            for c in clips.values() if c.usable
        ],
    }
    return [Message.system(SYSTEM_PROMPT), Message.user(json.dumps(payload))]


class StoryRefiner:
    def __init__(self, provider: ModelProvider, *, timeout_s: float = 20.0, max_repairs: int = 1):
        self.provider = provider
        self.config = GenerationConfig(temperature=0.3, max_output_tokens=1200, json_output=True, timeout_s=timeout_s)
        self.max_repairs = max_repairs

    @property
    def model_id(self) -> str:
        return self.provider.model_id

    async def propose(self, pack: ContextPack, adapted: AdaptedPattern, draft: Mapping[str, list[str]], hero: str,
                      clips: Mapping[str, ClipIntelligence]) -> StoryRefinement:
        act_ids = [a.act_id for a in adapted.acts]
        hero_act = act_ids[adapted.hero_act_index]
        usable = {cid for cid, c in clips.items() if c.usable}

        def check(r: StoryRefinement) -> list[str]:
            errors = []
            if list(r.act_assignments) != act_ids:
                errors.append(f"act_assignments keys must be exactly {act_ids} in order")
            seen: list[str] = [c for ids in r.act_assignments.values() for c in ids]
            if len(seen) != len(set(seen)):
                errors.append("a clip is used more than once")
            unknown = set(seen) - usable
            if unknown:
                errors.append(f"unknown or unusable clip_ids: {sorted(unknown)}")
            if any(not ids for ids in r.act_assignments.values()):
                errors.append("every act needs at least one clip")
            if r.hero_clip_id not in r.act_assignments.get(hero_act, []):
                errors.append(f"hero_clip_id must be in act '{hero_act}'")
            return errors

        result = await generate_structured(
            self.provider, build_messages(pack, adapted, draft, hero, clips), StoryRefinement,
            config=self.config, semantic_validator=check, max_repairs=self.max_repairs,
        )
        return result.value
