"""
MONTA — Style Mapper
======================
Layer 8: Converts style descriptions into concrete editing parameters.

Converts: "like a movie trailer"
Into: {"cut_speed": "fast", "zoom": "aggressive", "music": "epic", "captions": "bold"}
"""

from services.style_engine.presets import STYLE_PRESETS


class StyleMapper:
    """Maps style descriptions to concrete editing parameters."""

    async def map_style(self, style_description: str, intent: dict) -> dict:
        """
        Convert a style description into editing parameters.
        
        Checks presets first, then uses LLM for custom styles.
        """
        # Check presets
        for preset_name, preset in STYLE_PRESETS.items():
            if preset_name in style_description.lower():
                return preset

        # TODO: Use LLM for custom style generation
        return {
            "cut_speed": "medium",
            "zoom": "subtle",
            "music": "ambient",
            "captions": "clean",
            "transitions": "cut",
            "color_grade": intent.get("color", "natural"),
        }
