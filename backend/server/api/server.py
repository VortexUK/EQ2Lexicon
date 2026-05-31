"""Active-server info for the current subdomain (frontend bootstrap)."""

from __future__ import annotations

from fastapi import APIRouter

from backend.server.server_context import current_server, list_public_servers

router = APIRouter(tags=["server"])


@router.get("/server")
async def get_active_server() -> dict:
    s = current_server()
    return {
        "world": s.world,
        "display_name": s.display_name,
        "max_level": s.max_level,
        "current_xpac": s.current_xpac,
        "launch_dt": s.launch_dt,
        "servers": list_public_servers(),
    }
