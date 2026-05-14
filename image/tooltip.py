from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from census.constants import CLASS_GROUPS, ALL_CLASSES
from census.models import ItemData, ItemEffect, ItemStat

# ---------------------------------------------------------------------------
# Colours matching the in-game tooltip screenshot
# ---------------------------------------------------------------------------
BG = (10, 10, 14)
BORDER_OUTER = (196, 158, 44)   # golden amber
BORDER_INNER = (54, 76, 92)     # slate teal

C_NAME = (232, 232, 232)
C_WHITE = (220, 220, 220)
C_BODY = (208, 208, 208)

# Rarity / quality  — text colour
QUALITY_COLORS: dict[str, tuple[int, int, int]] = {
    "fabled":    (255, 147, 157),   # #ff939d  — light pink
    "legendary": (255, 138,   0),
    "treasured": (176, 166,  96),
    "uncommon":  ( 96, 206,  96),
    "common":    (178, 178, 178),
}

# Glow colour for qualities that have a bloom effect.
# Value: (glow_rgb, glow_radius, glow_passes)
QUALITY_GLOWS: dict[str, tuple[tuple[int, int, int], int, int]] = {
    "fabled": ((223, 83, 95), 4, 2),    # #DF535F × 2 passes
}

C_STAT_PRIMARY = (44, 192, 72)     # green
C_STAT_SECONDARY = (60, 192, 192)  # cyan
C_VALUE = (60, 192, 192)
C_CLASS = (44, 192, 72)
C_GOLD = (196, 162, 28)
C_EFFECT_NAME = (228, 58, 58)

# Adornment slot name → colour
ADORN_COLORS: dict[str, tuple[int, int, int]] = {
    "white": (220, 220, 220),
    "turquoise": (60, 192, 192),
    "orange": (255, 138, 0),
    "red": (215, 55, 55),
    "blue": (80, 118, 218),
    "green": (55, 192, 55),
    "yellow": (215, 192, 55),
    "purple": (176, 78, 218),
}

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
WIDTH = 460
PADDING = 18          # left/right margin inside border
BORDER_W = 3
INSET = 6             # gap to inner border
LINE_GAP = 2          # extra pixels between lines
SECTION_GAP = 8       # extra pixels between sections
COL2_FRAC = 0.48      # second column starts at this fraction of content width


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_tooltip(item: ItemData) -> Image.Image:
    return _TooltipRenderer().render(item)


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

