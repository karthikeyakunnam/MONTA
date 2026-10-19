"""
MONTA — Clip Contracts (Layer 2 metadata, Layer 6 intelligence)
=================================================================
``TechnicalMetadata`` is what ffprobe can prove. ``SignalMetrics`` is what pixel
statistics can measure. ``VisionObservation`` is what a multimodal model
believes. ``ClipIntelligence`` is the fused, explainable result consumed by the
Story Architect.
"""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.contracts.arbitration import ArbitrationSummary
from shared.contracts.base import NonEmptyText, Text
from shared.contracts.vocab import (
    CameraMotion,
    CameraType,
    Emotion,
    Lighting,
    ShotType,
    StoryRole,
)

CLIP_SCHEMA_VERSION = "clip.v1"

ShortText = Annotated[str, Field(max_length=200)]
Labels = Annotated[tuple[ShortText, ...], Field(max_length=32)]


class ClipSource(BaseModel):
    """A creator-uploaded clip as handed over by the Media Gateway."""

    model_config = ConfigDict(frozen=True)

    clip_id: str = Field(..., min_length=1, max_length=128)
    path: str = Field(..., min_length=1)


class TechnicalMetadata(BaseModel):
    """Container-level facts from ffprobe."""

    model_config = ConfigDict(frozen=True)

    clip_id: str
    path: str
    duration_s: float = Field(..., gt=0)
    fps: float = Field(..., gt=0)
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    codec: str
    has_audio: bool
    rotation: int = 0
    file_size_bytes: int = Field(..., ge=0)
    fingerprint: str = Field(..., description="Content hash used as the analysis cache key")
    # Stable active-picture rectangle detected in the source. Coordinates are in
    # decoded (autorotated) pixels: width, height, x, y.
    content_crop: tuple[int, int, int, int] | None = None
    # Distinguishes "crop scan completed and found no matte" from older cached
    # metadata that predates active-picture detection.
    content_crop_analyzed: bool = False

    @property
    def is_vertical(self) -> bool:
        w, h = (self.height, self.width) if self.rotation in (90, 270, -90) else (self.width, self.height)
        return h > w


class SignalMetrics(BaseModel):
    """Deterministic pixel measurements. Reproducible, cheap, model-free."""

    model_config = ConfigDict(frozen=True)

    signal_version: str
    sample_fps: float = Field(..., gt=0)
    frames_analyzed: int = Field(..., ge=0)
    brightness_mean: float = Field(..., ge=0, le=1)
    contrast: float = Field(..., ge=0)
    clipped_ratio: float = Field(..., ge=0, le=1)
    sharpness: float = Field(..., ge=0, description="Median variance of Laplacian on [0,1] luma")
    motion_mean: float = Field(..., ge=0, description="Mean absolute frame difference")
    motion_std: float = Field(..., ge=0)
    camera_speed: float = Field(..., ge=0, description="Global motion in frame-widths per second")
    camera_jitter: float = Field(..., ge=0, description="Frame-to-frame change of global motion, widths per second")
    direction_consistency: float = Field(..., ge=0, le=1)
    dominant_axis: str = Field(..., pattern="^(horizontal|vertical|none)$")
    cut_count: int = Field(..., ge=0)
    cut_times_s: tuple[float, ...] = Field((), description="Approximate source-relative visual discontinuities from sampled luma")
    peak_time_s: float = Field(..., ge=0)
    energy_curve: tuple[float, ...] = Field((), description="Normalized motion over time, at most 24 points")


class RoleCandidate(BaseModel):
    """A narrative role a clip could play, with confidence."""

    model_config = ConfigDict(frozen=True)

    role: StoryRole
    confidence: float = Field(..., ge=0, le=1)


class FrameEvidence(BaseModel):
    """A timestamped model observation; the source frame remains the authority."""

    model_config = ConfigDict(frozen=True)

    time_s: float = Field(..., ge=0)
    description: NonEmptyText
    model_id: str = Field(..., min_length=1)


