"""
MONTA — Video Evaluation Manifest
===================================
JSONL, one clip per line. Every label is optional; metrics are computed only
over clips that carry the label, and the report states the sample size.

    {"clip_id": "gym_0042", "path": "clips/gym_0042.mp4",
     "labels": {"activities": ["deadlift"], "emotion": "intense", "scene_type": "gym",
                "shot_type": "medium", "camera_motion": "handheld", "lighting": "good",
                "story_roles": ["intensity", "peak"], "quality": 7.5, "energy": 8.0},
     "annotators": 3}

Paths are relative to the manifest file unless absolute.
"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.vocab import CameraMotion, Emotion, Lighting, ShotType, StoryRole


class ClipLabels(BaseModel):
    model_config = ConfigDict(frozen=True)

    activities: tuple[str, ...] | None = None
    emotion: Emotion | None = None
    scene_type: str | None = None
    shot_type: ShotType | None = None
    camera_motion: CameraMotion | None = None
    lighting: Lighting | None = None
    story_roles: tuple[StoryRole, ...] | None = None
    quality: float | None = Field(None, ge=0, le=10)
    energy: float | None = Field(None, ge=0, le=10)


class ManifestEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    clip_id: str
    path: str
    labels: ClipLabels
    annotators: int = Field(1, ge=1)
    source: str = Field("human", description="human | synthetic (labels true by construction)")


def load_manifest(path: str | Path) -> list[ManifestEntry]:
    base = Path(path).parent
    out = []
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        entry = ManifestEntry.model_validate(json.loads(line))
        p = Path(entry.path)
        out.append(entry.model_copy(update={"path": str(p if p.is_absolute() else (base / p).resolve())}))
    ids = [e.clip_id for e in out]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate clip_id")
    return out
