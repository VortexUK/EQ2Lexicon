"""Guild recruitment API — public read, officer-or-admin write.

A guild's recruitment profile (needed classes, tags, description, in-game
contacts, Discord invite, 200x200 logo) plus the world-wide "Guilds
Recruiting" listing. Profiles are keyed by CENSUS GUILD ID in the store
(rename-proof — see backend/server/db/guild_recruitment.py); routes stay
name-addressed like the rest of the guild API, and every officer save
re-resolves the id and refreshes the stored name.

Write gate mirrors raid_schedule.py (`_officer_chars`, admin override).
Every free-text field is sanitised + blocklist-screened; a hit is rejected
AND reported via audit_log. Logo uploads are base64 JSON (no multipart
anywhere in the app), re-encoded server-side through PIL to a <=200px WebP
— re-encoding destroys EXIF/polyglot payloads — and every upload/removal
is audited with the actor's discord id (the user's abuse-trail
requirement).
"""

from __future__ import annotations

import base64
import binascii
import io
import logging

from fastapi import APIRouter, HTTPException, Request, Response
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from backend.census.constants import ALL_CLASSES
from backend.census.store import store as census_store
from backend.server.api.guild import _officer_chars, _roster_rank_map, _validate_guild_name
from backend.server.auth_deps import is_admin
from backend.server.core.audit_log import audit_log
from backend.server.core.census_lifecycle import shared_census_client
from backend.server.core.executor import run_sync
from backend.server.core.session_user import SessionUser
from backend.server.core.text_moderation import contains_blocked_term, sanitize_text
from backend.server.core.validation import DISCORD_INVITE_RE, validate_character_name
from backend.server.db import get_display_names_for_discord_ids
from backend.server.db.guild_recruitment import store as recruitment_db
from backend.server.limiter import limiter, upload_rate_key
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["guild"])

#: The curated tag set (user-approved 2026-10-03). Single source of truth —
#: served to the frontend as `available_tags` on profile + list responses;
#: never duplicate this list in the frontend.
RECRUITMENT_TAGS: tuple[str, ...] = (
    "casual",
    "core",
    "hardcore",
    "raiding",
    "questing",
    "leveling",
    "social",
    "crafting",
    "roleplay",
    "family-friendly",
    "eu-friendly",
    "late-night",
)
_TAG_ORDER = {t: i for i, t in enumerate(RECRUITMENT_TAGS)}

_MAX_DESCRIPTION = 1000
_MAX_CONTACTS = 3

