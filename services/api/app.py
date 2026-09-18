"""Composition root for the consolidated local product API."""

import os

from fastapi import FastAPI

from services.api.dependencies import RuntimeFactory
from services.api.errors import register_errors
from services.api.routes import reviews, runs, uploads, workspace
from services.infrastructure.runtime import Settings, build_runtime


def create_app(
    runtime_factory: RuntimeFactory = build_runtime,
    *,
    settings: Settings | None = None,
    max_upload_bytes: int = 50 * 1024 * 1024,
) -> FastAPI:
    if max_upload_bytes < 1:
        raise ValueError("max_upload_bytes must be positive")
    app = FastAPI(title="Alexis local review API")
    app.state.runtime_factory = runtime_factory
    app.state.settings = settings or Settings.model_validate(
        {
            key: value
            for key, env in (
                ("database_url", "DATABASE_URL"),
                ("source_root", "SOURCE_ROOT"),
            )
            if (value := os.environ.get(env)) is not None
        }
    )
    app.state.max_upload_bytes = max_upload_bytes
    register_errors(app)
    for router in (uploads.router, runs.router, workspace.router, reviews.router):
        app.include_router(router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