class _TooltipRenderer:
    def render(self, item: ItemData) -> Image.Image:
        fonts = _load_fonts()

        # Draw onto a tall scratch canvas, then crop
        canvas = Image.new("RGB", (WIDTH, 1600), BG)
        draw = ImageDraw.Draw(canvas)

        content_x = PADDING + BORDER_W + 2
        final_y = self._draw_all(draw, canvas, item, fonts, content_x)

        # Crop
        bottom = final_y + PADDING + BORDER_W + 2
        img = canvas.crop((0, 0, WIDTH, bottom))

        # Borders drawn on final-sized image
        d = ImageDraw.Draw(img)
        w, h = img.size
        d.rectangle([0, 0, w - 1, h - 1], outline=BORDER_OUTER, width=BORDER_W)
        d.rectangle(
            [INSET, INSET, w - 1 - INSET, h - 1 - INSET],
            outline=BORDER_INNER,
            width=1,
        )
        return img

    # ------------------------------------------------------------------

    def _draw_all(
        self,
        draw: ImageDraw.ImageDraw,
        canvas: Image.Image,
        item: ItemData,
        fonts: dict,
        x: int,
    ) -> int:
        y = PADDING + BORDER_W + 4
        content_w = WIDTH - 2 * x
        col2_x = x + int(content_w * COL2_FRAC)

        # Icon (top-right corner)
        ICON_SIZE = 64
        icon_x = WIDTH - x - ICON_SIZE
        icon_y = y
        name_max_w = icon_x - x - 8

        if item.icon_bytes:
            try:
                icon = Image.open(io.BytesIO(item.icon_bytes)).convert("RGBA")
                icon = icon.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
                bg = Image.new("RGB", (ICON_SIZE, ICON_SIZE), (20, 20, 20))
                bg.paste(icon.convert("RGB"), (0, 0))
                framed = Image.new("RGB", (ICON_SIZE + 2, ICON_SIZE + 2), BORDER_INNER)
                framed.paste(bg, (1, 1))
                canvas.paste(framed, (icon_x - 1, icon_y - 1))
            except Exception:
                _draw_icon_placeholder(draw, icon_x, icon_y, ICON_SIZE)
        else:
            _draw_icon_placeholder(draw, icon_x, icon_y, ICON_SIZE)

        # Item name (wraps if needed, leaving room for icon)
        for line in _wrap(item.name, fonts["name"], name_max_w):
            draw.text((x, y), line, font=fonts["name"], fill=C_NAME)
            y += _lh(fonts["name"])
        y += 2

        # Keep y below icon bottom
        if item.icon_bytes:
            y = max(y, icon_y + ICON_SIZE + 6)

        # Rarity
        if item.quality:
            q = item.quality.lower()
            color = QUALITY_COLORS.get(q, C_WHITE)
            glow_info = QUALITY_GLOWS.get(q)
            if glow_info:
                glow_color, glow_radius, glow_passes = glow_info
                _draw_with_glow(
                    canvas, x, y, item.quality.upper(), fonts["bold_lg"],
                    color, glow_color, glow_radius, glow_passes,
                )
            else:
                draw.text((x, y), item.quality.upper(), font=fonts["bold_lg"], fill=color)
            y += _lh(fonts["bold_lg"]) + 4

        # Primary stats (green)
        primary = [s for s in item.stats if s.stat_group == "primary"]
        if primary:
            y = _draw_stat_cols(draw, primary, fonts["bold"], y, x, col2_x, C_STAT_PRIMARY)
            y += LINE_GAP

        # Secondary stats (cyan)
        secondary = [s for s in item.stats if s.stat_group == "secondary"]
        if secondary:
            y = _draw_stat_cols(draw, secondary, fonts["bold"], y, x, col2_x, C_STAT_SECONDARY)
            y += LINE_GAP

        # Armor section
        has_armor = any([item.armor_type, item.slot_type, item.mitigation, item.item_level])
        if has_armor:
            y += SECTION_GAP
            header = _armor_header(item)
            if header:
                draw.text((x, y), header, font=fonts["bold"], fill=C_WHITE)
                y += _lh(fonts["bold"]) + LINE_GAP
            if item.mitigation is not None:
                y = _draw_kv(draw, x, y, "Mitigation", str(item.mitigation), fonts["regular"])
            if item.item_level is not None:
                y = _draw_kv(draw, x, y, "Level", str(item.item_level), fonts["regular"])
            y += LINE_GAP

        # Class restrictions
        if item.classes:
            y += 2
            draw.text((x, y), _format_classes(item.classes), font=fonts["bold"], fill=C_CLASS)
            y += _lh(fonts["bold"]) + SECTION_GAP

        # Effects
        if item.effects:
            draw.text((x, y), "Effects:", font=fonts["bold"], fill=C_GOLD)
            y += _lh(fonts["bold"]) + LINE_GAP
            for eff in item.effects:
                y = _draw_effect(draw, eff, fonts, y, x, content_w)

        # Adornment slots
        if item.adornment_slots:
            y += SECTION_GAP
            draw.text((x, y), "Adornment Slots:", font=fonts["bold"], fill=C_GOLD)
            y += _lh(fonts["bold"]) + LINE_GAP
            y = _draw_adorn_slots(draw, item.adornment_slots, fonts["regular"], y, x)

        # Flags (HEIRLOOM  LORE-EQUIP  ATTUNEABLE …)
        if item.flags:
            y += SECTION_GAP + 4
            text = "   ".join(item.flags)
            draw.text((x, y), text, font=fonts["bold_lg"], fill=C_GOLD)
            y += _lh(fonts["bold_lg"]) + LINE_GAP

        # Game link
        if item.game_link:
            y += SECTION_GAP
            draw.text((x, y), "Game Link:", font=fonts["bold"], fill=C_WHITE)
            y += _lh(fonts["bold"]) + LINE_GAP
            y = _draw_game_link_box(draw, item.game_link, fonts["small"], y, x, content_w)

        return y


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _draw_with_glow(
    canvas: Image.Image,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    text_color: tuple[int, int, int],
    glow_color: tuple[int, int, int],
    glow_radius: int = 4,
    glow_passes: int = 2,
) -> None:
    """
    Render text with a CSS-style glow + black outline onto canvas in-place.

    Matches: text-shadow -1/0/1px black outline + blur glow in glow_color.
    """
    w, h = canvas.size

    # Build glow layer: draw text in glow_color, then blur
    glow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow_layer).text((x, y), text, font=font, fill=(*glow_color, 220))
    blurred = glow_layer.filter(ImageFilter.GaussianBlur(radius=glow_radius))

    # Composite glow passes onto canvas
    base = canvas.convert("RGBA")
    for _ in range(glow_passes):
        base = Image.alpha_composite(base, blurred)

    # 1-pixel black outline (matches the 4-direction text-shadow)
    d = ImageDraw.Draw(base)
    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        d.text((x + ox, y + oy), text, font=font, fill=(0, 0, 0, 255))

    # Main text on top
    d.text((x, y), text, font=font, fill=(*text_color, 255))

    # Write result back into the original canvas image in-place
    canvas.paste(base.convert("RGB"))


