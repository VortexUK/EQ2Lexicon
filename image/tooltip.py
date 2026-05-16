from __future__ import annotations

import io
from pathlib import Path
from typing import Optional


from PIL import Image, ImageDraw, ImageFilter, ImageFont

from census.constants import ARCHETYPES, CLASS_GROUPS, ALL_CLASSES
from census.models import ItemData, ItemEffect, ItemStat

# ---------------------------------------------------------------------------
# Colours matching the in-game tooltip screenshot
# ---------------------------------------------------------------------------
BG = (10, 10, 14)
BORDER_OUTER = (196, 158, 44)   # golden amber
BORDER_INNER = (54, 76, 92)     # slate teal

C_NAME = (232, 232, 232)
C_WHITE = (220, 220, 220)
C_BODY  = (199, 207, 199)   # #c7cfc7 — base card text colour from CSS

# Glow params shared between quality badges and effect names: (colour, radius, passes)
EFFECT_GLOW: tuple[tuple[int, int, int], int, int] = ((223, 83, 95), 4, 2)

# Rarity / quality  — text colour
QUALITY_COLORS: dict[str, tuple[int, int, int]] = {
    "fabled":    (255, 147, 157),   # #ff939d
    "legendary": (255, 201, 147),   # #ffc993
    "treasured":     (147, 217, 255),   # #93d9ff
    "mastercrafted": (147, 217, 255),   # same as treasured
    "uncommon":     (190, 255, 147),   # #beff93
    "handcrafted":  (190, 255, 147),   # same as uncommon
    "common":       (190, 255, 147),   # same as uncommon
}

QUALITY_GLOWS: dict[str, tuple[tuple[int, int, int], int, int]] = {
    "fabled":    EFFECT_GLOW,
    "legendary": ((213, 105, 0), 4, 2),    # #D56900
    "treasured":     ((213, 105, 0), 4, 2),   # #D56900
    "mastercrafted": ((213, 105, 0), 4, 2),
}

C_STAT_PRIMARY   = (34, 255, 34)    # #22ff22
C_STAT_SECONDARY = (60, 192, 192)   # cyan
C_VALUE       = (60, 192, 192)
C_CLASS       = (34, 255, 34)    # #22ff22
C_GOLD        = (230, 233, 112)     # #e6e970 — "Effects:" / "Adornment Slots:" headers
C_EFFECT_NAME = (255, 147, 157)     # #ff939d — same pink as FABLED rarity

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
# ZOOM: output size multiplier — change this to resize everything proportionally.
# SCALE: supersampling factor for anti-aliasing — keep at 2, do not change.
ZOOM        = 1.3
SCALE       = 2

def _z(n: float) -> int:
    """Convert a base pixel value to render pixels, applying both ZOOM and SCALE."""
    return round(n * ZOOM) * SCALE

WIDTH_OUT   = round(368 * ZOOM)    # final output width in pixels
WIDTH       = WIDTH_OUT * SCALE    # internal render width
PADDING     = _z(18)
BORDER_W    = _z(3)
INSET       = _z(6)
LINE_GAP    = _z(4)
SECTION_GAP = _z(8)
ICON_SIZE   = round(_z(64) * 0.9)  # render-space icon size (90% of base)
COL2_FRAC   = 0.48                 # fraction — scale-independent

_SLOT_BACKDROP_PATH = Path(__file__).resolve().parent.parent / "data" / "AAs" / "slot-empty-blue.png"
_slot_backdrop: Image.Image | None = None


