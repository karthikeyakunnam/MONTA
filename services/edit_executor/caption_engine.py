"""
MONTA — Caption Engine
========================
Generates, formats, and burns styled captions onto video using local Whisper
transcription and deterministic typography rendering.
Works 100% locally and offline without external API dependencies.
"""

import asyncio
import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Optional, Sequence

from PIL import Image, ImageDraw, ImageFont

from speech.caption_generator import CaptionGenerator
from speech.transcriber import WhisperTranscriber
from shared.contracts.vocab import CaptionStyle
from shared.contracts.timeline import TextOverlayIR

logger = logging.getLogger("monta.captions.engine")

# Visual styling presets for rendered caption overlays
_STYLE_SPECS = {
    CaptionStyle.NIKE: {
        "font_size": 48,
        "text_color": (255, 255, 255, 255),
        "stroke_color": (0, 0, 0, 255),
        "stroke_width": 4,
        "bg_pill": None,
        "uppercase": True,
        "bottom_margin": 80,
    },
    CaptionStyle.GYMSHARK: {
        "font_size": 46,
        "text_color": (255, 230, 0, 255),  # Vibrant yellow
        "stroke_color": (0, 0, 0, 255),
        "stroke_width": 4,
        "bg_pill": None,
        "uppercase": True,
        "bottom_margin": 80,
    },
    CaptionStyle.DAVID_GOGGINS: {
        "font_size": 52,
        "text_color": (255, 255, 255, 255),
        "stroke_color": (180, 0, 0, 255),  # Blood red stroke
        "stroke_width": 5,
        "bg_pill": None,
        "uppercase": True,
        "bottom_margin": 90,
    },
    CaptionStyle.CINEMATIC_DOCUMENTARY: {
        "font_size": 34,
        "text_color": (235, 235, 235, 255),
        "stroke_color": (0, 0, 0, 180),
        "stroke_width": 1,
        "bg_pill": (0, 0, 0, 160),         # Translucent black pill
        "uppercase": False,
        "bottom_margin": 90,
    },
    CaptionStyle.MOTIVATIONAL: {
        "font_size": 44,
        "text_color": (255, 200, 30, 255), # Warm gold
        "stroke_color": (0, 0, 0, 255),
        "stroke_width": 3,
        "bg_pill": None,
        "uppercase": False,
        "bottom_margin": 75,
    },
    CaptionStyle.ALPHA_MINDSET: {
        "font_size": 50,
        "text_color": (255, 215, 0, 255),  # Electric gold
        "stroke_color": (0, 0, 0, 255),
        "stroke_width": 5,
        "bg_pill": None,
        "uppercase": True,
        "bottom_margin": 85,
    },
    CaptionStyle.MINIMAL: {
        "font_size": 36,
        "text_color": (255, 255, 255, 255),
        "stroke_color": (20, 20, 20, 200),
        "stroke_width": 2,
        "bg_pill": (0, 0, 0, 120),
        "uppercase": False,
        "bottom_margin": 60,
    },
}


