"""
MONTA — Intelligence Fusion (Layer 6)
=======================================
Combines measured signals with vision observations into ``ClipIntelligence``.

Authority rules (what each source is trusted for):

=================  ==========================  =================================
field              primary source              why
=================  ==========================  =================================
quality_score      signals (−0.5/vision issue)  pixels don't lie about blur/exposure
energy_score       0.65 signals + 0.35 vision   motion is measurable; intensity is semantic
camera_motion      signals                     optical flow beats visual guessing
lighting           signals                     luma statistics
semantics/roles    vision                      only a model can see "deadlift"
=================  ==========================  =================================

Without a vision provider the clip is still fully usable: semantic fields
stay empty, roles are derived from energy/quality with low confidence, and
the reasoning says so explicitly.
"""

from datetime import datetime, timezone

from orchestration.agents.intelligence.signals import (
    SIGNAL_VERSION,
    camera_motion_from_signals,
    energy_from_signals,
    lighting_from_signals,
    quality_from_signals,
)
from shared.contracts.clip import AnalysisProvenance, ClipIntelligence, RoleCandidate, SignalMetrics, TechnicalMetadata, VisionObservation
from shared.contracts.explain import clamp
from shared.contracts.vocab import StoryRole

FUSION_VERSION = "fusion.v1"
MIN_USABLE_DURATION_S = 0.5
MIN_USABLE_QUALITY = 2.0
VISION_ISSUE_PENALTY = 0.5
MAX_ISSUE_PENALTY = 2.0


def derived_roles(energy: float, quality: float) -> tuple[RoleCandidate, ...]:
    """Low-confidence roles from measurements alone, used when no vision model is available."""
    if energy >= 7.5:
        roles = [(StoryRole.PEAK, 0.35), (StoryRole.INTENSITY, 0.3)]
    elif energy >= 5.0:
        roles = [(StoryRole.PROGRESS, 0.3), (StoryRole.INTENSITY, 0.25)]
    else:
        roles = [(StoryRole.ESTABLISHING, 0.3), (StoryRole.B_ROLL, 0.3), (StoryRole.PREPARATION, 0.2)]
    if quality >= 8.0:
        roles.append((StoryRole.HERO, 0.25))
    return tuple(RoleCandidate(role=r, confidence=c) for r, c in roles)


def fuse(
    meta: TechnicalMetadata,
    signals: SignalMetrics,
    vision: VisionObservation | None,
    *,
    vision_model: str | None,
    degraded: tuple[str, ...] = (),
) -> ClipIntelligence:
    q_sig, q_conf, q_why = quality_from_signals(signals)
    e_sig, e_conf, e_why = energy_from_signals(signals, meta.duration_s)
    lighting, l_why = lighting_from_signals(signals)
    motion, m_why = camera_motion_from_signals(signals)

    reasons = [f"Quality {q_sig:.1f} from {q_why}.", f"Camera {motion.value} ({m_why}).", f"Lighting {lighting.value} ({l_why})."]

    if vision is not None:
        penalty = min(MAX_ISSUE_PENALTY, VISION_ISSUE_PENALTY * len(vision.quality_issues))
        quality = clamp(q_sig - penalty, 0, 10)
        if penalty:
            reasons.append(f"Quality reduced {penalty:.1f} for visible issues: {', '.join(vision.quality_issues)}.")
        energy = 0.65 * e_sig + 0.35 * vision.energy_estimate
        agreement = 1 - abs(e_sig - vision.energy_estimate) / 10
        energy_conf = clamp(e_conf + 0.25 * agreement, 0, 0.95)
        reasons.append(
            f"Energy {energy:.1f} = 0.65×measured {e_sig:.1f} ({e_why}) + 0.35×vision {vision.energy_estimate:.1f}."
        )
        reasons.append(f"Vision: {vision.reasoning}")
        semantic = dict(
            activities=vision.activities, objects=vision.objects, people=vision.people,
            camera_type=vision.camera_type, shot_type=vision.shot_type, scene_type=vision.scene_type,
            emotion=vision.emotion, emotion_confidence=vision.emotion_confidence,
            story_role_candidates=vision.story_role_candidates, visual_tags=vision.visual_tags,
        )
        quality_conf = clamp(q_conf + 0.05, 0, 0.95)
    else:
        quality, energy, energy_conf, quality_conf = q_sig, e_sig, e_conf, q_conf
        reasons.append(f"Energy {energy:.1f} measured ({e_why}).")
        reasons.append("No vision analysis: semantic fields are empty and story roles are derived from energy/quality only.")
        semantic = dict(story_role_candidates=derived_roles(e_sig, q_sig))

    usable, why_not = True, None
    if meta.duration_s < MIN_USABLE_DURATION_S:
        usable, why_not = False, f"duration {meta.duration_s:.2f}s below {MIN_USABLE_DURATION_S}s"
    elif signals.frames_analyzed < 2:
        usable, why_not = False, "fewer than 2 frames could be decoded"
    elif quality < MIN_USABLE_QUALITY:
        usable, why_not = False, f"quality {quality:.1f} below {MIN_USABLE_QUALITY}"
    if why_not:
        reasons.append(f"Unusable: {why_not}.")

    return ClipIntelligence(
        clip_id=meta.clip_id,
        duration_s=meta.duration_s,
        camera_motion=motion,
        lighting=lighting,
        quality_score=round(quality, 2),
        quality_confidence=round(quality_conf, 3),
        energy_score=round(clamp(energy, 0, 10), 2),
        energy_confidence=round(energy_conf, 3),
        reasoning=" ".join(reasons),
        peak_time_s=min(signals.peak_time_s, meta.duration_s),
        energy_curve=signals.energy_curve,
        usable=usable,
        unusable_reason=why_not,
        provenance=AnalysisProvenance(
            signal_version=SIGNAL_VERSION, vision_model=vision_model if vision else None,
            analyzed_at=datetime.now(timezone.utc), degraded=degraded,
        ),
        **semantic,
    )
