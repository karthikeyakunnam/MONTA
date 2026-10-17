"""Rasterize validated prompt text into transparent, deterministic PNG overlays."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from shared.contracts.intent import TextOverlayRequirement

_FONT_PATHS = {
    ("serif", False): "/System/Library/Fonts/Supplemental/Georgia.ttf",
    ("serif", True): "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    ("sans_serif", False): "/System/Library/Fonts/Supplemental/Arial.ttf",
    ("sans_serif", True): "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
}
_COLORS = {"white": (255, 255, 255, 255), "magenta": (242, 0, 153, 255)}
_SIZES = {"title": 0.068, "subtitle": 0.026, "credit": 0.023, "caption": 0.031}


def render_text_overlay_asset(
    requirement: TextOverlayRequirement,
    width: int,
    height: int,
    output_path: str | Path,
) -> Path:
    """Create a full-canvas RGBA asset; no prompt text is evaluated as a filter string."""
    if width <= 0 or height <= 0:
        raise ValueError("overlay dimensions must be positive")

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font_key = (requirement.font_family or ("serif" if requirement.role == "title" else "sans_serif"),
                requirement.bold if requirement.bold is not None else requirement.role == "title")
    font_path = _FONT_PATHS[font_key]
    font_size = max(18, round(height * _SIZES[requirement.role]))
    max_width = round(width * 0.88)
    tracking = round(height * 0.002) if requirement.wide_tracking else 0

    while font_size > 18:
        font = ImageFont.truetype(font_path, font_size)
        measured = sum(draw.textlength(character, font=font) for character in requirement.text)
        measured += tracking * max(0, len(requirement.text) - 1)
        if measured <= max_width:
            break
        font_size -= 2
    else:
        font = ImageFont.truetype(font_path, font_size)

    text_color = _COLORS[requirement.color or "white"]
    text_bbox = draw.textbbox((0, 0), requirement.text, font=font, stroke_width=0)
    text_width = sum(draw.textlength(character, font=font) for character in requirement.text)
    text_width += tracking * max(0, len(requirement.text) - 1)
    text_height = text_bbox[3] - text_bbox[1]
    x = max(0, round((width - text_width) / 2))

    position = requirement.position or {
        "title": "center", "subtitle": "below_title", "credit": "lower_third", "caption": "bottom",
    }[requirement.role]
    y_ratios = {"center": 0.39, "below_title": 0.48, "top": 0.12, "lower_third": 0.76, "bottom": 0.87}
    y = round(height * y_ratios[position] - text_height / 2)
    y = min(max(0, y), max(0, height - text_height))

    card_bounds = None
    if requirement.background == "dark_card":
        pad_x, pad_y = round(width * 0.055), round(height * 0.018)
        card_bounds = (max(0, x - pad_x), max(0, y - pad_y),
                       min(width, x + round(text_width) + pad_x),
                       min(height, y + text_height + pad_y))
        draw.rounded_rectangle(card_bounds, radius=round(height * 0.014), fill=(0, 0, 0, 190))

    if requirement.glow:
        glow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)
        _draw_tracked(glow_draw, (x, y), requirement.text, font, text_color, tracking)
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=max(2, round(height * 0.008))))
        canvas = Image.alpha_composite(canvas, glow_layer)
        draw = ImageDraw.Draw(canvas)
        if card_bounds:
            draw.rounded_rectangle(card_bounds, radius=round(height * 0.014), fill=(0, 0, 0, 190))

    _draw_tracked(draw, (x, y), requirement.text, font, text_color, tracking)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG")
    return destination


def _draw_tracked(draw: ImageDraw.ImageDraw, position, text: str, font, fill, tracking: int) -> None:
    x, y = position
    for character in text:
        draw.text((round(x), y), character, font=font, fill=fill)
        x += draw.textlength(character, font=font) + tracking