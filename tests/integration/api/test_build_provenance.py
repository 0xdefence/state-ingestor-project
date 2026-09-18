"""Run creation takes its build identity from the real runtime boundary."""

import asyncio

import httpx
import pytest

from services.api.app import create_app
from tests.integration.foundation.test_concurrent_ingest import engine as engine


@pytest.mark.parametrize("build", [None, "release-2026.09.18"])
def test_upload_persists_runtime_build_and_retry_reuse_retains_it(
    engine, tmp_path, monkeypatch, build
):
    monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
    monkeypatch.setenv("SOURCE_ROOT", str(tmp_path))
    monkeypatch.delenv("APPLICATION_BUILD_REVISION", raising=False)
    if build is not None:
        monkeypatch.setenv("APPLICATION_BUILD_REVISION", build)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            uploaded = (
                await client.post(
                    "/api/uploads",
                    files={"file": ("source.csv", b"UNKNOWN,x\n")},
                    data={"operator_name": "Alex", "idempotency_key": "first"},
                )
            ).json()
            run_id = uploaded["run_id"]
            detail = (await client.get(f"/api/runs/{run_id}")).json()
            assert detail["run"]["build_revision"] == (build or "local-development")

        monkeypatch.setenv("APPLICATION_BUILD_REVISION", "later-runtime")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            for key in ("first", "duplicate"):
                reused = (
                    await client.post(
                        "/api/uploads",
                        files={"file": ("again.csv", b"UNKNOWN,x\n")},
                        data={"operator_name": "Alex", "idempotency_key": key},
                    )
                ).json()
                assert reused["run_id"] == run_id
                assert (
                    await client.post(f"/api/runs/{run_id}/process")
                ).status_code == 200
            reloaded = (await client.get(f"/api/runs/{run_id}")).json()
            assert reloaded["run"]["build_revision"] == (build or "local-development")
            assert reloaded["run"]["state"] == "staged"

    asyncio.run(scenario())