def _get_slot_backdrop(size: int) -> Image.Image:
    global _slot_backdrop
    if _slot_backdrop is None or _slot_backdrop.size != (size, size):
        _slot_backdrop = (
            Image.open(_SLOT_BACKDROP_PATH).convert("RGBA").resize((size, size), Image.LANCZOS)
        )
    return _slot_backdrop


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

        # Borders drawn on full-resolution image before downscaling
        d = ImageDraw.Draw(img)
        w, h = img.size
        d.rectangle([0, 0, w - 1, h - 1], outline=BORDER_OUTER, width=BORDER_W)
        d.rectangle(
            [INSET, INSET, w - 1 - INSET, h - 1 - INSET],
            outline=BORDER_INNER,
            width=SCALE,
        )

        # Downscale to output size — Lanczos gives crisp anti-aliased result
        out_h = h // SCALE
        return img.resize((WIDTH_OUT, out_h), Image.LANCZOS)

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

        # Icon (top-right corner) — ICON_SIZE is the module-level scaled constant
        icon_x = WIDTH - x - ICON_SIZE
        icon_y = y
        name_max_w = icon_x - x - 8

        if item.icon_bytes:
            try:
                backdrop = _get_slot_backdrop(ICON_SIZE)
                icon = Image.open(io.BytesIO(item.icon_bytes)).convert("RGBA")
                icon = icon.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
                composed = Image.alpha_composite(backdrop, icon).convert("RGB")
                framed = Image.new("RGB", (ICON_SIZE + 2, ICON_SIZE + 2), BORDER_INNER)
                framed.paste(composed, (1, 1))
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

        # Description (bags, quest items, etc.) — stays in left column
        if item.description:
            for line in _wrap(item.description, fonts["regular"], name_max_w):
                draw.text((x, y), line, font=fonts["regular"], fill=C_BODY)
                y += _lh(fonts["regular"])
            y += LINE_GAP

        # Container slots
        if item.container_slots is not None:
            y = _draw_kv(draw, x, y, "Slots", str(item.container_slots), fonts["regular"], value_color=C_WHITE)
            y += LINE_GAP

        # Rarity — bottom of tier text aligns with bottom of icon
        if item.quality:
            tier_h  = _lh(fonts["bold_lg"])
            tier_y  = max(y + LINE_GAP, icon_y + ICON_SIZE - tier_h)
            q = item.quality.lower()
            color = QUALITY_COLORS.get(q, C_WHITE)
            glow_info = QUALITY_GLOWS.get(q)
            if glow_info:
                glow_color, glow_radius, glow_passes = glow_info
                _draw_with_glow(
                    canvas, x, tier_y, item.quality.upper(), fonts["bold_lg"],
                    color, glow_color, glow_radius, glow_passes,
                )
            else:
                draw.text((x, tier_y), item.quality.upper(), font=fonts["bold_lg"], fill=color)
            y = max(tier_y + tier_h, icon_y + ICON_SIZE) + 6

        # Adornment header
        if "adornment" in (item.armor_type or "").lower():
            draw.text((x, y), "Adds the following to an item:", font=fonts["regular"], fill=C_BODY)
            y += _lh(fonts["regular"]) + LINE_GAP

        # Primary stats (green) — attributes first (str/sta), skills last
        _PRIMARY_ORDER = {"Stamina": 0, "Primary Attributes": 1, "Resistances": 2, "Combat Skills": 3}
        primary = sorted(
            [s for s in item.stats if s.stat_group == "primary"],
            key=lambda s: _PRIMARY_ORDER.get(s.display_name, 99),
        )
        if primary:
            y = _draw_stat_cols(draw, primary, fonts["bold"], y, x, col2_x, C_STAT_PRIMARY)
            y += LINE_GAP

        # Secondary stats (cyan)
        secondary = [s for s in item.stats if s.stat_group == "secondary"]
        if secondary:
            y = _draw_stat_cols(draw, secondary, fonts["bold"], y, x, col2_x, C_STAT_SECONDARY)
            y += LINE_GAP

        # Item properties block (type, slot, mitigation, level, charges, casting, recast…)
        has_info = any([item.armor_type, item.slot_type, item.mitigation, item.item_level, item.extra_info])
        if has_info:
            y += SECTION_GAP
            if item.armor_type:
                y = _draw_kv(draw, x, y, "Type", item.armor_type, fonts["regular"], value_color=C_WHITE)
            if item.slot_type:
                y = _draw_kv(draw, x, y, "Slot", item.slot_type, fonts["regular"], value_color=C_WHITE)
            if item.mitigation:
                y = _draw_kv(draw, x, y, "Mitigation", str(item.mitigation), fonts["regular"], value_color=C_WHITE)
            if item.item_level is not None:
                y = _draw_kv(draw, x, y, "Level", str(item.item_level), fonts["regular"], value_color=C_STAT_PRIMARY)
            for label, value in item.extra_info:
                y = _draw_kv(draw, x, y, label, value, fonts["regular"], value_color=C_WHITE)
            y += LINE_GAP

        # Class restrictions (word-wrap if the string is too wide)
        if item.classes:
            y += 2
            class_str = _format_classes(item.classes)
            for line in _wrap(class_str, fonts["bold"], content_w):
                draw.text((x, y), line, font=fonts["bold"], fill=C_CLASS)
                y += _lh(fonts["bold"])
            y += SECTION_GAP

        # Effects
        if item.effects:
            q = item.quality.lower() if item.quality else ""
            eff_color = QUALITY_COLORS.get(q, C_EFFECT_NAME)
            eff_glow  = QUALITY_GLOWS.get(q)
            draw.text((x, y), "Effects:", font=fonts["bold"], fill=C_GOLD)
            y += _lh(fonts["bold"]) + LINE_GAP
            for eff in item.effects:
                y = _draw_effect(draw, eff, fonts, y, x, content_w, canvas,
                                 name_color=eff_color, glow_info=eff_glow)

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
    tab: int = _z(110),
    value_color: tuple = C_VALUE,
) -> int:
    draw.text((x, y), label, font=font, fill=C_WHITE)
    draw.text((x + tab, y), value, font=font, fill=value_color)
    return y + _lh(font) + 1