_MAX_RAW_LOGO_BYTES = 2 * 1024 * 1024  # decoded upload cap
_MAX_BASE64_CHARS = 2_900_000  # ~2 MiB of base64 + slack; hard cap pre-decode
_LOGO_BOX = 200
_MAX_STORED_LOGO_BYTES = 128 * 1024
_MAX_SOURCE_DIM = 4096  # header-checked BEFORE any pixel decode
_ALLOWED_SOURCE_FORMATS = {"PNG", "JPEG", "GIF", "WEBP", "BMP"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RecruitmentProfileResponse(BaseModel):
    recruiting: bool
    description: str
    classes: list[str]
    tags: list[str]
    contacts: list[str]
    discord_url: str | None = None
    updated_at: int | None = None
    updated_by_name: str | None = None
    has_logo: bool = False
    logo_uploaded_at: int | None = None
    logo_uploaded_by_name: str | None = None
    available_tags: list[str]


class RecruitmentProfileInput(BaseModel):
    recruiting: bool = False
    description: str = ""
    classes: list[str] = []
    tags: list[str] = []
    contacts: list[str] = []
    discord_url: str | None = None


class LogoUploadInput(BaseModel):
    image_base64: str = Field(min_length=1, max_length=_MAX_BASE64_CHARS)


class LogoUploadResponse(BaseModel):
    ok: bool
    logo_uploaded_at: int


class RecruitingGuildEntry(BaseModel):
    guild_name: str
    description: str
    classes: list[str]
    tags: list[str]
    contacts: list[str]
    discord_url: str | None = None
    updated_at: int | None = None
    has_logo: bool = False
    logo_uploaded_at: int | None = None
    member_count: int | None = None


class RecruitingListResponse(BaseModel):
    guilds: list[RecruitingGuildEntry]
    available_tags: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_officer(request: Request, guild_name: str) -> SessionUser:
    """Session user who is an officer of this guild (or a site admin) —
    401/403 otherwise. Exactly the raid_schedule gate."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not is_admin(user) and not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")
    return user  # type: ignore[return-value]  # Discord OAuth dict matches SessionUser shape


async def _resolve_guild_id(guild_name: str) -> int:
    """The guild's census id for a write. An existing row's id is reused
    (no Census call); a first-ever save resolves it live. Saves are rare so
    one Census round-trip is acceptable; if Census can't answer right now
    the save fails with a retryable 503 rather than writing an unanchored
    row."""
    world = current_world()
    profile = await recruitment_db.get_profile(world, guild_name)
    if profile["guild_id"] is not None:
        return int(profile["guild_id"])
    async with shared_census_client() as client:
        gid = await client.get_guild_id(guild_name, world)
    if gid is None:
        raise HTTPException(
            status_code=503,
            detail="Couldn't verify the guild with Census just now — try again in a minute.",
        )
    return int(gid)


def _screen_text(value: str, *, actor: str, guild: str, field: str) -> None:
    """Reject + report free text that hits the blocklist. No-op if clean."""
    hit = contains_blocked_term(value)
    if hit:
        audit_log("suspicious_recruitment_text", actor=actor, guild=guild, field=field, value=value, reason=hit)
        raise HTTPException(
            status_code=400,
            detail="That text contains disallowed content and has been reported.",
        )


async def _fmt_profile(profile: dict) -> RecruitmentProfileResponse:
    ids = [i for i in (profile.get("updated_by"), profile.get("logo_uploaded_by")) if i]
    names = await get_display_names_for_discord_ids(ids) if ids else {}
    return RecruitmentProfileResponse(
        recruiting=profile["recruiting"],
        description=profile["description"],
        classes=profile["classes"],
        tags=profile["tags"],
        contacts=profile["contacts"],
        discord_url=profile["discord_url"],
        updated_at=profile["updated_at"],
        updated_by_name=names.get(profile.get("updated_by") or ""),
        has_logo=profile["has_logo"],
        logo_uploaded_at=profile["logo_uploaded_at"],
        logo_uploaded_by_name=names.get(profile.get("logo_uploaded_by") or ""),
        available_tags=list(RECRUITMENT_TAGS),
    )


def _process_logo(raw: bytes) -> bytes:
    """Re-encode an uploaded image to a <=200x200 WebP (sync — run via
    run_sync). Raises ValueError with a user-facing message on anything
    that isn't a safe decodable image.

    Safety order matters: format + header dimensions are checked BEFORE any
    pixel decode (with Pillow's default MAX_IMAGE_PIXELS bomb guard kept),
    then verify() sanity-checks the structure, then the image is REOPENED
    (verify invalidates the parser) for the real decode. Animated GIF/WebP
    flattens to frame 0. The WebP re-encode strips EXIF and destroys any
    polyglot payload hiding in the original bytes."""
    try:
        probe = Image.open(io.BytesIO(raw))
        fmt = (probe.format or "").upper()
        width, height = probe.size
        if fmt not in _ALLOWED_SOURCE_FORMATS:
            raise ValueError("Unsupported image format — use PNG, JPEG, GIF, WebP or BMP.")
        if width > _MAX_SOURCE_DIM or height > _MAX_SOURCE_DIM:
            raise ValueError(f"Image is too large — at most {_MAX_SOURCE_DIM} pixels per side.")
        probe.verify()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("That file could not be read as an image.") from exc

    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img) or img
        img = img.convert("RGBA")
        img.thumbnail((_LOGO_BOX, _LOGO_BOX), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=85, method=6)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("That image could not be processed — try a different file.") from exc

    out = buf.getvalue()
    if len(out) > _MAX_STORED_LOGO_BYTES:
        raise ValueError("That image could not be compressed enough — try a simpler one.")
    return out


# Adventure class names from the same catalogue /api/classes serves — the
# frontend's toggle list and this validator can't drift. ALL_CLASSES
# (census constants) is a startup cross-check: a mismatch would mean the
# committed classes.db and the census constants have diverged.
_adventure_classes: frozenset[str] | None = None


def _adventure_class_names_sync() -> frozenset[str]:
    from backend.eq2db.classes import catalogue as classes_db

    names = frozenset(r["name"] for r in classes_db.list_all() if r["archetype"] != "Crafter")
    if names != ALL_CLASSES:
        _log.warning("[recruitment] classes.db adventure names differ from census constants ALL_CLASSES")
    return names


async def _adventure_class_names() -> frozenset[str]:
    global _adventure_classes
    if _adventure_classes is None:
        _adventure_classes = await run_sync(_adventure_class_names_sync)
    return _adventure_classes


# ---------------------------------------------------------------------------
# Endpoints — profile
# ---------------------------------------------------------------------------


@router.get("/guild/{guild_name}/recruitment", response_model=RecruitmentProfileResponse)
async def get_recruitment(guild_name: str) -> RecruitmentProfileResponse:
    """Public — anyone may view a guild's recruitment profile."""
    _validate_guild_name(guild_name)
    profile = await recruitment_db.get_profile(current_world(), guild_name)
    return await _fmt_profile(profile)


@router.put("/guild/{guild_name}/recruitment", response_model=RecruitmentProfileResponse)
@limiter.limit("20/minute", key_func=upload_rate_key)
async def put_recruitment(
    guild_name: str, body: RecruitmentProfileInput, request: Request
) -> RecruitmentProfileResponse:
    """Officer-or-admin — replace the guild's recruitment profile (never
    touches the logo)."""
    _validate_guild_name(guild_name)
    user = await _require_officer(request, guild_name)

    description = sanitize_text(body.description, max_len=_MAX_DESCRIPTION)
    if description:
        _screen_text(description, actor=user["id"], guild=guild_name, field="description")

    valid_classes = await _adventure_class_names()
    classes: list[str] = []
    for cls in body.classes:
        if cls not in valid_classes:
            raise HTTPException(status_code=400, detail=f"Unknown adventure class {cls!r}.")
        if cls not in classes:
            classes.append(cls)

    tags: list[str] = []
    for tag in body.tags:
        if tag not in _TAG_ORDER:
            raise HTTPException(status_code=400, detail=f"Unknown tag {tag!r}.")
        if tag not in tags:
            tags.append(tag)
    tags.sort(key=_TAG_ORDER.__getitem__)

    if len(body.contacts) > _MAX_CONTACTS:
        raise HTTPException(status_code=400, detail=f"At most {_MAX_CONTACTS} in-game contacts.")
    contacts: list[str] = []
    if body.contacts:
        # Officer gate already warmed the roster cache, so this is free.
        rank_map = await _roster_rank_map(guild_name)
        for raw_name in body.contacts:
            name = validate_character_name(raw_name)
            if name is None:
                raise HTTPException(status_code=400, detail=f"Invalid character name {raw_name!r}.")
            canonical = name.capitalize()
            if canonical.lower() not in rank_map:
                raise HTTPException(
                    status_code=400,
                    detail=f"{canonical} is not a member of {guild_name} — contacts must be guild members.",
                )
            if canonical not in contacts:
                contacts.append(canonical)

    discord_url = (body.discord_url or "").strip() or None
    if discord_url and not DISCORD_INVITE_RE.match(discord_url):
        raise HTTPException(
            status_code=400,
            detail="Discord link must be https://discord.gg/<code> or https://discord.com/invite/<code>.",
        )

    world = current_world()
    guild_id = await _resolve_guild_id(guild_name)
    stored = await recruitment_db.upsert_profile(
        world,
        guild_id,
        guild_name,
        recruiting=body.recruiting,
        description=description,
        classes=classes,
        tags=tags,
        contacts=contacts,
        discord_url=discord_url,
        updated_by=user["id"],
    )
    audit_log(
        "guild_recruitment_updated",
        actor=user["id"],
        guild=guild_name,
        guild_id=guild_id,
        recruiting=body.recruiting,
        classes=len(classes),
        tags=len(tags),
        contacts=len(contacts),
        has_discord=bool(discord_url),
    )
    return await _fmt_profile(stored)


# ---------------------------------------------------------------------------
# Endpoints — logo
# ---------------------------------------------------------------------------


@router.put("/guild/{guild_name}/recruitment/logo", response_model=LogoUploadResponse)
@limiter.limit("10/hour", key_func=upload_rate_key)
async def put_recruitment_logo(guild_name: str, body: LogoUploadInput, request: Request) -> LogoUploadResponse:
    """Officer-or-admin — upload (replace) the guild logo. Base64 JSON body;
    the image is re-encoded server-side and the uploader is audited."""
    _validate_guild_name(guild_name)
    user = await _require_officer(request, guild_name)

    try:
        raw = base64.b64decode(body.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image_base64 is not valid base64.") from exc
    if len(raw) > _MAX_RAW_LOGO_BYTES:
        raise HTTPException(status_code=413, detail="Logo file is too large — 2 MB max.")

    try:
        processed = await run_sync(_process_logo, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    world = current_world()
    guild_id = await _resolve_guild_id(guild_name)
    uploaded_at = await recruitment_db.set_logo(
        world, guild_id, guild_name, logo=processed, media_type="image/webp", uploaded_by=user["id"]
    )
    audit_log(
        "guild_logo_uploaded",
        actor=user["id"],
        guild=guild_name,
        guild_id=guild_id,
        source_bytes=len(raw),
        stored_bytes=len(processed),
    )
    return LogoUploadResponse(ok=True, logo_uploaded_at=uploaded_at)


@router.delete("/guild/{guild_name}/recruitment/logo")
async def delete_recruitment_logo(guild_name: str, request: Request) -> dict:
    """Officer-or-admin — remove the guild logo. Admin access is the abuse
    backstop; idempotent (a second delete is a no-op and isn't audited)."""
    _validate_guild_name(guild_name)
    user = await _require_officer(request, guild_name)
    removed = await recruitment_db.clear_logo(current_world(), guild_name)
    if removed:
        audit_log("guild_logo_removed", actor=user["id"], guild=guild_name, by_admin=is_admin(user))
    return {"ok": True, "removed": removed}


@router.get("/guild/{guild_name}/recruitment/logo")
async def get_recruitment_logo(guild_name: str, request: Request) -> Response:
    """Public — the stored WebP logo. Strong caching: the bytes behind a
    given logo_uploaded_at never change (uploads bump it), so ETag + the
    frontend's ?v= cache-buster make revalidation cheap."""
    _validate_guild_name(guild_name)
    stored = await recruitment_db.get_logo(current_world(), guild_name)
    if stored is None:
        raise HTTPException(status_code=404, detail="No logo")
    etag = f'W/"{stored["logo_uploaded_at"]}"'
    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=86400",
        "X-Content-Type-Options": "nosniff",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=stored["logo"], media_type=stored["logo_media_type"] or "image/webp", headers=headers)


# ---------------------------------------------------------------------------
# Endpoints — browse listing
# ---------------------------------------------------------------------------


@router.get("/recruiting", response_model=RecruitingListResponse)
@limiter.limit("30/minute")
async def list_recruiting(request: Request) -> RecruitingListResponse:
    """All recruiting guilds on the active server, newest profile edit
    first, with last-known member counts from the census guild_history
    mirror (None when the store has never seen the guild)."""
    world = current_world()
    rows = await recruitment_db.list_recruiting(world)

    counts: dict[str, int] = {}
    if rows and census_store.path.exists():

        def _read_counts() -> dict[str, int]:
            conn = census_store.init_db()
            try:
                return census_store.latest_guild_member_counts(conn, world, [r["guild_name"].lower() for r in rows])
            finally:
                conn.close()

        counts = await run_sync(_read_counts)

    return RecruitingListResponse(
        guilds=[
            RecruitingGuildEntry(
                guild_name=r["guild_name"],
                description=r["description"],
                classes=r["classes"],
                tags=r["tags"],
                contacts=r["contacts"],
                discord_url=r["discord_url"],
                updated_at=r["updated_at"],
                has_logo=r["has_logo"],
                logo_uploaded_at=r["logo_uploaded_at"],
                member_count=counts.get(r["guild_name"].lower()),
            )
            for r in rows
        ],
        available_tags=list(RECRUITMENT_TAGS),
    )
