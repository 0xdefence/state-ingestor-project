"""Acceptance-only app: arm a real batch failure and inspect persisted rows.

Never loaded by the production launcher unless explicitly selected by the test
server. No application responses, persistence, or browser requests are mocked.
"""

import json
import os
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, select

from services.api.app import create_app as product_app
from services.application import process
from services.application.normalise import normalise_run
from services.infrastructure.db.models import Base
from services.pipeline.normalise.candidates import NormaliseContext


class InjectedFailure(RuntimeError):
    """Expected acceptance-only failure."""


def create_app() -> FastAPI:
    app = product_app()

    @app.exception_handler(InjectedFailure)
    async def expected_failure(
        _request: Request, _error: InjectedFailure
    ) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred",
                    "details": {},
                }
            },
        )

    armed = False
    original = process.normalise_run

    @app.post("/__test__/fail-next-process")
    def arm() -> dict[str, bool]:
        nonlocal armed
        armed = True
        return {"armed": True}

    def fail_once(
        run_id: UUID, batch_size: int, uow_factory: Any, context: NormaliseContext
    ) -> Any:
        nonlocal armed
        if not armed:
            return original(run_id, batch_size, uow_factory, context)
        armed = False

        def fail_second_batch(batch: int) -> None:
            if batch == 2:
                raise InjectedFailure(
                    "Acceptance: recoverable normalisation batch failure"
                )

        return normalise_run(
            run_id, 10, uow_factory, context, failure_injector=fail_second_batch
        )

    process.normalise_run = fail_once

    @app.get("/__test__/snapshot")
    def snapshot() -> dict[str, list[dict[str, Any]]]:
        engine = create_engine(os.environ["DATABASE_URL"])
        try:
            with engine.connect() as connection:
                result = {
                    table.name: sorted(
                        [
                            dict(row)
                            for row in connection.execute(select(table)).mappings()
                        ],
                        key=lambda row: str(row.get("id", row)),
                    )
                    for table in Base.metadata.sorted_tables
                    if table.name not in ("source_occurrence", "run_source_occurrence")
                }
            return json.loads(json.dumps(result, default=str))
        finally:
            engine.dispose()

    return app
