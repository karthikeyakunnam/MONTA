"""
MONTA — Local Vision Pipeline (Layer 6, offline multimodal)
============================================================
Two-stage pipeline for local-only semantic video analysis:

  Stage 1 — VLM description:
    Send bounded keyframes to a lightweight vision model (moondream, llava, etc.)
    through the Ollama adapter and receive a natural-language description.

  Stage 2 — Structured extraction:
    Feed the description to a text model (qwen2.5) with the VisionObservation
    schema prompt to produce validated, schema-compliant JSON.

This decouples "seeing" from "structuring" so that each model can be used
at its strength:
  - moondream is excellent at describing images but cannot produce JSON schemas
  - qwen2.5 is excellent at following JSON schemas but cannot see images

The resulting VisionObservation is compatible with the existing fusion and
story architecture pipeline — no downstream changes needed.

Memory / timeout constraints:
  - Maximum 4 frames sent to the VLM per clip (bounded at call site)
  - Each frame is resized to ≤512px wide before JPEG encoding
  - VLM timeout: 60s per clip (configurable)
  - Text structuring timeout: 30s (configurable)
  - Total per-clip vision budget: ≤90s
"""

import json
import logging
from collections.abc import Sequence

from shared.contracts.clip import TechnicalMetadata, VisionObservation
from shared.contracts.vocab import CameraType, Emotion, ShotType, StoryRole
from shared.providers.base import (
    Capability,
    GenerationConfig,
    ImagePart,
    Message,
    ModelProvider,
    ModelResponse,
)
from shared.providers.errors import (
    CapabilityError,
    ProviderError,
    ProviderResponseError,
    StructuredOutputError,
)
from shared.providers.structured import extract_json_object, generate_structured, schema_prompt
from shared.observability import catalog as m
from shared.observability.tracing import span

logger = logging.getLogger("monta.intelligence.local_vision")

# Maximum frames per VLM call to prevent OOM on small models
MAX_VLM_FRAMES = 4

# Compact enumeration strings for the structuring prompt
_CAMERA_TYPES = ", ".join(v.value for v in CameraType)
_SHOT_TYPES = ", ".join(v.value for v in ShotType)
_EMOTIONS = ", ".join(v.value for v in Emotion)
_STORY_ROLES = ", ".join(v.value for v in StoryRole)

VLM_DESCRIBE_PROMPT = """Describe what you see in this video frame in detail.
Focus on: what actions/activities are happening, what objects are visible,
how many people and what they look like (no names), the camera angle and
framing, the setting/scene type, the mood/emotion conveyed, and any quality
issues (blur, overexposure, etc.).
Be specific and factual. Do not invent things you cannot see."""

STRUCTURING_SYSTEM_PROMPT = f"""You are a video analysis structuring engine.
You receive a natural-language description of video frames and must convert it
into a JSON object matching the VisionObservation schema.

Field mapping:
- activities: list of concrete verbs/actions observed (e.g. ["walking", "lifting weights"])
- objects: list of salient physical objects (e.g. ["barbell", "tree", "car"])
- people: list of short descriptions of distinct people (e.g. ["woman in red dress", "man with beard"])
- camera_type: one of [{_CAMERA_TYPES}]
- shot_type: one of [{_SHOT_TYPES}]
- scene_type: 1-3 words describing the setting (e.g. "gym", "beach sunset")
- emotion: dominant feeling conveyed — one of [{_EMOTIONS}]
- emotion_confidence: 0.0–1.0 how confident you are about the emotion
- energy_estimate: 0 (still/quiet) to 10 (explosive action)
- story_role_candidates: list of {{"role": "<role>", "confidence": 0.0-1.0}} where role is one of [{_STORY_ROLES}]
  Include 1-3 roles. Use "hero" only for the single most impressive/defining shot.
- visual_tags: additional visual descriptors not captured above
- quality_issues: any visible problems (e.g. ["motion blur", "overexposed"])
- reasoning: 1-2 sentences explaining your analysis

Return ONLY a valid JSON object. No markdown, no explanation outside the JSON."""


