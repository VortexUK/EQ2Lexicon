from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_health_returns_ok(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_health_response_shape(app):
    """Ensure the response always contains exactly the fields we expect."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health")

    assert set(response.json().keys()) == {"status", "version"}


@pytest.mark.asyncio
async def test_openapi_schema_available():
    """OpenAPI schema is available when SHOW_API_DOCS is enabled."""
    import backend.server.app as app_module

    # _SHOW_DOCS is a module-level constant; patch it so create_app sees True.
    with patch.object(app_module, "_SHOW_DOCS", True):
        docs_app = app_module.create_app(session_secret="test-secret")

    async with AsyncClient(transport=ASGITransport(app=docs_app), base_url="http://test") as client:
        response = await client.get("/api/openapi.json")

    assert response.status_code == 200
    assert "paths" in response.json()
