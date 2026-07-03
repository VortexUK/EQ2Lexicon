from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "AAs"
ICONS_DIR = DATA_DIR / "icons"
_TREES_DIR = DATA_DIR / "trees"
BG_PATH = DATA_DIR / "background.jpg"
BG_CLASS = DATA_DIR / "bg_class.png"
BG_SUBCLASS = DATA_DIR / "bg_subclass.png"
BG_SHADOWS = DATA_DIR / "bg_shadows.png"
BG_SPRITE = DATA_DIR / "bg_sprite.png"

# Backdrop sprite sheet: 7 large (44×44) sprites in order, separated by 1px gaps
# IDs map to x-offsets in the sheet
_BACKDROP_X: dict[int, int] = {-1: 0, 456: 45, 457: 90, 458: 135, 459: 180, 460: 225, 461: 270}
_BACKDROP_NATIVE = 44  # sprite size at native scale
_backdrop_sheet: Image.Image | None = None

# Badge sprites (small 24×24 in the same sheet, after the large ones)
_BADGE_YELLOW_X = 340  # not maxed
_BADGE_GREEN_X = 365  # maxed
_BADGE_NATIVE = 24
BADGE_SIZE = 32  # output pixels


def _get_backdrop(backdrop_id: int) -> Image.Image | None:
    global _backdrop_sheet
    if _backdrop_sheet is None:
        _backdrop_sheet = Image.open(BG_SPRITE).convert("RGBA")
    x = _BACKDROP_X.get(backdrop_id)
    if x is None:
        return None
    sprite = _backdrop_sheet.crop((x, 0, x + _BACKDROP_NATIVE, _BACKDROP_NATIVE))
    d = NODE_R * 2
    sprite = sprite.resize((d, d), Image.LANCZOS)
    # Clip to circle
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
    result = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    result.paste(sprite, (0, 0), mask)
    return result


# Native grid calibration (640×480 base)
# x columns for xcoord 1,4,7,10,13
_COL_X = {1: 86, 4: 206, 7: 327, 10: 447, 13: 567}
# y rows for ycoord 0-6
_BASE_Y = 42
_ROW_H = (442 - 42) / 6  # ≈ 66.67 px per row at native scale
_ROW_Y = {y: round(_BASE_Y + y * _ROW_H) for y in range(7)}

SCALE = 2  # render at 2× then no downscale (keep crisp)
NODE_R = 44  # node radius in output pixels
IMG_W = 640 * SCALE
IMG_H = 480 * SCALE


def _px(xcoord: int, ycoord: int) -> tuple[int, int]:
    return _COL_X[xcoord] * SCALE, _ROW_Y[ycoord] * SCALE


def _circle_icon(icon_id: int, backdrop_id: int = -1) -> Image.Image | None:
    path = ICONS_DIR / f"{icon_id}.png"
    if not path.exists():
        return None
    icon = Image.open(path).convert("RGBA")

    d = NODE_R * 2
    icon = icon.resize((d, d), Image.LANCZOS)

    # Circular clip mask
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)

    # Clip icon to circle so its transparent areas remain transparent
    icon_clipped = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    icon_clipped.paste(icon, (0, 0), mask)

    # Composite: backdrop first, then icon on top (respects icon's own alpha)
    backdrop = _get_backdrop(backdrop_id) or _get_backdrop(-1)
    result = backdrop.copy() if backdrop else Image.new("RGBA", (d, d), (0, 0, 0, 255))
    result = Image.alpha_composite(result, icon_clipped)

    draw = ImageDraw.Draw(result)
    bw = max(3, NODE_R // 12)

    # Outer dark ring
    draw.ellipse((0, 0, d - 1, d - 1), outline=(20, 12, 4, 220), width=bw + 1)
    # Gold ring
    draw.ellipse((bw, bw, d - bw - 1, d - bw - 1), outline=(210, 175, 80, 255), width=bw)

    return result


def _placeholder_node() -> Image.Image:
    d = NODE_R * 2
    img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, d - 1, d - 1), fill=(50, 50, 60, 200), outline=(180, 150, 60, 255), width=4)
    return img


def _make_badge(tier: int, maxed: bool) -> Image.Image:
    sheet = Image.open(BG_SPRITE).convert("RGBA")
    sx = _BADGE_GREEN_X if maxed else _BADGE_YELLOW_X
    sprite = sheet.crop((sx, 0, sx + _BADGE_NATIVE, _BADGE_NATIVE))
    badge = sprite.resize((BADGE_SIZE, BADGE_SIZE), Image.LANCZOS)

    font = ImageFont.load_default(size=max(10, BADGE_SIZE // 2))
    draw = ImageDraw.Draw(badge)
    text = str(tier)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (BADGE_SIZE - tw) // 2 - bbox[0]
    ty = (BADGE_SIZE - th) // 2 - bbox[1]
    draw.text((tx + 1, ty + 1), text, font=font, fill=(0, 0, 0, 200))
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))
    return badge


