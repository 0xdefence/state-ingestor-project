"""Real PostgreSQL + ASGI product contract, projections and safe errors."""

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from services.api.app import create_app
from services.infrastructure.runtime import Settings
from tests.integration.foundation.test_concurrent_ingest import engine as engine


@pytest.fixture
def app(engine, tmp_path):
    return create_app(
        settings=Settings(
            database_url=engine.url.render_as_string(hide_password=False),
            source_root=tmp_path / "sources",
        )
    )


def test_seven_product_endpoints_and_effective_decisions(app):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            body = Path("data/messy_sample_data.csv").read_bytes()

            async def upload(key):
                return await client.post(
                    "/api/uploads",
                    files={"file": ("sample.csv", body)},
                    data={"operator_name": "Alex", "idempotency_key": key},
                )

            uploaded = await upload("upload-one")
            assert uploaded.status_code == 201, uploaded.text
            ids = uploaded.json()
            replay = await upload("upload-one")
            assert replay.status_code == 200
            assert replay.json()["source_occurrence_id"] == ids["source_occurrence_id"]
            duplicate = await upload("upload-two")
            assert duplicate.status_code == 200
            assert (
                duplicate.json()["source_occurrence_id"] != ids["source_occurrence_id"]
            )
            run_id = ids["run_id"]
            processed = await client.post(f"/api/runs/{run_id}/process")
            assert processed.status_code == 200, processed.text
            assert processed.json()["state"] == "staged"
            params = [("scope", "current"), ("run_id", run_id)]
            workspace = await client.get("/api/workspace", params=params)
            assert workspace.status_code == 200, workspace.text
            assert len(workspace.json()["runs"]) == 1
            run = await client.get(f"/api/runs/{run_id}")
            assert run.status_code == 200, run.text
            assert {o["id"] for o in run.json()["occurrences"]} == {
                ids["source_occurrence_id"],
                duplicate.json()["source_occurrence_id"],
            }
            queue = await client.get("/api/reviews", params=params)
            assert queue.status_code == 200, queue.text
            item = next(
                i for i in queue.json()["items"] if i["verdict"] == "NEEDS_REVIEW"
            )
            assert "payload" not in item
            detail = await client.get(f"/api/reviews/{item['id']}")
            assert detail.status_code == 200, detail.text
            before = detail.json()
            assert before["item"]["effective_state"] == "pending"
            candidates = [
                n
                for n in before["evidence"]["nodes"]
                if n["kind"] == "candidate_revision"
            ]
            assert len({n["id"] for n in candidates}) == len(candidates)
            assert item["candidate_revision_id"] in {n["id"] for n in candidates}
            command = {
                "candidate_revision_id": item["candidate_revision_id"],
                "expected_sequence": 0,
                "outcome": "reject",
                "operator_name": "Alex",
                "reason": "Source needs correction",
                "idempotency_key": "decision-one",
            }
            decision = await client.post(
                f"/api/reviews/{item['id']}/decisions", json=command
            )
            assert decision.status_code == 200, decision.text
            replayed = await client.post(
                f"/api/reviews/{item['id']}/decisions", json=command
            )
            assert replayed.status_code == 200
            assert replayed.json()["replayed"] is True
            after = (await client.get(f"/api/reviews/{item['id']}")).json()
            assert after["item"]["effective_state"] == "rejected"
            assert after["item"]["decision_sequence"] == 1
            assert len(after["decisions"]) == 1
            assert after["allowed_outcomes"] == ["approve"]
            filtered = (
                await client.get(
                    "/api/reviews", params=params + [("effective_state", "rejected")]
                )
            ).json()
            assert [i["id"] for i in filtered["items"]] == [item["id"]]
            stale = await client.post(
                f"/api/reviews/{item['id']}/decisions",
                json={**command, "idempotency_key": "stale"},
            )
            assert stale.status_code == 409
            assert set(stale.json()) == {"error"}
            assert stale.json()["error"]["code"] == "stale_decision"
            assert isinstance(stale.json()["error"]["details"], dict)
            conflict = await client.post(
                f"/api/reviews/{item['id']}/decisions",
                json={**command, "reason": "Different"},
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "idempotency_conflict"

    asyncio.run(scenario())


def test_health_scope_validation_missing_rows_and_limit(app, engine):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/health")).json() == {"status": "ok"}
            for query in (
                "scope=current",
                "scope=selected",
                f"scope=all&run_id={uuid4()}",
                "scope=bogus",
                "scope=current&run_id=bad",
            ):
                result = await client.get("/api/workspace?" + query)
                assert result.status_code == 422
                assert result.json()["error"]["code"] == "validation_error"
            for path in (
                f"/api/runs/{uuid4()}",
                f"/api/reviews/{uuid4()}",
                f"/api/workspace?scope=current&run_id={uuid4()}",
            ):
                assert (await client.get(path)).status_code == 404
            app.state.max_upload_bytes = 3
            too_big = await client.post(
                "/api/uploads",
                files={"file": ("big.csv", b"1234")},
                data={"operator_name": "Alex"},
            )
            assert too_big.status_code == 413
            with engine.connect() as conn:
                assert conn.scalar(text("SELECT count(*) FROM source_occurrence")) == 0

    asyncio.run(scenario())


def test_repeatable_read_keeps_aggregate_before_concurrent_decision(engine, tmp_path):
    from dataclasses import FrozenInstanceError

    from sqlalchemy import event

    from services.application.decisions import decide_review
    from services.application.queries import CurrentFileScope, WorkspaceQuery
    from services.application.query_services import get_workspace
    from services.infrastructure.db.read_repository import SqlAlchemyReadRepository
    from tests.integration.review.test_decisions import setup_review, uow_for
    from tests.unit.classify.test_rules import FixedClock

    command = setup_review(engine, tmp_path)
    with uow_for(engine) as uow:
        run_id = uow.reviews.get_locked(command.review_item_id).run_id
    injected = False
    isolation = []

    def concurrent_commit(conn, cursor, statement, parameters, context, executemany):
        nonlocal injected
        if statement.startswith("SELECT run.") and not injected:
            injected = True
            isolation.append(conn.get_isolation_level())
            decide_review(command, uow_for(engine), FixedClock())

    event.listen(engine, "after_cursor_execute", concurrent_commit)
    try:
        view = get_workspace(
            SqlAlchemyReadRepository(engine), WorkspaceQuery(CurrentFileScope(run_id))
        )
    finally:
        event.remove(engine, "after_cursor_execute", concurrent_commit)
    assert isolation == ["REPEATABLE READ"]
    assert dict(view.review_counts.fields)["pending"] == 1
    assert dict(view.review_counts.fields)["approved"] == 0
    fresh = get_workspace(
        SqlAlchemyReadRepository(engine), WorkspaceQuery(CurrentFileScope(run_id))
    )
    assert dict(fresh.review_counts.fields)["approved"] == 1
    with pytest.raises(FrozenInstanceError):
        fresh.review_counts.fields = ()


def test_scopes_exclude_unselected_files_and_all_includes_them(app):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            ids = []
            for content in (b"UNKNOWN,x\n", b"UNKNOWN,y\n"):
                upload = await client.post(
                    "/api/uploads",
                    files={"file": ("input.csv", content)},
                    data={"operator_name": "Alex"},
                )
                run_id = upload.json()["run_id"]
                ids.append(run_id)
                assert (
                    await client.post(f"/api/runs/{run_id}/process")
                ).status_code == 200
            selected = await client.get(
                "/api/workspace", params=[("scope", "selected"), ("run_id", ids[1])]
            )
            assert [r["id"] for r in selected.json()["runs"]] == [ids[1]]
            all_runs = (await client.get("/api/workspace?scope=all")).json()
            assert {r["id"] for r in all_runs["runs"]} == set(ids)
            queue = (
                await client.get(
                    "/api/reviews",
                    params=[
                        ("scope", "selected"),
                        ("run_id", ids[1]),
                        ("verdict", "REJECTED"),
                    ],
                )
            ).json()
            assert len(queue["items"]) == 1
            assert queue["items"][0]["run_id"] == ids[1]
            item = queue["items"][0]
            command = {
                "candidate_revision_id": item["candidate_revision_id"],
                "expected_sequence": 0,
                "outcome": "approve",
                "operator_name": "Alex",
                "idempotency_key": "illegal",
            }
            result = await client.post(
                f"/api/reviews/{item['id']}/decisions", json=command
            )
            assert result.status_code == 422
            assert result.json()["error"]["code"] == "illegal_decision"
            missing = await client.post(
                f"/api/reviews/{uuid4()}/decisions", json=command
            )
            assert missing.status_code == 404

    asyncio.run(scenario())


def test_unexpected_failure_redacts_credentials():
    from contextlib import contextmanager

    @contextmanager
    def broken_runtime(settings):
        raise RuntimeError("postgresql://private_user:private_password@host/database")
        yield

    async def scenario():
        app = create_app(runtime_factory=broken_runtime)
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


def test_run_detail_preserves_processing_and_occurrence_lineage(app):
    import hashlib

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            content = b"UNKNOWN,x\n"
            uploads = []
            for name in ("original.csv", "again.csv"):
                response = await client.post(
                    "/api/uploads",
                    files={"file": (name, content)},
                    data={"operator_name": "Alex"},
                )
                uploads.append(response.json())
            run_id = uploads[0]["run_id"]
            await client.post(f"/api/runs/{run_id}/process")
            result = (await client.get(f"/api/runs/{run_id}")).json()
            assert result["run"]["source_sha256"] == hashlib.sha256(content).hexdigest()
            assert result["run"]["source_byte_size"] == len(content)
            assert result["run"]["rules_version"]
            assert result["run"]["fx_snapshot_id"]
            assert [o["filename"] for o in result["occurrences"]] == [
                "original.csv",
                "again.csv",
            ]
            assert result["occurrences"][0]["run_links"][0]["relation"] == "initiated"
            assert (
                result["occurrences"][1]["run_links"][0]["relation"]
                == "duplicate_upload"
            )
            assert result["occurrences"][1]["run_links"][0]["run_id"] == run_id

    asyncio.run(scenario())
