"""Stable safe errors; database/driver exception text never crosses HTTP."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from services.application.decisions import (
    IdempotencyConflictError,
    IllegalDecisionError,
    StaleDecisionError,
)
from services.application.errors import (
    ApplicationValidationError,
    ResourceNotFoundError,
)


class UploadTooLargeError(ValueError):
    pass


def _response(
    status: int, code: str, message: str, details: dict[str, object] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


def register_errors(app: FastAPI) -> None:
    async def validation(request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, RequestValidationError)
        issues = [
            {"location": list(e["loc"]), "message": e["msg"], "type": e["type"]}
            for e in error.errors()
        ]
        return _response(
            422, "validation_error", "Check the supplied fields", {"issues": issues}
        )

    async def mapped(request: Request, error: Exception) -> JSONResponse:
        if isinstance(error, UploadTooLargeError):
            return _response(
                413, "upload_too_large", "File exceeds the configured upload limit"
            )
        if isinstance(error, StaleDecisionError):
            return _response(409, "stale_decision", str(error))
        if isinstance(error, IdempotencyConflictError):
            return _response(409, "idempotency_conflict", str(error))
        if isinstance(error, IllegalDecisionError):
            return _response(422, "illegal_decision", str(error))
        if isinstance(error, ResourceNotFoundError):
            return _response(404, "not_found", "Requested record was not found")
        if isinstance(error, ApplicationValidationError):
            return _response(422, "validation_error", str(error))
        if isinstance(error, HTTPException):
            return _response(
                error.status_code, "http_error", "Request could not be completed"
            )
        return _response(500, "internal_error", "An unexpected error occurred")

    app.add_exception_handler(RequestValidationError, validation)
    for cls in (
        ApplicationValidationError,
        ResourceNotFoundError,
        UploadTooLargeError,
        StaleDecisionError,
        IdempotencyConflictError,
        IllegalDecisionError,
        HTTPException,
        Exception,
    ):
        app.add_exception_handler(cls, mapped)