class VisionObservation(BaseModel):
    """Semantic understanding returned by a multimodal provider (validated at the provider boundary)."""

    model_config = ConfigDict(frozen=True)

    activities: Labels = ()
    objects: Labels = ()
    people: Labels = Field((), description="One short description per distinct person")
    camera_type: CameraType = CameraType.UNKNOWN
    shot_type: ShotType = ShotType.UNKNOWN
    scene_type: str = Field("", max_length=64)
    emotion: Emotion = Emotion.NEUTRAL
    emotion_confidence: float = Field(0.0, ge=0, le=1)
    energy_estimate: float = Field(..., ge=0, le=10)
    story_role_candidates: tuple[RoleCandidate, ...] = ()
    visual_tags: Labels = ()
    quality_issues: Labels = Field((), description="e.g. 'motion blur', 'out of focus', 'watermark'")
    reasoning: NonEmptyText

    @model_validator(mode="before")
    @classmethod
    def _sanitize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)

        # 1. Normalize list fields if given as single string or delimited
        for list_field in ("activities", "objects", "people", "visual_tags", "quality_issues"):
            val = d.get(list_field)
            if isinstance(val, str):
                parts = [p.strip() for p in val.split(",") if p.strip()]
                d[list_field] = parts or ([val.strip()] if val.strip() else [])
            elif isinstance(val, (list, tuple)):
                d[list_field] = [str(x).strip() for x in val if str(x).strip()]

        # 2. Camera type normalization
        ct = str(d.get("camera_type", "")).strip().lower()
        if ct:
            cam_map = {
                "mobile": CameraType.PHONE,
                "smartphone": CameraType.PHONE,
                "cellphone": CameraType.PHONE,
                "phone camera": CameraType.PHONE,
                "action": CameraType.ACTION_CAM,
                "gopro": CameraType.ACTION_CAM,
                "aerial": CameraType.DRONE,
                "dslr": CameraType.CINEMA,
                "mirrorless": CameraType.CINEMA,
                "professional": CameraType.CINEMA,
                "cam": CameraType.UNKNOWN,
            }
            if ct in CameraType:
                d["camera_type"] = CameraType(ct)
            elif ct in cam_map:
                d["camera_type"] = cam_map[ct]

        # 3. Shot type normalization
        st = str(d.get("shot_type", "")).strip().lower().replace("-", "_").replace(" ", "_")
        if st:
            shot_map = {
                "close_up": ShotType.CLOSE_UP,
                "closeup": ShotType.CLOSE_UP,
                "close": ShotType.CLOSE_UP,
                "wide_shot": ShotType.WIDE,
                "long_shot": ShotType.WIDE,
                "medium_shot": ShotType.MEDIUM,
                "mid": ShotType.MEDIUM,
                "mid_shot": ShotType.MEDIUM,
                "full_shot": ShotType.WIDE,
            }
            if st in ShotType:
                d["shot_type"] = ShotType(st)
            elif st in shot_map:
                d["shot_type"] = shot_map[st]

        # 4. Emotion normalization
        emo = str(d.get("emotion", "")).strip().lower()
        if emo:
            emo_map = {
                "happy": Emotion.JOYFUL,
                "exciting": Emotion.ENERGETIC,
                "excited": Emotion.ENERGETIC,
                "action": Emotion.INTENSE,
                "peaceful": Emotion.CALM,
                "relaxed": Emotion.CALM,
                "sad": Emotion.EMOTIONAL,
                "somber": Emotion.NOSTALGIC,
                "melancholic": Emotion.NOSTALGIC,
                "love": Emotion.ROMANTIC,
            }
            if emo in Emotion:
                d["emotion"] = Emotion(emo)
            elif emo in emo_map:
                d["emotion"] = emo_map[emo]

        # 5. Energy estimate normalization
        ee = d.get("energy_estimate")
        if ee is not None:
            try:
                if isinstance(ee, str):
                    ee_clean = ee.split("/")[0].strip()
                    d["energy_estimate"] = max(0.0, min(10.0, float(ee_clean)))
                else:
                    d["energy_estimate"] = max(0.0, min(10.0, float(ee)))
            except (ValueError, TypeError):
                d["energy_estimate"] = 5.0

        # 6. Emotion confidence normalization
        ec = d.get("emotion_confidence")
        if ec is not None:
            try:
                d["emotion_confidence"] = max(0.0, min(1.0, float(ec)))
            except (ValueError, TypeError):
                d["emotion_confidence"] = 0.5

        # 7. Story role candidates normalization and deduplication
        srcs = d.get("story_role_candidates")
        if isinstance(srcs, (list, tuple)):
            norm_roles = []
            seen_roles = set()
            role_map = {
                "action": StoryRole.INTENSITY,
                "climax": StoryRole.PEAK,
                "introduction": StoryRole.ESTABLISHING,
                "intro": StoryRole.ESTABLISHING,
                "setup": StoryRole.PREPARATION,
                "broll": StoryRole.B_ROLL,
                "b-roll": StoryRole.B_ROLL,
                "reaction_shot": StoryRole.REACTION,
            }
            for item in srcs:
                if isinstance(item, str):
                    r_str = item.strip().lower()
                    conf = 0.7
                elif isinstance(item, dict):
                    r_str = str(item.get("role", "")).strip().lower()
                    try:
                        conf = float(item.get("confidence", 0.7))
                        conf = max(0.0, min(1.0, conf))
                    except (ValueError, TypeError):
                        conf = 0.7
                elif isinstance(item, RoleCandidate):
                    r_str = item.role.value
                    conf = item.confidence
                else:
                    continue

                matched_role = None
                if r_str in StoryRole:
                    matched_role = StoryRole(r_str)
                elif r_str in role_map:
                    matched_role = role_map[r_str]

                if matched_role and matched_role not in seen_roles:
                    seen_roles.add(matched_role)
                    norm_roles.append({"role": matched_role, "confidence": conf})

            d["story_role_candidates"] = norm_roles

        # 8. Reasoning normalization
        reasoning = str(d.get("reasoning", "")).strip()
        if not reasoning:
            acts = ", ".join(d.get("activities", [])) or "scene"
            scene = d.get("scene_type", "") or "footage"
            d["reasoning"] = f"Observed {acts} in {scene}."

        return d