def detect_tree_type(tree_data: dict) -> str:
    """Return a string key identifying the structural type of an AA tree."""
    tree = tree_data["alternateadvancement_list"][0]
    nodes = tree["alternateadvancementnode_list"]
    ofy = tree.get("ofyclassification", "")
    node_classes = {n.get("classification", "") for n in nodes}
    xs = {n["xcoord"] for n in nodes}
    ys = {n["ycoord"] for n in nodes}

    if xs == {1, 4, 7, 10, 13}:
        return "class"
    if ofy == "Expertise" and max(ys) == 19:
        return "subclass"
    if xs == {0, 6, 12, 18, 24, 30, 38, 42}:
        return "shadows"
    if "Heroic" in node_classes:
        return "heroic"
    if "Crafting Expertise" in node_classes:
        return "tradeskill"
    if xs == {3, 7, 11, 18, 22, 26, 33, 37, 41}:
        return "tradeskill_general"
    if "Warder Primals" in node_classes:
        return "warder"
    if ofy in ("Prestige Expertise", "Conversion") and "Prestige" in node_classes:
        return "prestige"
    if xs == {1, 5, 9, 13} and max(ys) == 4:
        return "dragon"
    if "Reign of Shadows" in node_classes:
        return "reign_of_shadows"
    if xs == {5, 13, 21, 29, 37}:
        return "far_seas"
    return "unknown"


@lru_cache(maxsize=1)
def load_tree_index() -> dict[int, dict[str, str]]:
    """Return {tree_id: {"name": str, "type": str}} parsed from data/AAs/trees/*.json.

    Single source of truth for both the web AA route and the bot /aacheck cog.
    Cached at first call — tree JSON is static reference data; restart to refresh.

    BE-204: logs at WARNING on per-file parse failure so a corrupt JSON doesn't
    silently disappear from the index.
    """
    out: dict[int, dict[str, str]] = {}
    for path in _TREES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            aa_list = data.get("alternateadvancement_list") or []
            if aa_list:
                tid = int(path.stem)
                out[tid] = {
                    "name": aa_list[0].get("name", path.stem),
                    "type": detect_tree_type(data),
                }
        except Exception:
            _log.exception("[aa-tree] Failed to load tree index %s", path.name)
    return out


@lru_cache(maxsize=256)
def tree_node_costs(tree_id: int) -> dict[int, int]:
    """Return {node_id: pointspertier} for a tree — the per-tier AA point cost of
    each node (most are 1, some endline nodes are 2). Used to compute a
    character's *point* spend (tier × cost) rather than a bare tier count.
    Missing/unparseable tree → empty dict (callers default to 1). Cached; tree
    JSON is static reference data (restart to refresh)."""
    path = _TREES_DIR / f"{tree_id}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        nodes = (data.get("alternateadvancement_list") or [{}])[0].get("alternateadvancementnode_list") or []
        return {int(n["nodeid"]): int(n.get("pointspertier", 1)) for n in nodes if "nodeid" in n}
    except Exception:
        _log.exception("[aa-tree] Failed to load node costs %s", path.name)
        return {}


@lru_cache(maxsize=256)
def tree_max_points(tree_id: int) -> int:
    """Return the tree's fully-maxed point total: Σ (maxtier × pointspertier) over
    its nodes. Used to derive the tradeskill AA cap from the data. Cached."""
    path = _TREES_DIR / f"{tree_id}.json"
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        nodes = (data.get("alternateadvancement_list") or [{}])[0].get("alternateadvancementnode_list") or []
        return sum(int(n.get("maxtier", 0)) * int(n.get("pointspertier", 1)) for n in nodes)
    except Exception:
        _log.exception("[aa-tree] Failed to compute max points %s", path.name)
        return 0


def render_aa_tree(tree_id: int, aa_data: dict[int, int] | None = None) -> Image.Image:
    tree_path = DATA_DIR / "trees" / f"{tree_id}.json"
    with tree_path.open(encoding="utf-8") as f:
        data = json.load(f)
    tree = data["alternateadvancement_list"][0]
    nodes = tree["alternateadvancementnode_list"]

    bg = Image.open(BG_PATH).convert("RGBA").resize((IMG_W, IMG_H), Image.LANCZOS)

    bg_class = Image.open(BG_CLASS).convert("RGBA")
    pixels = bg_class.load()
    for y in range(bg_class.height):
        for x in range(bg_class.width):
            r, g, b, a = pixels[x, y]
            if r > 210 and g > 210 and b > 210:
                pixels[x, y] = (r, g, b, 0)
    bg_class = bg_class.resize((IMG_W, IMG_H), Image.LANCZOS)

    canvas = Image.alpha_composite(bg, bg_class)
    _draw_nodes(canvas, nodes, _px, aa_data)
    return canvas.convert("RGB")


