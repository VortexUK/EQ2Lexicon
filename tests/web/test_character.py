from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from web.app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.mark.asyncio
async def test_character_not_found(app):
    """Census returns nothing → 404."""
    with patch("web.routes.character.CensusClient") as MockClient:
        instance = MockClient.return_value
        instance.get_character = AsyncMock(return_value=None)
        instance.close = AsyncMock()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/character/NoSuchChar")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_character_returns_data(app):
    """Valid Census response → 200 with character fields."""
    from census.models import CharacterOverview

    fake_char = CharacterOverview(
        id="123",
        name="Vortex",
        level=70,
        cls="Wizard",
        race="High Elf",
        gender="Male",
        deity=None,
        aa_count=50,
        world="Varsoon",
        ts_class="Sage",
        ts_level=70,
        equipment=[],
    )

    with patch("web.routes.character.CensusClient") as MockClient:
        instance = MockClient.return_value
        instance.get_character = AsyncMock(return_value=fake_char)
        instance.close = AsyncMock()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/character/Vortex")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Vortex"
    assert data["level"] == 70
    assert data["cls"] == "Wizard"
    assert data["aa_count"] == 50
    assert data["ts_class"] == "Sage"
    assert data["ts_level"] == 70
