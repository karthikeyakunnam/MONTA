"""
MONTA — Style Mapper (Layer 8)
================================
Converts high-level style descriptions, ContextPack intent, and user preferences
into concrete editing parameters (color adjustments, transitions, scale modes,
audio style, caption style) that drive timeline generation and rendering.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from services.style_engine.presets import STYLE_PRESETS
from shared.contracts.context import ContextPack
from shared.contracts.timeline import ColorAdjustment, ScaleMode, TransitionType
from shared.contracts.vocab import CaptionStyle, ColorGrade, MusicStyle

logger = logging.getLogger("monta.style.mapper")

_STYLE_LIB_DIR = Path(__file__).parent / "style_library"


# Concrete ColorAdjustment mapping for each color grade
_COLOR_GRADE_MAP: dict[str, ColorAdjustment] = {
    "orange_teal": ColorAdjustment(brightness=-0.02, contrast=1.18, saturation=1.15, gamma=0.95),
    "cinematic": ColorAdjustment(brightness=-0.03, contrast=1.14, saturation=1.08, gamma=0.98),
    "dark": ColorAdjustment(brightness=-0.08, contrast=1.20, saturation=0.90, gamma=0.90),
    "high_contrast": ColorAdjustment(brightness=0.00, contrast=1.30, saturation=1.10, gamma=1.00),
    "vibrant": ColorAdjustment(brightness=0.02, contrast=1.12, saturation=1.25, gamma=1.02),
    "hype": ColorAdjustment(brightness=0.03, contrast=1.22, saturation=1.28, gamma=1.00),
    "warm": ColorAdjustment(brightness=0.01, contrast=1.08, saturation=1.12, gamma=1.05),
    "cool": ColorAdjustment(brightness=-0.01, contrast=1.10, saturation=0.95, gamma=0.96),
    "natural": ColorAdjustment(brightness=0.00, contrast=1.00, saturation=1.00, gamma=1.00),
    "bw": ColorAdjustment(brightness=0.00, contrast=1.12, saturation=0.0, gamma=1.00),
    "vintage": ColorAdjustment(brightness=0.02, contrast=0.92, saturation=0.82, gamma=1.04, highlight_warmth=0.12),
    "neon": ColorAdjustment(brightness=0.01, contrast=1.22, saturation=1.55, gamma=1.00),
}


class StyleMapper:
    """Layer 8: Maps creative styles into concrete timeline & rendering parameters."""

    def __init__(self):
        self._library_cache: dict[str, dict[str, Any]] = {}
        self._load_library()

    def _load_library(self):
        if _STYLE_LIB_DIR.exists():
            for f in _STYLE_LIB_DIR.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    self._library_cache[f.stem.lower()] = data
                except Exception as e:
                    logger.debug("Failed to load style library item %s: %s", f, e)

    def get_preset(self, name: str) -> dict[str, Any]:
        """Fetch style parameters by preset name."""
        k = name.lower().strip()
        if k in self._library_cache:
            return dict(self._library_cache[k])
        if k in STYLE_PRESETS:
            return dict(STYLE_PRESETS[k])
        for p_name, p_val in STYLE_PRESETS.items():
            if p_name in k or k in p_name:
                return dict(p_val)
        return dict(STYLE_PRESETS.get("cinematic", {}))

    async def map_style(self, style_description: str, intent: Optional[dict] = None) -> dict[str, Any]:
        """Convert a style description into editing parameters."""
        intent = intent or {}
        k = style_description.lower().strip()
        for preset_name, preset in STYLE_PRESETS.items():
            if preset_name in k:
                res = dict(preset)
                if "color" in intent:
                    res["color_grade"] = intent["color"]
                return res

        return {
            "cut_speed": "medium",
            "zoom": "subtle",
            "music": intent.get("music", "ambient"),
            "captions": intent.get("captions", "minimal"),
            "transitions": "cut",
            "color_grade": intent.get("color", "natural"),
            "scale_mode": "fit",
        }

    @classmethod
    def resolve_style_for_pack(
        cls,
        pack: ContextPack,
        style_override: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Deterministically resolve complete editing style parameters from ContextPack."""
        mapper = cls()
        merged: dict[str, Any] = {}

        # 1. Start with matched preset if specified in pack.style_presets
        if pack.style_presets:
            top_preset = pack.style_presets[0]
            merged.update(mapper.get_preset(top_preset.name))
            if top_preset.parameters:
                merged.update(top_preset.parameters)

        # 2. Extract from intent genre / color / caption
        if pack.intent.genre and pack.intent.genre.value:
            genre_str = pack.intent.genre.value.value.lower()
            genre_preset = mapper.get_preset(genre_str)
            for k, v in genre_preset.items():
                if k not in merged:
                    merged[k] = v

        if pack.intent.color_grade and pack.intent.color_grade.value:
            merged["color_grade"] = pack.intent.color_grade.value.value

        if pack.intent.caption_style and pack.intent.caption_style.value:
            merged["captions"] = pack.intent.caption_style.value.value

        if pack.intent.music_style and pack.intent.music_style.value:
            merged["music"] = pack.intent.music_style.value.value

        brief = pack.intent.creative_brief
        if "shadow_led_contrast" in brief.visual_treatments and brief.visual_confidence >= 0.45:
            merged["color_grade"] = "dark"
            merged.setdefault("shadow_coolness", 0.16)
            if brief.energy_trajectory == "rising" and any(
                tone.value in {"uplifting", "joyful", "inspiring"} for tone in brief.emotional_tones
            ) and brief.emotion_confidence >= 0.25:
                merged.setdefault("ending_highlight_warmth", 0.18)

        # 3. Layer user preferences if present
        if pack.user_preferences:
            prefs = pack.user_preferences
            if prefs.color_grade and prefs.color_grade.value:
                merged["color_grade"] = prefs.color_grade.value.value
            if prefs.caption_style and prefs.caption_style.value:
                merged["captions"] = prefs.caption_style.value.value
            if prefs.music_style and prefs.music_style.value:
                merged["music"] = prefs.music_style.value.value

        # An explicitly selected preset replaces inferred and remembered style.
        # Explicit individual controls below still take precedence over it.
        if style_override and style_override.get("style_preset"):
            merged.update(mapper.get_preset(str(style_override["style_preset"])))

        # 4. Explicit style override outranks inferred parameters
        if style_override:
            merged.update({key: value for key, value in style_override.items() if key != "style_preset"})

        # Defaults if unspecified
        merged.setdefault("cut_speed", "medium")
        merged.setdefault("transitions", "cut")
        merged.setdefault("transition_ms", 250)
        merged.setdefault("color_grade", "natural")
        merged.setdefault("music", "cinematic")
        merged.setdefault("captions", "minimal")
        merged.setdefault("scale_mode", "fit")

        return merged

    @classmethod
    def resolve_color_adjustment(cls, color_name: Optional[str]) -> Optional[ColorAdjustment]:
        """Convert a color grade name to a concrete ColorAdjustment object."""
        if not color_name:
            return None
        k = color_name.lower().replace("-", "_").replace(" ", "_")
        if k in _COLOR_GRADE_MAP:
            return _COLOR_GRADE_MAP[k]
        for name, adj in _COLOR_GRADE_MAP.items():
            if name in k:
                return adj
        return None

    @classmethod
    def resolve_transition(cls, trans_name: Optional[str], default_ms: int = 250) -> tuple[TransitionType, int]:
        """Convert a transition name to TransitionType and duration in ms."""
        if not trans_name:
            return TransitionType.CUT, 0
        raw = str(trans_name).lower().strip()
        if raw in ("cut", "smash_cut", "none"):
            return TransitionType.CUT, 0
        if raw in ("fade", "dissolve", "crossfade"):
            return TransitionType.CROSSFADE, max(50, default_ms)
        if raw in ("wipe", "wipe_left", "glitch"):
            return TransitionType.WIPE_LEFT, max(50, default_ms)
        if raw in ("wipe_right",):
            return TransitionType.WIPE_RIGHT, max(50, default_ms)
        if raw in ("slide", "slide_left"):
            return TransitionType.SLIDE_LEFT, max(50, default_ms)
        if raw in ("slide_right",):
            return TransitionType.SLIDE_RIGHT, max(50, default_ms)
        if raw in ("fade_black", "fadeblack", "black"):
            return TransitionType.FADE_BLACK, max(50, default_ms)
        if raw in ("fade_white", "fadewhite", "white", "flash"):
            return TransitionType.FADE_WHITE, max(50, default_ms)
        try:
            return TransitionType(raw), default_ms
        except ValueError:
            return TransitionType.CUT, 0

    @classmethod
    def resolve_scale_mode(cls, mode_name: Optional[str]) -> ScaleMode:
        """Convert scale mode string to ScaleMode enum."""
        if not mode_name:
            return ScaleMode.FIT
        k = str(mode_name).lower().strip()
        if k == "fill":
            return ScaleMode.FILL
        if k == "stretch":
            return ScaleMode.STRETCH
        return ScaleMode.FIT
