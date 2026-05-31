"""Shared ingest-test helpers for parses ingest web tests.

Extracted per TEST-005 / Phase 2b.2. Single source of truth for the
ingest payload builder and signing helpers — both test_parses_ingest.py
and test_parses_ingest_hmac.py import from here.

The _minimal_payload in this file is the canonical (full) version from
test_parses_ingest.py; the HMAC test previously had a minimal variant
with only one combatant — that variant is now covered by the HMAC test
calling this version directly (it only needs the payload to be valid,
not to exercise every field).
"""

from __future__ import annotations

import hashlib
import hmac
import json


def _minimal_payload(encid: str = "ABCD1234", logger_server: str | None = "Varsoon") -> dict:
    """Smallest payload that should pass validation + ingest.

    Defaults logger_server to a value on the ALLOWED_SERVERS allowlist
    so the strict server gate (active since the introduction of the
    allowlist) doesn't trip on tests that don't care about the field.
    Pass logger_server=None to build the pre-v0.1.10 shape for tests
    that explicitly exercise the strict gate.
    """
    payload = {
        "logger_name": "Menludiir",
        "encounter": {
            "encid": encid,
            "title": "a krait patriarch",
            "zone": "Great Divide",
            "starttime": "2026-05-24 13:51:56",
            "endtime": "2026-05-24 13:52:42",
            "duration": 46,
            "damage": 502718,
            "encdps": 10928.65,
            "kills": 4,
            "deaths": 0,
        },
        "combatants": [
            {
                "name": "Menludiir",
                "ally": "T",
                "starttime": "2026-05-24 13:51:56",
                "endtime": "2026-05-24 13:52:43",
                "duration": 47,
                "damage": 502718,
                "damageperc": "100%",
                "kills": 4,
                "healed": 11637,
                "healedperc": "100%",
                "critheals": 1,
                "heals": 40,
                "curedispels": 0,
                "powerdrain": 0,
                "powerreplenish": 0,
                "dps": 10696.13,
                "encdps": 10928.65,
                "enchps": 252.98,
                "hits": 132,
                "crithits": 123,
                "blocked": 0,
                "misses": 0,
                "swings": 132,
                "healstaken": 11637,
                "damagetaken": 27557,
                "deaths": 0,
                "tohit": 100.0,
                "critdamperc": "93%",
                "crithealperc": "3%",
                "crittypes": "0.8%L - 0.0%F - 0.0%M",
                "threatstr": "+(0)20000/-(0)0",
                "threatdelta": 20000,
            },
            {
                "name": "a krait patriarch",
                "ally": "F",
                "duration": 15,
                "damage": 5716,
                "damageperc": "--",
                "kills": 0,
                "healed": 0,
                "dps": 381.07,
                "encdps": 124.26,
                "hits": 11,
                "swings": 12,
                "deaths": 1,
                "damagetaken": 145877,
                "tohit": 91.67,
            },
        ],
        "damage_types": [
            {
                "combatant": "Menludiir",
                "grouping": "Group 1",
                "type": "divine",
                "damage": 400000,
                "hits": 100,
                "swings": 100,
                "crithits": 90,
                "maxhit": 8000,
                "dps": 8500.0,
                "critperc": "90%",
            },
        ],
        "attack_types": [
            {
                "attacker": "Menludiir",
                "victim": "a krait patriarch",
                "swingtype": 2,
                "type": "Smite",
                "damage": 400000,
                "hits": 100,
                "swings": 100,
                "crithits": 90,
                "maxhit": 8000,
                "minhit": 100,
                "resist": "divine",
                "critperc": "90%",
            },
            # Heal row (swing_type=3) — must come through unchanged
            {
                "attacker": "Menludiir",
                "swingtype": 3,
                "type": "Reverence",
                "damage": 7818,
                "hits": 12,
                "swings": 12,
                "resist": "Hitpoints",
                "critperc": "0%",
            },
            # All rollup — must be filtered out by the ingest path
            {
                "attacker": "Menludiir",
                "swingtype": 100,
                "type": "All",
                "damage": 502718,
                "hits": 132,
                "swings": 132,
                "resist": "All",
                "critperc": "93%",
            },
        ],
    }
    # Only stamp logger_server when the caller asked for one — passing
    # None lets a test build the pre-v0.1.10 shape to drive the strict
    # gate.
    if logger_server is not None:
        payload["logger_server"] = logger_server
    return payload


async def _fake_require_user(request):
    return {"id": "discord-123", "username": "alice", "auth_source": "token"}


def _sign(body_bytes: bytes, token: str) -> str:
    """Match what PayloadSigner.Sign does on the plugin side — lowercase
    hex HMAC-SHA256, key = utf-8 bytes of the bearer token."""
    return hmac.new(token.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


def _signed_post_kwargs(payload: dict, token: str = "eq2c_test_token") -> dict:
    """Build the AsyncClient.post(**kwargs) dict that a real v0.1.8+
    plugin upload would produce — raw `content` bytes so we control the
    exact bytes we hash, matching headers (Authorization + Content-Type
    + X-Lexicon-Signature). Use this for any test where the signature
    SHOULD validate; tests that probe the absent/wrong cases build the
    headers by hand instead."""
    body_bytes = json.dumps(payload).encode("utf-8")
    return {
        "content": body_bytes,
        "headers": {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Lexicon-Signature": _sign(body_bytes, token),
        },
    }
