"""Health stays available without a database; invalid commands fail safely."""

import asyncio
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest

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


@pytest.mark.parametrize(
    "failure",
    [
        ValueError("postgresql://private_user:private_password@host/database"),
        KeyError("corrupt internal evidence"),
    ],
)
def test_unexpected_builtin_errors_have_safe_500_envelope(failure):
    @contextmanager
    def unavailable(settings):
        raise failure
        yield

    async def scenario():
        app = create_app(runtime_factory=unavailable)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            result = await client.get("/api/workspace")
            assert result.status_code == 500
            assert result.json() == {
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred",
                    "details": {},
                }
            }

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"operator_name": " "},
        {"idempotency_key": " "},
        {"reason": None},
    ],
)
def test_expected_decision_validation_remains_422_before_runtime(changes):
    @contextmanager
    def unavailable(settings):
        raise RuntimeError("must not open database for invalid request")
        yield

    async def scenario():
        app = create_app(runtime_factory=unavailable)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            command = {
                "candidate_revision_id": str(uuid4()),
                "expected_sequence": 0,
                "outcome": "reject",
                "operator_name": "Alex",
                "reason": "Bad source",
                "idempotency_key": "test",
                **changes,
            }
            result = await client.post(
                f"/api/reviews/{uuid4()}/decisions", json=command
            )
            assert result.status_code == 422
            assert result.json()["error"]["code"] == "validation_error"

    asyncio.run(scenario())
