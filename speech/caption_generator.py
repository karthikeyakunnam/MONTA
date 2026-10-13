"""
MONTA — Caption Generator
===========================
Generates timestamped subtitles and formatted styled captions (SRT, ASS)
with support for Nike, Gymshark, David Goggins, Cinematic, and Minimal styles.
"""

import math
from typing import Any, Optional

from shared.contracts.vocab import CaptionStyle


def _sec_to_srt_time(seconds: float) -> str:
    """Format seconds into HH:MM:SS,mmm for SRT."""
    millis = int(round(seconds * 1000))
    h = millis // 3600000
    millis %= 3600000
    m = millis // 60000
    millis %= 60000
    s = millis // 1000
    ms = millis % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _sec_to_ass_time(seconds: float) -> str:
    """Format seconds into H:MM:SS.cc for ASS."""
    centis = int(round(seconds * 100))
    h = centis // 360000
    centis %= 360000
    m = centis // 6000
    centis %= 6000
    s = centis // 100
    cs = centis % 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ASS Style configurations
# ASS Color format: &HAABBGGRR (Alpha, Blue, Green, Red)
_ASS_STYLE_DEFS = {
    CaptionStyle.NIKE: {
        "font": "Arial Black",
        "size": 52,
        "primary": "&H00FFFFFF",   # Pure white
        "outline_col": "&H00000000", # Black outline
        "back_col": "&H80000000",
        "bold": 1,
        "italic": 0,
        "outline": 4.0,
        "shadow": 0.0,
        "alignment": 2,           # Bottom center
        "margin_v": 60,
        "uppercase": True,
    },
    CaptionStyle.GYMSHARK: {
        "font": "Arial Black",
        "size": 50,
        "primary": "&H0000E6FF",   # Vibrant Yellow/Cyan (B=00, G=E6, R=FF)
        "outline_col": "&H00000000",
        "back_col": "&H80000000",
        "bold": 1,
        "italic": 0,
        "outline": 4.0,
        "shadow": 2.0,
        "alignment": 2,
        "margin_v": 60,
        "uppercase": True,
    },
    CaptionStyle.DAVID_GOGGINS: {
        "font": "Arial Black",
        "size": 56,
        "primary": "&H00FFFFFF",   # White text
        "outline_col": "&H000000B0", # Deep red border (B=00, G=00, R=B0)
        "back_col": "&H90000000",
        "bold": 1,
        "italic": 0,
        "outline": 4.5,
        "shadow": 3.0,
        "alignment": 2,
        "margin_v": 70,
        "uppercase": True,
    },
    CaptionStyle.CINEMATIC_DOCUMENTARY: {
        "font": "Georgia",
        "size": 38,
        "primary": "&H00E8E8E8",   # Soft ivory
        "outline_col": "&H00202020",
        "back_col": "&HA0000000",
        "bold": 0,
        "italic": 0,
        "outline": 1.5,
        "shadow": 1.0,
        "alignment": 2,
        "margin_v": 80,
        "uppercase": False,
    },
    CaptionStyle.MOTIVATIONAL: {
        "font": "Arial",
        "size": 48,
        "primary": "&H0018C8FF",   # Warm Gold
        "outline_col": "&H00000000",
        "back_col": "&H80000000",
        "bold": 1,
        "italic": 1,
        "outline": 3.5,
        "shadow": 2.0,
        "alignment": 2,
        "margin_v": 60,
        "uppercase": False,
    },
    CaptionStyle.ALPHA_MINDSET: {
        "font": "Arial Black",
        "size": 54,
        "primary": "&H0000D0FF",   # Electric Gold Yellow
        "outline_col": "&H00000000",
        "back_col": "&H00000000",
        "bold": 1,
        "italic": 0,
        "outline": 5.0,
        "shadow": 2.0,
        "alignment": 2,
        "margin_v": 65,
        "uppercase": True,
    },
    CaptionStyle.MINIMAL: {
        "font": "Helvetica",
        "size": 36,
        "primary": "&H00FFFFFF",
        "outline_col": "&H00202020",
        "back_col": "&H80000000",
        "bold": 0,
        "italic": 0,
        "outline": 1.5,
        "shadow": 0.0,
        "alignment": 2,
        "margin_v": 45,
        "uppercase": False,
    },
}


class CaptionGenerator:
    """Formats raw transcription segments into styled subtitles and burning formats."""

    @classmethod
    def to_srt(cls, segments: list[dict[str, Any]]) -> str:
        """Convert transcription segments into standard SubRip (.srt) format."""
        lines = []
        for i, seg in enumerate(segments, start=1):
            start_str = _sec_to_srt_time(seg["start"])
            end_str = _sec_to_srt_time(seg["end"])
            text = seg.get("text", "").strip()
            lines.append(f"{i}")
            lines.append(f"{start_str} --> {end_str}")
            lines.append(text)
            lines.append("")
        return "\n".join(lines)

    @classmethod
    def to_ass(
        cls,
        segments: list[dict[str, Any]],
        style: CaptionStyle | str = CaptionStyle.MINIMAL,
        play_res_x: int = 1920,
        play_res_y: int = 1080,
    ) -> str:
        """Convert transcription segments into Advanced SubStation Alpha (.ass) format with styling."""
        matched_style = None
        if isinstance(style, CaptionStyle):
            matched_style = style
        else:
            style_str = str(style).strip().lower()
            for cs in CaptionStyle:
                if cs.value.lower() == style_str:
                    matched_style = cs
                    break
        if not matched_style or matched_style == CaptionStyle.NONE:
            matched_style = CaptionStyle.MINIMAL

        cfg = _ASS_STYLE_DEFS.get(matched_style, _ASS_STYLE_DEFS[CaptionStyle.MINIMAL])

        # Scale font size based on vertical resolution (reference PlayResY = 1080)
        scale_factor = play_res_y / 1080.0
        effective_font_size = int(round(cfg["size"] * scale_factor))
        effective_outline = round(cfg["outline"] * scale_factor, 1)
        effective_shadow = round(cfg["shadow"] * scale_factor, 1)
        effective_margin_v = int(round(cfg["margin_v"] * scale_factor))

        ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{cfg['font']},{effective_font_size},{cfg['primary']},&H000000FF,{cfg['outline_col']},{cfg['back_col']},{cfg['bold']},{cfg['italic']},0,0,100,100,0,0,1,{effective_outline},{effective_shadow},{cfg['alignment']},20,20,{effective_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        dialogue_lines = []
        for seg in segments:
            start_str = _sec_to_ass_time(seg["start"])
            end_str = _sec_to_ass_time(seg["end"])
            text = seg.get("text", "").strip()
            if cfg.get("uppercase", False):
                text = text.upper()
            # Escape newlines for ASS
            text = text.replace("\n", "\\N")
            dialogue_lines.append(f"Dialogue: 0,{start_str},{end_str},Main,,0,0,0,,{text}")

        return ass_header + "\n".join(dialogue_lines) + "\n"