def _draw_icon_placeholder(
    draw: ImageDraw.ImageDraw,
    icon_x: int,
    icon_y: int,
    size: int,
) -> None:
    """Draw a dark bordered box in place of the item icon."""
    draw.rectangle(
        [icon_x - 1, icon_y - 1, icon_x + size, icon_y + size],
        fill=(18, 18, 22),
        outline=BORDER_INNER,
        width=1,
    )


def _draw_stat_cols(
    draw: ImageDraw.ImageDraw,
    stats: list[ItemStat],
    font: ImageFont.FreeTypeFont,
    y: int,
    x: int,
    col2_x: int,
    color: tuple,
) -> int:
    i = 0
    while i < len(stats):
        s1 = stats[i]
        draw.text((x, y), _fmt_stat(s1), font=font, fill=color)
        if i + 1 < len(stats):
            s2 = stats[i + 1]
            draw.text((col2_x, y), _fmt_stat(s2), font=font, fill=color)
            i += 2
        else:
            i += 1
        y += _lh(font) + 1
    return y


def _draw_kv(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    label: str,
    value: str,
    font: ImageFont.FreeTypeFont,
    tab: int = 110,
) -> int:
    draw.text((x, y), label, font=font, fill=C_WHITE)
    draw.text((x + tab, y), value, font=font, fill=C_VALUE)
    return y + _lh(font) + 1


def _draw_effect(
    draw: ImageDraw.ImageDraw,
    eff: ItemEffect,
    fonts: dict,
    y: int,
    x: int,
    content_w: int,
) -> int:
    draw.text((x, y), eff.name, font=fonts["bold"], fill=C_EFFECT_NAME)
    y += _lh(fonts["bold"]) + 1

    if eff.trigger:
        draw.text((x, y), eff.trigger, font=fonts["regular"], fill=C_BODY)
        y += _lh(fonts["regular"]) + LINE_GAP

    BULLET = "• "
    bullet_w = fonts["regular"].getbbox(BULLET)[2]
    indent_x = x + 8
    wrap_w = WIDTH - x - PADDING - BORDER_W - 2 - bullet_w - 8

    for line_text in eff.lines:
        wrapped = _wrap(line_text, fonts["regular"], wrap_w)
        for j, wline in enumerate(wrapped):
            if j == 0:
                draw.text((indent_x, y), BULLET + wline, font=fonts["regular"], fill=C_BODY)
            else:
                draw.text((indent_x + bullet_w, y), wline, font=fonts["regular"], fill=C_BODY)
            y += _lh(fonts["regular"]) + 1

    return y + LINE_GAP


