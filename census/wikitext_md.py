"""
MediaWiki wikitext → Markdown converter.

Used by the EQ2i raid-strategies scraper (``scripts/dev/scrape_eq2i_raids.py``)
and later by the future editor's preview pane. Pure function — no
network, no DB; given a wikitext string returns a markdown string.

Scope is "best-effort for the EQ2 wiki" rather than "complete MediaWiki
parser". Handles the constructs that actually appear in EQ2i raid
pages, falls back to readable placeholders for anything unknown.

Known constructs handled:

  * **Headings**: ``==Sec==`` / ``===Sub===`` → ``## Sec`` / ``### Sub``.
  * **Wikilinks**: ``[[Foo]]`` and ``[[Foo|bar]]`` → ``[bar](URL)``
    where URL is built from ``WIKI_BASE_URL`` + the target slug.
  * **External links**: ``[https://x display]`` → ``[display](https://x)``.
  * **Templates**: see ``_render_template`` — special-cased for the
    EQ2-specific ones we see in raid pages (``{{Monster}}``, ``{{loc}}``,
    ``{{IZoneInformation}}``, ``{{NPC}}``, etc.). Unknown templates
    drop to plain-text on best-effort.
  * **HTML-ish tags**: ``<br>``/``<br/>`` → newline; ``<small>``/``<big>``
    strip the tag but keep content; ``<ref>...</ref>`` is dropped
    (footnotes don't render usefully without the references list).
  * **Lists**: ``* item`` and ``# item`` pass through as markdown
    ``- item`` / ``1. item`` (mwparserfromhell leaves these in the
    text stream, so the conversion happens line-by-line at the end).
  * **Bold/italic**: ``'''bold'''`` → ``**bold**``, ``''italic''`` →
    ``*italic*``.
  * **HTML entities**: passed through (``&nbsp;`` etc.).
  * **Comments**: dropped.
  * **Images**: dropped — we can't render them inline and they'd just
    clutter the markdown.
"""

from __future__ import annotations

import re
from typing import Any

