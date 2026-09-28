"""
MONTA — Vision Analyzer (Layer 6, multimodal)
===============================================
Sends sampled keyframes to any vision-capable ``ModelProvider`` (Qwen-VL,
Gemini, future models) and returns a schema-validated ``VisionObservation``.
No vendor logic here — the provider abstraction owns that.
"""

import json
from collections.abc import Sequence

from shared.contracts.clip import TechnicalMetadata, VisionObservation
from shared.contracts.vocab import CameraType, Emotion, ShotType, StoryRole
from shared.providers.base import Capability, GenerationConfig, ImagePart, Message, ModelProvider
from shared.providers.errors import CapabilityError
from shared.arbitration import VISION_POLICY, ReliabilityTable, run_arbitrated
from shared.arbitration.arbitrate import VISION_SELF_CONFIDENCE
from shared.contracts.arbitration import ArbitrationSummary
from shared.providers.structured import generate_structured, schema_prompt


def _enum(e) -> str:
    return ", ".join(v.value for v in e)


SYSTEM_PROMPT = f"""You are the Video Intelligence analyst of MONTA, an AI video editor.
You receive frames sampled in time order from ONE clip. Describe what is actually visible — never invent.

Field guidance:
- activities: concrete verbs/actions ("deadlift", "walking on beach", "speaking to camera").
- objects: salient physical objects. people: one short description per distinct person (no names, no identity guesses).
- camera_type: {_enum(CameraType)}.
- shot_type: dominant framing — {_enum(ShotType)}.
- scene_type: 1–3 words ("gym", "beach", "kitchen", "stage").
- emotion: the feeling the clip conveys to a viewer — {_enum(Emotion)}; emotion_confidence 0–1.
- energy_estimate: 0 (still, quiet) to 10 (explosive action).
- story_role_candidates: narrative jobs this clip could do in an edit, with confidence —
  {_enum(StoryRole)}. Use 'hero' only for the single most impressive/defining kind of shot.
- quality_issues: visible problems ("motion blur", "out of focus", "overexposed", "watermark", "obstructed lens").
- reasoning: one or two sentences citing what you saw.

Return ONLY a JSON object matching this schema:
{schema_prompt(VisionObservation)}
"""


def _semantic_checks(obs: VisionObservation) -> list[str]:
    roles = [c.role for c in obs.story_role_candidates]
    errors = []
    if len(roles) != len(set(roles)):
        errors.append("story_role_candidates contains duplicate roles")
    if not obs.story_role_candidates:
        errors.append("story_role_candidates must contain at least one role")
    return errors


class VisionAnalyzer:
    """One vision provider → structured observation; several → arbitrated observation."""

    def __init__(self, providers: ModelProvider | Sequence[ModelProvider], *, timeout_s: float = 45.0, max_repairs: int = 1,
                 reliability: ReliabilityTable | None = None):
        self.providers = [providers] if isinstance(providers, ModelProvider) else list(providers)
        if not self.providers:
            raise ValueError("VisionAnalyzer needs at least one provider")
        for p in self.providers:
            if Capability.VISION not in p.capabilities:
                raise CapabilityError(f"{p.model_id} cannot analyze images", provider=p.name, model=p.model)
        self.config = GenerationConfig(temperature=0.1, max_output_tokens=1024, json_output=True, timeout_s=timeout_s)
        self.max_repairs = max_repairs
        self.reliability = reliability or ReliabilityTable()

    @property
    def model_id(self) -> str:
        if len(self.providers) == 1:
            return self.providers[0].model_id
        return "arbitrated:" + "+".join(p.model_id for p in self.providers)

    async def observe(self, meta: TechnicalMetadata, frames: list[ImagePart], times_s: list[float]) -> VisionObservation:
        observation, _ = await self.observe_with_arbitration(meta, frames, times_s)
        return observation

    async def observe_with_arbitration(self, meta: TechnicalMetadata, frames: list[ImagePart],
                                       times_s: list[float]) -> tuple[VisionObservation, ArbitrationSummary | None]:
        context = {
            "clip_id": meta.clip_id,
            "duration_s": round(meta.duration_s, 2),
            "resolution": f"{meta.width}x{meta.height}",
            "fps": round(meta.fps, 2),
            "frame_timestamps_s": times_s,
        }
        messages = [
            Message.system(SYSTEM_PROMPT),
            Message.user(f"Clip context: {json.dumps(context)}\nAnalyze the {len(frames)} frames above.", images=tuple(frames)),
        ]
        if len(self.providers) == 1:
            result = await generate_structured(
                self.providers[0], messages, VisionObservation, config=self.config,
                semantic_validator=_semantic_checks, max_repairs=self.max_repairs,
            )
            return result.value, None
        return await run_arbitrated(self.providers, messages, VisionObservation, VISION_POLICY, self.reliability,
                                    config=self.config, semantic_validator=_semantic_checks,
                                    self_confidence_fields=VISION_SELF_CONFIDENCE, max_repairs=self.max_repairs)
