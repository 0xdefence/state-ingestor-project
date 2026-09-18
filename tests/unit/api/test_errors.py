"""Health stays available without a database; invalid commands fail safely."""

import asyncio
from contextlib import contextmanager
from uuid import uuid4

import httpx

from services.api.app import create_app


def test_health_does_not_open_runtime():
    @contextmanager
    def unavailable(settings):
        raise RuntimeError("database unavailable")
        yield

    async def scenario():
        app = create_app(runtime_factory=unavailable)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            result = await client.get("/api/health")
            assert result.status_code == 200
            assert result.json() == {"status": "ok"}

    asyncio.run(scenario())


def test_missing_body_has_stable_validation_envelope():
    # Validation must not need a runtime, even when the DB is unavailable.
    async def scenario():
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            result = await client.post(f"/api/reviews/{uuid4()}/decisions", json={})
            assert result.status_code == 422
            assert result.json()["error"]["code"] == "validation_error"
            assert "input" not in str(result.json()["error"]["details"])

    asyncio.run(scenario())
