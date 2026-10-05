"""
MONTA — Shared Contracts
==========================
Versioned, type-safe schemas exchanged between Layers 3–7 and consumed by the
Critic (Layer 12) and Learning Layer (Layer 14).
"""

from shared.contracts.timeline import (
    AspectRatio,
    AudioSegmentIR,
    ColorAdjustment,
    RenderProfileIR,
    ScaleMode,
    TimelineIR,
    TimelineSegmentIR,
    TransitionType,
)

__all__ = [
    "TimelineIR",
    "TimelineSegmentIR",
    "AudioSegmentIR",
    "RenderProfileIR",
    "AspectRatio",
    "ScaleMode",
    "TransitionType",
    "ColorAdjustment",
]