class AnalysisProvenance(BaseModel):
    """Which analyzers produced a ClipIntelligence and what was unavailable."""

    model_config = ConfigDict(frozen=True)

    signal_version: str
    vision_model: str | None
    analyzed_at: datetime
    cache_hit: bool = False
    degraded: tuple[Text, ...] = ()
    arbitration: ArbitrationSummary | None = None


class ClipIntelligence(BaseModel):
    """Layer 6 output for one clip — the fused, explainable view the Story Architect consumes."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = CLIP_SCHEMA_VERSION
    clip_id: str
    duration_s: float = Field(..., gt=0)

    activities: Labels = ()
    objects: Labels = ()
    people: Labels = ()
    camera_type: CameraType = CameraType.UNKNOWN
    camera_motion: CameraMotion = CameraMotion.UNKNOWN
    shot_type: ShotType = ShotType.UNKNOWN
    lighting: Lighting = Lighting.UNKNOWN
    quality_score: float = Field(..., ge=0, le=10)
    quality_confidence: float = Field(..., ge=0, le=1)
    energy_score: float = Field(..., ge=0, le=10)
    energy_confidence: float = Field(..., ge=0, le=1)
    emotion: Emotion | None = None
    emotion_confidence: float = Field(0.0, ge=0, le=1)
    scene_type: str = ""
    story_role_candidates: tuple[RoleCandidate, ...] = ()
    visual_tags: Labels = ()
    frame_evidence: tuple[FrameEvidence, ...] = ()
    scene_cut_times_s: tuple[float, ...] = Field((), description="Approximate source-relative visual discontinuities; not semantic labels")
    reasoning: NonEmptyText

    peak_time_s: float = Field(0.0, ge=0)
    energy_curve: tuple[float, ...] = ()
    usable: bool = True
    unusable_reason: Text | None = None
    provenance: AnalysisProvenance

    def role_confidence(self, role: StoryRole) -> float:
        """Confidence that this clip can play ``role`` (0 if not a candidate)."""
        return max((c.confidence for c in self.story_role_candidates if c.role == role), default=0.0)
