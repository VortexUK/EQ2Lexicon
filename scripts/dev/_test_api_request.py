"""Start the app and hit the search endpoint to verify it works."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpx import ASGITransport, AsyncClient

from backend.server.app import create_app


async def test():
    app = create_app()
    # Call startup
    for handler in app.router.on_startup:
        await handler() if asyncio.iscoroutinefunction(handler) else handler()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # Test 1: no filters — should return empty
        r = await client.get("/api/items/search")
        print(f"No filters -> {r.status_code}: {r.json()['total']} results")

        # Test 2: has_stat=Ability Mod
        r = await client.get("/api/items/search?has_stat=Ability+Mod")
        j = r.json()
        print(
            f"has_stat=Ability+Mod -> {r.status_code}: {j['total']} results, first={j['results'][0]['name'] if j['results'] else 'none'}"
        )

        # Test 3: has_stat=Ability Mod (percent-encoded)
        r = await client.get("/api/items/search?has_stat=Ability%20Mod")
        j = r.json()
        print(f"has_stat=Ability%20Mod -> {r.status_code}: {j['total']} results")

        # Test 4: name filter
        r = await client.get("/api/items/search?name=templar")
        j = r.json()
        print(f"name=templar -> {r.status_code}: {j['total']} results")


asyncio.run(test())