# ---------------------------------------------------------------------------
# Subclass tree renderer
# ---------------------------------------------------------------------------
# Pixel mapping derived from bg_subclass.png bow-tie connector analysis:
#   connector centres at native-scale x=234, 311, 389 correspond to xcoords 15, 21, 27
#   step = (389-234)/(27-15) = 155/12 ≈ 12.917 px per xcoord unit at native scale
# Y mapping shares the same top/bottom anchors as the class tree (y=0 → 42px, y=19 → 442px)
_SUB_ANCHOR_X = 234  # native-scale pixel x for xcoord 15
_SUB_ANCHOR_XC = 15  # reference xcoord
_SUB_STEP_X = 155 / 12  # native-scale px per xcoord unit
_SUB_BASE_Y = 42  # native-scale pixel y for ycoord 0
_SUB_STEP_Y = (442 - 42) / 19  # native-scale px per ycoord unit (~21.05)


def _sub_px(xcoord: int, ycoord: int) -> tuple[int, int]:
    x = round((_SUB_ANCHOR_X + (xcoord - _SUB_ANCHOR_XC) * _SUB_STEP_X) * SCALE)
    y = round((_SUB_BASE_Y + ycoord * _SUB_STEP_Y) * SCALE)
    return x, y


def render_subclass_tree(tree_id: int, aa_data: dict[int, int] | None = None) -> Image.Image:
    tree_path = DATA_DIR / "trees" / f"{tree_id}.json"
    with tree_path.open(encoding="utf-8") as f:
        data = json.load(f)
    tree = data["alternateadvancement_list"][0]
    nodes = tree["alternateadvancementnode_list"]

    bg = Image.open(BG_PATH).convert("RGBA").resize((IMG_W, IMG_H), Image.LANCZOS)
    bg_sub = Image.open(BG_SUBCLASS).convert("RGBA").resize((IMG_W, IMG_H), Image.LANCZOS)
    canvas = Image.alpha_composite(bg, bg_sub)

    _draw_nodes(canvas, nodes, _sub_px, aa_data)
    return canvas.convert("RGB")


# ---------------------------------------------------------------------------
# Shadows tree renderer
# ---------------------------------------------------------------------------
# bg_shadows.png is 632×472.  Arrow x-peaks measured from connector bands:
#   xcoord → native_x = 40 + xcoord * 13
# Node rows sit 55 px above each connector band centre (206, 313, 417):
#   ycoord → native_y:  1→44, 6→151, 11→258, 16→362
_SHADOWS_NATIVE_W = 632
_SHADOWS_NATIVE_H = 472
_SHADOWS_ANCHOR_X = 40  # native x for xcoord 0
_SHADOWS_STEP_X = 13  # native px per xcoord unit
_SHADOWS_ROW_Y = {1: 59, 6: 166, 11: 273, 16: 377}  # native y per ycoord


def _shadows_px(xcoord: int, ycoord: int) -> tuple[int, int]:
    x = round((_SHADOWS_ANCHOR_X + xcoord * _SHADOWS_STEP_X) * IMG_W / _SHADOWS_NATIVE_W)
    y = round(_SHADOWS_ROW_Y[ycoord] * IMG_H / _SHADOWS_NATIVE_H)
    return x, y


def _draw_nodes(
    canvas: Image.Image,
    nodes: list[dict],
    px_fn,
    aa_data: dict[int, int] | None = None,
) -> None:
    for node in nodes:
        px, py = px_fn(node["xcoord"], node["ycoord"])
        icon = node.get("icon") or {}
        icon_id = icon.get("id", -1)
        backdrop_id = int(icon.get("backdrop", -1))
        node_img = _circle_icon(int(icon_id), backdrop_id) if icon_id and icon_id > 0 else _placeholder_node()
        if node_img:
            canvas.paste(node_img, (px - NODE_R, py - NODE_R), node_img)

        if aa_data is not None:
            node_id = node.get("nodeid")
            tier = aa_data.get(node_id, 0) if node_id is not None else 0
            if tier > 0:
                max_tier = node.get("maxtier", 1) or 1
                badge = _make_badge(tier, tier >= max_tier)
                bx = px + NODE_R - BADGE_SIZE // 2
                by = py + NODE_R - BADGE_SIZE // 2
                canvas.paste(badge, (bx, by), badge)