import mwparserfromhell as mwp
from mwparserfromhell.nodes import (
    Comment,
    ExternalLink,
    Heading,
    HTMLEntity,
    Tag,
    Template,
    Text,
    Wikilink,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Base URL the converter assumes when resolving [[Wikilinks]]. The
# raids scraper points this at EQ2i; if we ever convert wikitext from
# another wiki the caller can override via convert(...).
WIKI_BASE_URL = "https://eq2.fandom.com/wiki/"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def convert(wikitext: str, wiki_base_url: str = WIKI_BASE_URL) -> str:
    """Convert ``wikitext`` to markdown.

    Returns a string with at most one blank line between paragraphs
    and trailing whitespace stripped.
    """
    if not wikitext:
        return ""
    code = mwp.parse(wikitext)
    out = _render(code.nodes, wiki_base_url)
    return _strip_leading_list_space(_collapse_blanks(out)).strip()


# ---------------------------------------------------------------------------
# Node rendering
# ---------------------------------------------------------------------------


def _render(nodes: list[Any], base_url: str) -> str:
    """Render a node sequence to markdown.

    Special-cases consecutive ``li`` tags so wiki nested lists work:
    ``**`` in source parses as TWO ``Tag(li, wiki_markup='*')`` in a
    row (not one ``li`` with markup ``**``). We treat the run-length
    as the nesting depth and emit a single markdown bullet at that
    depth — otherwise we'd render ``- -`` for the second-level marker.
    """
    parts: list[str] = []
    i = 0
    while i < len(nodes):
        node = nodes[i]
        if isinstance(node, Tag) and str(node.tag).lower() == "li":
            # Collapse the consecutive li run
            depth = 0
            last_markup = "*"
            while i < len(nodes) and isinstance(nodes[i], Tag) and str(nodes[i].tag).lower() == "li":
                depth += 1
                last_markup = str(nodes[i].wiki_markup or "*")
                i += 1
            indent = "  " * (depth - 1)
            marker = "1." if last_markup[-1] == "#" else "-"
            parts.append(f"{indent}{marker} ")
            continue
        parts.append(_render_node(node, base_url))
        i += 1
    return "".join(parts)


def _render_node(node: Any, base_url: str) -> str:
    if isinstance(node, Text):
        # Plain text. Strip MediaWiki bold/italic on this segment.
        return _bold_italic(str(node))

    if isinstance(node, Heading):
        # mwparserfromhell heading.level is the count of `=` chars.
        # MediaWiki: level=1 is the page title (==), level=2 is a top
        # section (==Sec==). Map to markdown headings of the SAME level
        # — that preserves the wiki's intended hierarchy and avoids
        # an h1 collision with whatever page title the renderer adds.
        title = _render(node.title.nodes, base_url).strip()
        level = max(2, min(node.level, 6))
        return f"\n\n{'#' * level} {title}\n\n"

    if isinstance(node, Wikilink):
        target = str(node.title).strip()
        display = _render(node.text.nodes, base_url).strip() if node.text is not None else target
        # File:/Image: links → drop (we'd need to actually fetch + host
        # the image to render it usefully; for now strip cleanly).
        if target.lower().startswith(("file:", "image:")):
            return ""
        # Category: links → drop (they're page metadata, not body content)
        if target.lower().startswith("category:"):
            return ""
        url = base_url + _slugify_target(target)
        return f"[{display}]({url})"

    if isinstance(node, ExternalLink):
        url = str(node.url).strip()
        display = _render(node.title.nodes, base_url).strip() if node.title is not None else url
        return f"[{display}]({url})"

    if isinstance(node, Template):
        return _render_template(node, base_url)

    if isinstance(node, Tag):
        return _render_tag(node, base_url)

    if isinstance(node, HTMLEntity):
        # `&nbsp;` etc. — pass through, markdown renderers handle them.
        return str(node)

    if isinstance(node, Comment):
        return ""

    # Anything else — render as text. Better than dropping.
    return str(node)


# ---------------------------------------------------------------------------
# Template handling (most EQ2-specific behaviour lives here)
# ---------------------------------------------------------------------------

# Templates we want to drop entirely — typically infobox-like things
# that the scraper extracts as structured data separately.
_DROP_TEMPLATES: frozenset[str] = frozenset(
    name.lower()
    for name in (
        "IZoneInformation",
        "OZoneInformation",
        "ZoneInformation",
        "Stub",
        "Stub-Information",
        "Stub-Strategy",
        "Disambig",
        "Cleanup",
        "RaidInformation",
        "RaidEncounter",
        "RaidLoot",
        "TOC",
        "Clear",
    )
)


def _render_template(node: Template, base_url: str) -> str:
    name = str(node.name).strip()
    lower = name.lower()

    if lower in _DROP_TEMPLATES:
        return ""

    # {{Monster|key|display}} → display (or key if no display)
    if lower in ("monster", "npc"):
        display = _template_arg(node, 2) or _template_arg(node, 1) or ""
        return display

    # {{loc|x,y,z}} → (x, y, z)
    if lower == "loc":
        # Args can be either positional ("x,y,z" all in arg1) or
        # individually positional (x=arg1, y=arg2, z=arg3).
        a1 = _template_arg(node, 1) or ""
        if "," in a1:
            return f"({a1.replace(' ', '')})"
        a2 = _template_arg(node, 2) or ""
        a3 = _template_arg(node, 3) or ""
        if a1 and a2 and a3:
            return f"({a1}, {a2}, {a3})"
        return f"({a1})" if a1 else ""

    # {{Item|name}} / {{Spell|name}} → just the name; the EQ2i renderer
    # would link them but we'd need a URL slug we don't reliably have.
    if lower in ("item", "spell", "ability", "quest"):
        return _template_arg(node, 1) or ""

    # Generic fallback: if there's a first positional arg that looks
    # like display text, use it. Otherwise drop (better than the raw
    # `{{Foo|...}}` syntax leaking into the markdown).
    first = _template_arg(node, 1)
    if first and len(first) < 80 and "\n" not in first:
        return first
    return ""


def _template_arg(node: Template, key: int | str) -> str | None:
    """Resolve a template positional or named argument to a stripped
    text value. Returns None when absent."""
    try:
        if not node.has(key):
            return None
        return str(node.get(key).value).strip()
    except (KeyError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Tag handling
# ---------------------------------------------------------------------------


def _render_tag(node: Tag, base_url: str) -> str:
    tag = str(node.tag).lower()
    if tag in ("br", "br/"):
        return "\n"
    if tag == "li":
        # Wiki list marker. mwparserfromhell parses `* item` / `# item`
        # as a self-closing li Tag whose wiki_markup is the marker
        # ('*' or '#'), followed by Text nodes for the item content.
        # Emit the markdown bullet directly here — going through a
        # line-based post-processor would collide with markdown ATX
        # headings (`## Title` and `# item` look identical to a regex).
        # Nesting: wiki uses `**` / `##` for sub-items; markdown uses
        # 2-space indent per level beyond the first.
        markup = str(node.wiki_markup or "*")
        level = max(1, len(markup))
        indent = "  " * (level - 1)
        marker = "1." if markup[-1] == "#" else "-"
        return f"{indent}{marker} "
    if tag == "dt":
        # Definition-list term: render as bold on its own line
        return "**"
    if tag == "dd":
        # Definition-list description: indent
        return "  "
    if tag in ("ref",):
        # Footnote — drop (no references-list rendering)
        return ""
    if tag in ("hr",):
        return "\n\n---\n\n"
    if tag in ("nowiki",):
        # Treat as literal text
        return str(node.contents) if node.contents else ""

    # Block-ish wrappers we can just strip: keep contents, drop tag.
    if tag in (
        "small",
        "big",
        "span",
        "div",
        "center",
        "p",
        "pre",
        "code",
        "u",
        "strike",
        "s",
        "sup",
        "sub",
        "abbr",
    ):
        if node.contents is None:
            return ""
        return _render(node.contents.nodes, base_url)

    # Bold/italic via HTML tags
    if tag in ("b", "strong"):
        inner = _render(node.contents.nodes, base_url) if node.contents else ""
        return f"**{inner}**" if inner else ""
    if tag in ("i", "em"):
        inner = _render(node.contents.nodes, base_url) if node.contents else ""
        return f"*{inner}*" if inner else ""

    # Tables — too complex to convert cleanly. Drop with a placeholder
    # so a human knows something was elided.
    if tag in ("table", "tr", "td", "th"):
        return ""

    # Default: render contents, drop the tag
    if node.contents is None:
        return ""
    return _render(node.contents.nodes, base_url)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_BOLD_RE = re.compile(r"'''(.+?)'''", flags=re.DOTALL)
_ITALIC_RE = re.compile(r"''(.+?)''", flags=re.DOTALL)


def _bold_italic(s: str) -> str:
    """Convert ''italic'' and '''bold''' wikitext to markdown."""
    s = _BOLD_RE.sub(r"**\1**", s)
    s = _ITALIC_RE.sub(r"*\1*", s)
    return s


def _slugify_target(target: str) -> str:
    """Wiki link target → URL slug. MediaWiki convention: spaces become
    underscores, the first character is capitalised. The wiki actually
    accepts either spaced or underscored URLs (it normalises), so the
    underscore form is the safe canonical."""
    t = target.strip()
    # Strip leading colons used to suppress link rendering (e.g. [[:Category:Foo]])
    while t.startswith(":"):
        t = t[1:]
    # Drop any URL fragment / section anchor — we'd need more context
    # to map it correctly and a raw `#Section` works on Fandom URLs.
    return t.replace(" ", "_")


# ---------------------------------------------------------------------------
# Post-processing — paragraph + list normalisation
# ---------------------------------------------------------------------------


# Matches a list marker (with optional leading indent for nested lists)
# followed by 2+ spaces. Group 1 captures the indent+marker so we can
# re-emit it with a single trailing space.
_LIST_LEADIN_RE = re.compile(r"^(\s*(?:[-*]|\d+\.))\s\s+", flags=re.MULTILINE)


def _strip_leading_list_space(text: str) -> str:
    """Tidy double-space after a list marker.

    The li-tag handler emits ``- `` / ``1. ``; mwparserfromhell's
    following Text node often starts with a space (the source wikitext
    had ``* item`` with a space after the marker that we preserved).
    Result: ``-  item`` (also ``  -  sub-item`` for nested). Collapse
    to a single space after the marker.
    """
    return _LIST_LEADIN_RE.sub(lambda m: m.group(1) + " ", text)


_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _collapse_blanks(text: str) -> str:
    """Collapse runs of 3+ newlines down to 2 (one blank line)."""
    return _MULTI_BLANK_RE.sub("\n\n", text)