def _draw_adorn_slots(
    draw: ImageDraw.ImageDraw,
    slots: list[str],
    font: ImageFont.FreeTypeFont,
    y: int,
    x: int,
) -> int:
    cur_x = x
    for i, slot in enumerate(slots):
        color = ADORN_COLORS.get(slot.lower(), C_WHITE)
        draw.text((cur_x, y), slot, font=font, fill=color)
        cur_x += font.getbbox(slot)[2]
        if i < len(slots) - 1:
            sep = ", "
            draw.text((cur_x, y), sep, font=font, fill=C_WHITE)
            cur_x += font.getbbox(sep)[2]
    return y + _lh(font) + LINE_GAP


def _draw_game_link_box(
    draw: ImageDraw.ImageDraw,
    game_link: str,
    font: ImageFont.FreeTypeFont,
    y: int,
    x: int,
    content_w: int,
) -> int:
    pad = 4
    h = _lh(font) + 2 * pad
    box_w = min(content_w, font.getbbox(game_link)[2] + 2 * pad)
    draw.rectangle([x, y, x + box_w, y + h], fill=(20, 20, 24), outline=C_WHITE, width=1)
    draw.text((x + pad, y + pad), game_link, font=font, fill=C_BODY)
    return y + h + LINE_GAP


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _fmt_stat(s: ItemStat) -> str:
    v = s.value
    value_str = str(int(v)) if v == int(v) else f"{v:g}"
    return f"{value_str} {s.display_name}"


def _format_classes(classes: list[str]) -> str:
    if not classes:
        return ""
    class_set = frozenset(classes)
    for group_set, group_name in CLASS_GROUPS.items():
        if class_set == group_set:
            return group_name
    return ", ".join(sorted(classes))


def _armor_header(item: ItemData) -> str:
    if item.armor_type and item.slot_type:
        return f"{item.armor_type} ({item.slot_type})"
    return item.armor_type or item.slot_type


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        test = " ".join(current + [word])
        if font.getbbox(test)[2] <= max_w or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _lh(font: ImageFont.FreeTypeFont) -> int:
    bbox = font.getbbox("Ag")
    return bbox[3] - bbox[1] + LINE_GAP


# ---------------------------------------------------------------------------
# Font loading
# ---------------------------------------------------------------------------

def _load_fonts() -> dict:
    project_fonts = Path("fonts")

    _SERIF_REGULAR = [
        project_fonts / "georgia.ttf",
        project_fonts / "Georgia.ttf",
        Path("C:/Windows/Fonts/georgia.ttf"),
        Path("/System/Library/Fonts/Supplemental/Georgia.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
    ]
    _SERIF_BOLD = [
        project_fonts / "georgiab.ttf",
        project_fonts / "georgia-bold.ttf",
        Path("C:/Windows/Fonts/georgiab.ttf"),
        Path("/System/Library/Fonts/Supplemental/Georgia Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
    ]

    def _find(candidates: list[Path]) -> Optional[str]:
        for p in candidates:
            if p.exists():
                return str(p)
        return None

    def _load(path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
        if path:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
        try:
            return ImageFont.load_default(size=size)  # Pillow >= 10
        except TypeError:
            return ImageFont.load_default()

    regular = _find(_SERIF_REGULAR)
    bold = _find(_SERIF_BOLD) or regular  # fall back to regular if no bold found

    return {
        "name": _load(bold, 20),
        "bold_lg": _load(bold, 16),
        "bold": _load(bold, 14),
        "regular": _load(regular, 13),
        "small": _load(regular, 12),
    }
