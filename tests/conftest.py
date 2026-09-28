"""Shared fixtures for the Layer 3–7 test suite."""

from collections.abc import Callable
from datetime import datetime, timezone

import numpy as np
import pytest

from shared.contracts.clip import AnalysisProvenance, ClipIntelligence, ClipSource, RoleCandidate, TechnicalMetadata
from shared.contracts.vocab import Emotion, StoryRole
from shared.providers.base import Capability, GenerationConfig, ImagePart, Message, ModelProvider, ModelResponse


class ScriptedProvider(ModelProvider):
    """Test double: returns queued responses (str) or raises queued exceptions, recording every request."""

    def __init__(self, responses: list, *, name: str = "scripted", vision: bool = True):
        self.name = name
        self.model = "test-model"
        self.capabilities = frozenset({Capability.TEXT, Capability.JSON_MODE} | ({Capability.VISION} if vision else set()))
        self.responses = list(responses)
        self.requests: list[list[Message]] = []

    async def generate(self, messages: list[Message], config: GenerationConfig | None = None) -> ModelResponse:
        self.check_request(messages)
        self.requests.append(list(messages))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return ModelResponse(text=item, provider=self.name, model=self.model)


class SyntheticMediaBackend:
    """MediaBackend over generated frames: each clip id maps to a motion profile."""

    def __init__(self, profiles: dict[str, str], *, durations: dict[str, float] | None = None, fail: set[str] = frozenset()):
        self.profiles = profiles
        self.durations = durations or {}
        self.fail = set(fail)
        self.luma_calls: list[str] = []

    async def probe(self, source: ClipSource) -> TechnicalMetadata:
        from orchestration.agents.intelligence.media import MediaToolError

        if source.clip_id in self.fail:
            raise MediaToolError(f"{source.clip_id}: corrupt file")
        return TechnicalMetadata(
            clip_id=source.clip_id, path=source.path, duration_s=self.durations.get(source.clip_id, 6.0), fps=30.0,
            width=1920, height=1080, codec="h264", has_audio=True, file_size_bytes=1000, fingerprint=f"fp-{source.clip_id}",
        )

    async def sample_luma(self, meta: TechnicalMetadata, *, max_frames: int, width: int):
        self.luma_calls.append(meta.clip_id)
        return synthetic_frames(self.profiles.get(meta.clip_id, "static")), 4.0

    async def keyframes(self, meta: TechnicalMetadata, times_s: list[float], *, width: int) -> list[ImagePart]:
        return [ImagePart(data=b"\xff\xd8jpeg", mime_type="image/jpeg") for _ in times_s]


def _texture(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.random((400, 700))
    k = np.ones(3) / 3
    for axis in (0, 1):
        base = np.apply_along_axis(lambda r: np.convolve(r, k, "same"), axis, base)
    return (base * 255).astype(np.uint8)


_TEX = _texture()


def synthetic_frames(profile: str, n: int = 16, h: int = 90, w: int = 160) -> np.ndarray:
    """static | pan | shaky | dark | blur | action (large subject motion)."""
    rng = np.random.default_rng(1)
    crop = lambda dy, dx: _TEX[100 + dy:100 + dy + h, 200 + dx:200 + dx + w]  # noqa: E731
    if profile == "pan":
        frames = [crop(0, i * 5) for i in range(n)]
    elif profile == "shaky":
        frames = [crop(int(rng.integers(-8, 8)), int(rng.integers(-8, 8))) for _ in range(n)]
    elif profile == "dark":
        frames = [(crop(0, 0) * 0.12).astype(np.uint8) for _ in range(n)]
    elif profile == "blur":
        frames = [np.full((h, w), 120, np.uint8) for _ in range(n)]
    elif profile == "action":
        frames = [_texture(seed=i)[:h, :w] for i in range(n)]
    else:
        frames = [crop(0, 0) for _ in range(n)]
    return np.stack(frames)


def make_clip(
    clip_id: str, energy: float, quality: float = 8.0, emotion: Emotion | None = Emotion.MOTIVATIONAL,
    roles: tuple[tuple[StoryRole, float], ...] = ((StoryRole.PROGRESS, 0.7),), *, duration: float = 5.0,
    usable: bool = True, activities: tuple[str, ...] = (), vision: bool = True,
) -> ClipIntelligence:
    return ClipIntelligence(
        clip_id=clip_id, duration_s=duration, activities=activities, quality_score=quality, quality_confidence=0.8,
        energy_score=energy, energy_confidence=0.7, emotion=emotion, emotion_confidence=0.7 if emotion else 0.0,
        story_role_candidates=tuple(RoleCandidate(role=r, confidence=c) for r, c in roles),
        reasoning="fixture", peak_time_s=duration / 2, usable=usable, unusable_reason=None if usable else "fixture unusable",
        provenance=AnalysisProvenance(signal_version="signals.v1", vision_model="scripted:test-model" if vision else None,
                                      analyzed_at=datetime.now(timezone.utc)),
    )


def metadata_for(clip: ClipIntelligence) -> TechnicalMetadata:
    return TechnicalMetadata(clip_id=clip.clip_id, path=f"/footage/{clip.clip_id}.mp4", duration_s=clip.duration_s, fps=30,
                             width=1080, height=1920, codec="h264", has_audio=True, file_size_bytes=1, fingerprint=clip.clip_id)


def gym_clips() -> list[ClipIntelligence]:
    R, E = StoryRole, Emotion
    return [
        make_clip("c1", 3.0, 7.5, E.DRAMATIC, ((R.STRUGGLE, .8), (R.ESTABLISHING, .6)), activities=("sitting in gym",)),
        make_clip("c2", 4.5, 7.0, E.INTENSE, ((R.PREPARATION, .8),), activities=("chalking hands",)),
        make_clip("c3", 6.0, 8.0, E.MOTIVATIONAL, ((R.PROGRESS, .8),), activities=("squat",)),
        make_clip("c4", 7.5, 8.2, E.INTENSE, ((R.INTENSITY, .8),), activities=("deadlift",)),
        make_clip("c5", 9.0, 8.6, E.AGGRESSIVE, ((R.PEAK, .9),), activities=("deadlift",)),
        make_clip("c6", 7.8, 9.4, E.INSPIRING, ((R.HERO, .9), (R.PAYOFF, .8)), activities=("flex",)),
        make_clip("c7", 1.0, 3.0, E.NEUTRAL, ((R.B_ROLL, .3),)),
    ]


@pytest.fixture
def clip_factory() -> Callable[..., ClipIntelligence]:
    return make_clip