def _draw_effect(
    draw: ImageDraw.ImageDraw,
    eff: ItemEffect,
    fonts: dict,
    y: int,
    x: int,
    content_w: int,
    canvas: Optional[Image.Image] = None,
    name_color: tuple = C_EFFECT_NAME,
    glow_info: Optional[tuple] = EFFECT_GLOW,
) -> int:
    if canvas is not None and glow_info is not None:
        glow_color, glow_radius, glow_passes = glow_info
        _draw_with_glow(canvas, x, y, eff.name, fonts["bold"], name_color, glow_color, glow_radius, glow_passes)
    else:
        draw.text((x, y), eff.name, font=fonts["bold"], fill=name_color)
    y += _lh(fonts["bold"]) + 1

    if eff.trigger:
        for line in _wrap(eff.trigger, fonts["regular"], content_w):
            draw.text((x, y), line, font=fonts["regular"], fill=C_BODY)
            y += _lh(fonts["regular"])
        y += LINE_GAP

    BULLET = "• "
    bullet_w = fonts["regular"].getbbox(BULLET)[2]
    indent_step = _z(12)

    for indent_level, line_text in eff.lines:
        line_x  = x + indent_level * indent_step
        wrap_w  = WIDTH - line_x - PADDING - BORDER_W - _z(1) - bullet_w
        wrapped = _wrap(line_text, fonts["regular"], wrap_w)
        for j, wline in enumerate(wrapped):
            if j == 0:
                draw.text((line_x, y), BULLET + wline, font=fonts["regular"], fill=C_BODY)
            else:
                draw.text((line_x + bullet_w, y), wline, font=fonts["regular"], fill=C_BODY)
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

    # Exact match (handles single groups and All Classes)
    for group_set, group_name in CLASS_GROUPS.items():
        if class_set == group_set:
            return group_name

    # Decompose into archetype groups (e.g. All Priests + All Mages)
    remaining = class_set
    matched: list[str] = []
    for archetype_set, archetype_name in ARCHETYPES:
        if archetype_set <= remaining:
            matched.append(archetype_name)
            remaining -= archetype_set
    if not remaining and matched:
        return ", ".join(matched)

    return ", ".join(sorted(classes))



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

    # Times New Roman first (matches the in-game CSS), Georgia as fallback
    _SERIF_REGULAR = [
        project_fonts / "times.ttf",
        project_fonts / "Times New Roman.ttf",
        Path("C:/Windows/Fonts/times.ttf"),
        Path("/Library/Fonts/Times New Roman.ttf"),
        Path("/System/Library/Fonts/Supplemental/Times New Roman.ttf"),
        # Georgia fallbacks
        project_fonts / "georgia.ttf",
        Path("C:/Windows/Fonts/georgia.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
    ]
    _SERIF_BOLD = [
        project_fonts / "timesbd.ttf",
        project_fonts / "Times New Roman Bold.ttf",
        Path("C:/Windows/Fonts/timesbd.ttf"),
        Path("/Library/Fonts/Times New Roman Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf"),
        # Georgia fallbacks
        project_fonts / "georgiab.ttf",
        Path("C:/Windows/Fonts/georgiab.ttf"),
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
        "name":    _load(bold,    _z(20)),
        "bold_lg": _load(bold,    _z(16)),
        "bold":    _load(bold,    _z(14)),
        "regular": _load(regular, _z(13)),
        "small":   _load(regular, _z(12)),
    }