def render_shadows_tree(tree_id: int, aa_data: dict[int, int] | None = None) -> Image.Image:
    tree_path = DATA_DIR / "trees" / f"{tree_id}.json"
    with tree_path.open(encoding="utf-8") as f:
        data = json.load(f)
    nodes = data["alternateadvancement_list"][0]["alternateadvancementnode_list"]

    bg = Image.open(BG_PATH).convert("RGBA").resize((IMG_W, IMG_H), Image.LANCZOS)
    bg_shad = Image.open(BG_SHADOWS).convert("RGBA").resize((IMG_W, IMG_H), Image.LANCZOS)
    canvas = Image.alpha_composite(bg, bg_shad)

    _draw_nodes(canvas, nodes, _shadows_px, aa_data)
    return canvas.convert("RGB")


# ---------------------------------------------------------------------------
# Tradeskill tree renderer  (no overlay — dark background only)
# ---------------------------------------------------------------------------
# xcoords 2–41 (same range as heroic), ycoords 1, 8, 15  (3 rows)
_TS_BASE_X = 65  # native x for xcoord 2  (shared with heroic)
_TS_STEP_X = 13  # native px per xcoord unit
_TS_BASE_Y = 60  # native y for ycoord 1
_TS_STEP_Y = 21  # native px per ycoord unit


def _ts_px(xcoord: int, ycoord: int) -> tuple[int, int]:
    x = round((_TS_BASE_X + (xcoord - 2) * _TS_STEP_X) * SCALE)
    y = round((_TS_BASE_Y + (ycoord - 1) * _TS_STEP_Y) * SCALE)
    return x, y


def render_tradeskill_tree(tree_id: int, aa_data: dict[int, int] | None = None) -> Image.Image:
    tree_path = DATA_DIR / "trees" / f"{tree_id}.json"
    with tree_path.open(encoding="utf-8") as f:
        data = json.load(f)
    nodes = data["alternateadvancement_list"][0]["alternateadvancementnode_list"]

    bg = Image.open(BG_PATH).convert("RGBA").resize((IMG_W, IMG_H), Image.LANCZOS)
    canvas = bg.copy()

    _draw_nodes(canvas, nodes, _ts_px, aa_data)
    return canvas.convert("RGB")


# ---------------------------------------------------------------------------
# Heroic tree renderer  (no overlay — dark background only)
# ---------------------------------------------------------------------------
# xcoords 2–41, ycoords 1–16.  Calibrated against example image layout.
# pixel = base + (coord - min_coord) * step   (native 640×480 space)
_HEROIC_BASE_X = 65  # native x for xcoord 2
_HEROIC_STEP_X = 13  # native px per xcoord unit
_HEROIC_BASE_Y = 50  # native y for ycoord 1
_HEROIC_STEP_Y = 22  # native px per ycoord unit


def _heroic_px(xcoord: int, ycoord: int) -> tuple[int, int]:
    x = round((_HEROIC_BASE_X + (xcoord - 2) * _HEROIC_STEP_X) * SCALE)
    y = round((_HEROIC_BASE_Y + (ycoord - 1) * _HEROIC_STEP_Y) * SCALE)
    return x, y


def render_heroic_tree(tree_id: int, aa_data: dict[int, int] | None = None) -> Image.Image:
    tree_path = DATA_DIR / "trees" / f"{tree_id}.json"
    with tree_path.open(encoding="utf-8") as f:
        data = json.load(f)
    nodes = data["alternateadvancement_list"][0]["alternateadvancementnode_list"]

    bg = Image.open(BG_PATH).convert("RGBA").resize((IMG_W, IMG_H), Image.LANCZOS)
    canvas = bg.copy()

    _draw_nodes(canvas, nodes, _heroic_px, aa_data)
    return canvas.convert("RGB")


# ---------------------------------------------------------------------------
# Unified entry point — picks renderer based on detected tree type
# ---------------------------------------------------------------------------
_RENDERERS = {
    "class": render_aa_tree,
    "subclass": render_subclass_tree,
    # remaining types share the subclass coordinate system until their own
    # backgrounds/layouts are calibrated; fall back to subclass renderer
    "shadows": render_shadows_tree,
    "heroic": render_heroic_tree,
    "tradeskill": render_tradeskill_tree,
    "tradeskill_general": render_subclass_tree,
    "warder": render_subclass_tree,
    "prestige": render_subclass_tree,
    "dragon": render_subclass_tree,
    "reign_of_shadows": render_subclass_tree,
    "far_seas": render_subclass_tree,
}


def render_tree(
    tree_id: int,
    aa_data: dict[int, int] | None = None,
) -> tuple[Image.Image, str]:
    """Render any AA tree, returning (image, tree_type_key)."""
    tree_path = DATA_DIR / "trees" / f"{tree_id}.json"
    with tree_path.open(encoding="utf-8") as f:
        data = json.load(f)
    tree_type = detect_tree_type(data)
    renderer = _RENDERERS.get(tree_type, render_subclass_tree)
    return renderer(tree_id, aa_data), tree_type