class CaptionEngine:
    """Generate, style, and burn-in video captions."""

    def __init__(
        self,
        model_size: str = "base",
        ffmpeg_bin: str = "ffmpeg",
        temp_dir: Optional[str] = None,
    ):
        self.ffmpeg_bin = ffmpeg_bin
        self.temp_dir = temp_dir or tempfile.gettempdir()
        self.transcriber = WhisperTranscriber(model_size=model_size, ffmpeg_bin=ffmpeg_bin)
        self.generator = CaptionGenerator()

    async def transcribe(self, video_path: str, language: Optional[str] = None) -> list[dict[str, Any]]:
        """Transcribe audio from video using local Whisper model.

        Returns:
            List of segments: [{'id': 0, 'start': 0.0, 'end': 2.5, 'text': '...'}]
        """
        result = await self.transcriber.transcribe_video(video_path, language=language)
        return result.get("segments", [])

    def create_overlay_image(
        self,
        text: str,
        width: int,
        height: int,
        style: CaptionStyle | str = CaptionStyle.MINIMAL,
        output_path: Optional[str] = None,
        placement: str = "bottom",
    ) -> str:
        """Render a single caption overlay transparent PNG matching the requested style."""
        matched = CaptionStyle.MINIMAL
        if isinstance(style, CaptionStyle):
            matched = style
        else:
            style_str = str(style).strip().lower()
            for cs in CaptionStyle:
                if cs.value.lower() == style_str:
                    matched = cs
                    break

        spec = _STYLE_SPECS.get(matched, _STYLE_SPECS[CaptionStyle.MINIMAL])

        # Scale font size with resolution (reference 1080p height)
        scale = height / 1080.0
        font_size = max(18, int(round(spec["font_size"] * scale)))
        if placement == "top":
            font_size = min(font_size, max(18, round(width * 0.05)))
        stroke_width = max(1, int(round(spec["stroke_width"] * scale)))
        bottom_margin = int(round(spec["bottom_margin"] * scale))

        display_text = text.upper() if spec.get("uppercase", False) else text

        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Load appropriate system font or fallback
        try:
            # Common macOS system fonts
            font_paths = [
                "/System/Library/Fonts/Helvetica.ttc",
                "/System/Library/Fonts/SFCompact.ttf",
                "/System/Library/Fonts/Supplemental/Arial Black.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf",
            ]
            font = None
            for fp in font_paths:
                if os.path.exists(fp):
                    try:
                        font = ImageFont.truetype(fp, font_size)
                        break
                    except Exception:
                        continue
            if font is None:
                font = ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        # Wrap against the actual rendered font. Long ASR segments must not be
        # drawn beyond the video edges (Pillow silently clips those pixels).
        safe_width = width * 0.88
        words = display_text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}" if current else word
            if current and draw.textlength(candidate, font=font) > safe_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        if any(draw.textlength(line, font=font) > safe_width for line in lines):
            raise ValueError("Caption contains a word wider than the safe video area")
        line_spacing = max(4, round(font_size * 0.15))
        line_boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
                      for line in lines]
        line_heights = [box[3] - box[1] for box in line_boxes]
        text_h = sum(line_heights) + line_spacing * max(0, len(lines) - 1)
        if text_h > height * 0.35:
            raise ValueError("Caption is too long for the safe video area")
        if placement == "bottom":
            y = height - bottom_margin - text_h
            if y < height * 0.55:
                raise ValueError("Caption overlaps the subject-safe area")
        elif placement == "top":
            y = bottom_margin
            if y + text_h > height * 0.45:
                raise ValueError("Caption is too tall for the upper safe area")
        else:
            raise ValueError(f"Unsupported caption placement: {placement}")

        # Draw background pill if requested
        if spec.get("bg_pill"):
            pad_x = int(round(16 * scale))
            pad_y = int(round(8 * scale))
            widest = max((draw.textlength(line, font=font) for line in lines), default=0)
            pill_box = ((width - widest) / 2 - pad_x, y - pad_y,
                        (width + widest) / 2 + pad_x, y + text_h + pad_y)
            radius = int(round(8 * scale))
            draw.rounded_rectangle(pill_box, radius=radius, fill=spec["bg_pill"])

        # Draw stroke and text
        line_y = y
        for line, line_height in zip(lines, line_heights):
            draw.text(((width - draw.textlength(line, font=font)) / 2, line_y), line,
                      font=font, fill=spec["text_color"], stroke_width=stroke_width,
                      stroke_fill=spec["stroke_color"])
            line_y += line_height + line_spacing

        if not output_path:
            out_file = Path(self.temp_dir) / f"cap_overlay_{os.getpid()}_{id(text)}.png"
            output_path = str(out_file)

        img.save(output_path, "PNG")
        return output_path

    def create_avoiding_overlay_image(
        self, text: str, width: int, height: int, start: float, end: float,
        avoid_overlays: Sequence[TextOverlayIR], *,
        style: CaptionStyle | str = CaptionStyle.MINIMAL,
        output_path: str,
        placement_order: tuple[str, ...] = ("bottom", "top"),
    ) -> str:
        """Choose a caption region using the actual saved text asset pixels."""
        active = [item for item in avoid_overlays
                  if item.start_ms < round(end * 1000) and item.end_ms > round(start * 1000)]
        reserved = []
        for item in active:
            with Image.open(item.asset_path) as image:
                if image.size != (width, height):
                    raise ValueError(f"Overlay {item.overlay_id} dimensions do not match the video")
                bounds = image.convert("RGBA").getchannel("A").getbbox()
                if bounds:
                    reserved.append((item.overlay_id, bounds))
        for placement in placement_order:
            self.create_overlay_image(text, width, height, style=style,
                                      output_path=output_path, placement=placement)
            with Image.open(output_path) as image:
                caption_box = image.getchannel("A").getbbox()
            if caption_box is None:
                raise ValueError("Caption overlay has no visible pixels")
            if not any(self._boxes_intersect(caption_box, box, padding=12)
                       for _, box in reserved):
                return output_path
        raise ValueError("Caption overlaps required title/subtitle in the available safe areas")

    def _prepare_caption_overlays(
        self, captions: list[dict[str, Any]], width: int, height: int,
        style: CaptionStyle | str, avoid_overlays: Sequence[TextOverlayIR],
    ) -> list[tuple[str, float, float]]:
        """Hold one vertical caption region throughout a continuous caption track when safe."""
        items = [item for item in captions if item.get("text", "").strip()]
        overlays: list[tuple[str, float, float]] = []
        placements: list[str] = []
        for index, item in enumerate(items):
            path = str(Path(self.temp_dir) / f"cap_{os.getpid()}_{index}.png")
            self.create_avoiding_overlay_image(
                item["text"].strip(), width, height, item["start"], item["end"],
                avoid_overlays, style=style, output_path=path,
            )
            with Image.open(path) as image:
                bounds = image.getchannel("A").getbbox()
            placements.append("top" if bounds and bounds[3] < height / 2 else "bottom")
            overlays.append((path, item["start"], item["end"]))
        if set(placements) == {"top", "bottom"}:
            try:
                for item, (path, _, _) in zip(items, overlays):
                    self.create_avoiding_overlay_image(
                        item["text"].strip(), width, height, item["start"], item["end"],
                        avoid_overlays, style=style, output_path=path,
                        placement_order=("top",),
                    )
            except ValueError:
                # Some other required overlay occupies the upper area; retain
                # each cue's individually validated position.
                for item, (path, _, _) in zip(items, overlays):
                    self.create_avoiding_overlay_image(
                        item["text"].strip(), width, height, item["start"], item["end"],
                        avoid_overlays, style=style, output_path=path,
                    )
        return overlays

    @staticmethod
    def _boxes_intersect(first: tuple[int, int, int, int], second: tuple[int, int, int, int],
                         *, padding: int = 0) -> bool:
        return (first[0] < second[2] + padding and first[2] + padding > second[0]
                and first[1] < second[3] + padding and first[3] + padding > second[1])

    async def burn_captions(
        self,
        video_path: str,
        captions: list[dict[str, Any]],
        output_path: str,
        style: CaptionStyle | str = CaptionStyle.MINIMAL,
        width: int = 1920,
        height: int = 1080,
        audio_gain: float = 1.0,
        avoid_overlays: Sequence[TextOverlayIR] = (),
    ) -> str:
        """Render and burn styled captions directly into video footage."""
        if not math.isfinite(audio_gain) or not 0 < audio_gain <= 1:
            raise ValueError("audio_gain must be finite, positive, and at most 1")
        if audio_gain != 1 and not captions:
            raise ValueError("audio gain requires a caption render; no captions were supplied")
        if not captions:
            # Nothing to burn, copy or link
            cmd = [self.ffmpeg_bin, "-y", "-v", "error", "-i", video_path, "-c", "copy", output_path]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()
            return output_path

        out_path = str(Path(output_path).resolve())
        overlay_files: list[str] = []
        try:
            # 1. Generate transparent overlays with a stable vertical position.
            overlay_files = self._prepare_caption_overlays(
                captions, width, height, style, avoid_overlays,
            )

            if not overlay_files:
                cmd = [self.ffmpeg_bin, "-y", "-v", "error", "-i", video_path, "-c", "copy", out_path]
                proc = await asyncio.create_subprocess_exec(*cmd)
                await proc.communicate()
                return out_path

            # 2. Build FFmpeg overlay filtergraph
            # [0:v][1:v]overlay=0:0:enable='between(t,s1,e1)'[v1];[v1][2:v]overlay=...
            cmd = [self.ffmpeg_bin, "-y", "-v", "error", "-i", video_path]
            for img_path, _, _ in overlay_files:
                cmd.extend(["-i", img_path])

            filters = []
            cur_v = "[0:v]"
            for idx, (_, start_s, end_s) in enumerate(overlay_files):
                in_img = f"[{idx + 1}:v]"
                nxt_v = f"[v_cap_{idx}]" if idx < len(overlay_files) - 1 else "[v_out]"
                filters.append(
                    f"{cur_v}{in_img}overlay=0:0:enable='between(t,{start_s:.3f},{end_s:.3f})'{nxt_v}"
                )
                cur_v = nxt_v

            full_filter = ";".join(filters)
            cmd.extend([
                "-filter_complex", full_filter,
                "-map", "[v_out]",
                "-map", "0:a?",
                "-c:v", "libx264",
                "-c:a", "aac",
                "-preset", "fast",
            ])
            if audio_gain != 1:
                cmd.extend(["-af", f"volume={audio_gain:.6f}"])
            cmd.append(out_path)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                err = stderr.decode().strip()
                raise RuntimeError(f"FFmpeg caption burning failed: {err}")

            return out_path
        finally:
            # Clean up temporary PNG overlays
            for item in overlay_files:
                path = item[0] if isinstance(item, tuple) else item
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    def export_subtitles(
        self,
        captions: list[dict[str, Any]],
        srt_path: Optional[str] = None,
        ass_path: Optional[str] = None,
        style: CaptionStyle | str = CaptionStyle.MINIMAL,
        width: int = 1920,
        height: int = 1080,
    ) -> dict[str, str]:
        """Export SRT and ASS subtitle files."""
        results = {}
        if srt_path:
            srt_content = self.generator.to_srt(captions)
            Path(srt_path).write_text(srt_content, encoding="utf-8")
            results["srt"] = srt_path
        if ass_path:
            ass_content = self.generator.to_ass(captions, style=style, play_res_x=width, play_res_y=height)
            Path(ass_path).write_text(ass_content, encoding="utf-8")
            results["ass"] = ass_path
        return results