class LocalVisionPipeline:
    """Two-stage local vision: VLM describes → text model structures.

    This is a drop-in replacement for VisionAnalyzer.observe() when running
    in offline mode with local models that cannot produce structured JSON
    from images directly.
    """

    def __init__(
        self,
        vlm_provider: ModelProvider,
        text_provider: ModelProvider,
        *,
        vlm_timeout_s: float = 60.0,
        text_timeout_s: float = 30.0,
        max_frames: int = MAX_VLM_FRAMES,
        max_repairs: int = 1,
    ):
        """
        Args:
            vlm_provider: A vision-capable model provider (moondream, llava, etc.)
            text_provider: A text model provider for JSON structuring (qwen2.5, etc.)
            vlm_timeout_s: Timeout for the VLM description stage.
            text_timeout_s: Timeout for the text structuring stage.
            max_frames: Maximum frames to send to the VLM.
            max_repairs: Number of repair attempts for structured output.
        """
        if Capability.VISION not in vlm_provider.capabilities:
            raise CapabilityError(
                f"{vlm_provider.model_id} does not support vision",
                provider=vlm_provider.name,
                model=vlm_provider.model,
            )
        self.vlm = vlm_provider
        self.text = text_provider
        self.vlm_config = GenerationConfig(
            temperature=0.1,
            max_output_tokens=512,
            json_output=False,
            timeout_s=vlm_timeout_s,
        )
        self.text_config = GenerationConfig(
            temperature=0.0,
            max_output_tokens=1024,
            json_output=True,
            timeout_s=text_timeout_s,
        )
        self.max_frames = max_frames
        self.max_repairs = max_repairs

    @property
    def model_id(self) -> str:
        return f"local_vision:{self.vlm.model_id}+{self.text.model_id}"

    async def _describe_frames(
        self,
        meta: TechnicalMetadata,
        frames: list[ImagePart],
        times_s: list[float],
    ) -> str:
        """Stage 1: Send frames to VLM and get natural-language description."""
        # Bound the number of frames
        if len(frames) > self.max_frames:
            step = len(frames) / self.max_frames
            indices = [int(i * step) for i in range(self.max_frames)]
            frames = [frames[i] for i in indices]
            times_s = [times_s[i] for i in indices if i < len(times_s)]

        context = (
            f"This is a {meta.duration_s:.1f}s video clip ({meta.width}x{meta.height}, {meta.fps:.0f}fps). "
            f"You are seeing {len(frames)} keyframes sampled at timestamps: "
            f"{', '.join(f'{t:.1f}s' for t in times_s[:len(frames)])}."
        )

        messages = [
            Message.user(
                f"{context}\n\n{VLM_DESCRIBE_PROMPT}",
                images=tuple(frames),
            ),
        ]

        with span("local_vision.describe", model=self.vlm.model_id, n_frames=len(frames)):
            response = await self.vlm.generate(messages, self.vlm_config)

        description = response.text.strip()
        if not description:
            raise ProviderResponseError(
                "VLM returned empty description",
                provider=self.vlm.name,
                model=self.vlm.model,
            )

        logger.debug(
            "VLM description for %s (%d frames, %.0fms): %s",
            meta.clip_id,
            len(frames),
            response.latency_ms,
            description[:200],
        )
        return description

    async def _structure_observation(
        self,
        description: str,
        meta: TechnicalMetadata,
    ) -> VisionObservation:
        """Stage 2: Convert natural-language description to VisionObservation JSON."""
        user_msg = (
            f"Video clip: {meta.clip_id}, duration {meta.duration_s:.1f}s, "
            f"resolution {meta.width}x{meta.height}.\n\n"
            f"Visual observation from footage:\n{description}\n\n"
            f"Convert this observation to a VisionObservation JSON object."
        )

        messages = [
            Message.system(STRUCTURING_SYSTEM_PROMPT),
            Message.user(user_msg),
        ]

        def _semantic_checks(obs: VisionObservation) -> list[str]:
            roles = [c.role for c in obs.story_role_candidates]
            errors = []
            if len(roles) != len(set(roles)):
                errors.append("story_role_candidates contains duplicate roles")
            if not obs.story_role_candidates:
                errors.append("story_role_candidates must contain at least one role")
            return errors

        with span("local_vision.structure", model=self.text.model_id):
            result = await generate_structured(
                self.text,
                messages,
                VisionObservation,
                config=self.text_config,
                semantic_validator=_semantic_checks,
                max_repairs=self.max_repairs,
            )

        return result.value

    async def observe(
        self,
        meta: TechnicalMetadata,
        frames: list[ImagePart],
        times_s: list[float],
    ) -> VisionObservation:
        """Full two-stage pipeline: describe → structure → validate."""
        with span("local_vision.observe", clip_id=meta.clip_id, n_frames=len(frames)):
            description = await self._describe_frames(meta, frames, times_s)
            observation = await self._structure_observation(description, meta)

        logger.info(
            "Local vision observation for %s: %d activities, %d roles, energy=%.1f",
            meta.clip_id,
            len(observation.activities),
            len(observation.story_role_candidates),
            observation.energy_estimate,
        )
        m.CLIP_ANALYSIS.inc(outcome="local_vision_ok")
        return observation

    async def observe_with_arbitration(
        self,
        meta: TechnicalMetadata,
        frames: list[ImagePart],
        times_s: list[float],
    ) -> tuple[VisionObservation, None]:
        """Compatibility with VisionAnalyzer interface. No arbitration for local pipeline."""
        obs = await self.observe(meta, frames, times_s)
        return obs, None
