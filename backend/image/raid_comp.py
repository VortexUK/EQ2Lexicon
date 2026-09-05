"""PIL renderer for the /raidcomp Discord card.

A dark, gold-accented composition card matching the site's palette: header
with guild + starting zone, a 2x2 grid of group boxes with class-coloured
member names, and an optional sitout strip. Rendered at 2x and downsampled
(the tooltip renderer's approach) so text stays crisp in Discord.

Pure-data in: ``groups`` is a list of up to 4 lists of member dicts
{name, cls, colour} (colour = '#rrggbb' from classes.db, None -> muted);
``sitout`` the same shape. Assembly from planner placements happens in the
cog's pure helper — this module only draws.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCALE = 2

# Site palette (frontend/src/index.css tokens, hardcoded — PIL can't read CSS)
_BG = (18, 15, 11)
_PANEL = (28, 24, 18)
_PANEL_EDGE = (74, 62, 40)
_GOLD = (200, 164, 82)
_GOLD_DIM = (140, 116, 62)
_TEXT = (232, 224, 208)
_MUTED = (150, 140, 122)

_GROUP_W, _GROUP_H = 300, 196
_PAD = 24
_SLOT_H = 26


def _z(n: float) -> int:
    return int(n * SCALE)


def _load_font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    project_fonts = Path("fonts")
    names = ["timesbd.ttf", "georgiab.ttf"] if bold else ["times.ttf", "georgia.ttf"]
    candidates = [project_fonts / n for n in names] + [Path("C:/Windows/Fonts") / n for n in names]
    candidates += [
        Path(
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"
        ),
        Path(
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
        ),
    ]
    for p in candidates:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10
        return ImageFont.load_default()


def _hex_rgb(colour: str | None) -> tuple[int, int, int]:
    if not colour or not colour.startswith("#") or len(colour) != 7:
        return _MUTED
    try:
        return tuple(int(colour[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    except ValueError:
        return _MUTED


def render_raid_comp(
    guild_name: str,
    zone_name: str,
    groups: list[list[dict]],
    sitout: list[dict],
    *,
    team_name: str | None = None,
    date_str: str | None = None,
) -> Image.Image:
    cols = 2
    grid_rows = 2
    width = _PAD * 2 + cols * _GROUP_W + (cols - 1) * 16
    sitout_h = 64 if sitout else 0
    height = 118 + grid_rows * _GROUP_H + 16 + sitout_h + _PAD

    img = Image.new("RGB", (_z(width), _z(height)), _BG)
    draw = ImageDraw.Draw(img)

    f_title = _load_font(True, _z(26))
    f_sub = _load_font(False, _z(16))
    f_group = _load_font(True, _z(15))
    f_name = _load_font(True, _z(14))
    f_cls = _load_font(False, _z(12))

    # ── header ──
    title = f"{guild_name} — Raid Composition"
    draw.text((_z(_PAD), _z(20)), title, font=f_title, fill=_GOLD)
    sub_bits = [f"Starting zone: {zone_name}"]
    if team_name:
        sub_bits.append(team_name)
    if date_str:
        sub_bits.append(date_str)
    draw.text((_z(_PAD), _z(58)), "   ·   ".join(sub_bits), font=f_sub, fill=_TEXT)
    draw.line([(_z(_PAD), _z(88)), (_z(width - _PAD), _z(88))], fill=_GOLD_DIM, width=SCALE)

    # ── group boxes ──
    top = 104
    for gi in range(4):
        gx = _PAD + (gi % cols) * (_GROUP_W + 16)
        gy = top + (gi // cols) * (_GROUP_H + 16)
        draw.rounded_rectangle(
            [(_z(gx), _z(gy)), (_z(gx + _GROUP_W), _z(gy + _GROUP_H))],
            radius=_z(8),
            fill=_PANEL,
            outline=_PANEL_EDGE,
            width=SCALE,
        )
        members = groups[gi] if gi < len(groups) else []
        draw.text((_z(gx + 12), _z(gy + 8)), f"Group {gi + 1}", font=f_group, fill=_GOLD)
        draw.text(
            (_z(gx + _GROUP_W - 12), _z(gy + 10)),
            f"{len(members)}/6",
            font=f_cls,
            fill=_MUTED,
            anchor="ra",
        )
        for si in range(6):
            sy = gy + 34 + si * _SLOT_H
            if si < len(members):
                m = members[si]
                draw.text((_z(gx + 14), _z(sy)), m["name"], font=f_name, fill=_hex_rgb(m.get("colour")))
                if m.get("cls"):
                    draw.text((_z(gx + _GROUP_W - 14), _z(sy + 2)), m["cls"], font=f_cls, fill=_MUTED, anchor="ra")
            else:
                draw.text((_z(gx + 14), _z(sy)), "—", font=f_cls, fill=(70, 62, 50))

    # ── sitout strip ──
    if sitout:
        sy = top + grid_rows * _GROUP_H + 16 + 8
        draw.text((_z(_PAD), _z(sy)), "Sitting out:", font=f_group, fill=_MUTED)
        names = "   ".join(m["name"] for m in sitout)
        draw.text((_z(_PAD + 104), _z(sy + 1)), names, font=f_name, fill=_TEXT)

    # ── footer ──
    draw.text((_z(width - _PAD), _z(height - 22)), "EQ2Lexicon", font=f_cls, fill=_GOLD_DIM, anchor="ra")

    return img.resize((int(width * 1.3), int(height * 1.3)), Image.LANCZOS)
