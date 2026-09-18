"""Spool-backed multipart upload, bounded while streaming into the source store."""

from io import BufferedReader, RawIOBase
from typing import Annotated, BinaryIO, cast

from fastapi import APIRouter, File, Form, Request, Response, UploadFile

from services.api.dependencies import RuntimeDependency
from services.api.errors import UploadTooLargeError
from services.api.presenters import present
from services.application.errors import ApplicationValidationError
from services.application.ingest import IngestFile, ingest_file

router = APIRouter()


class LimitedReader(RawIOBase):
    def __init__(self, source: BinaryIO, limit: int) -> None:
        self.source = source
        self.remaining = limit

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: object) -> int:
        view = memoryview(cast(bytearray, buffer))
        chunk = self.source.read(min(len(view), self.remaining + 1))
        if len(chunk) > self.remaining:
            raise UploadTooLargeError()
        self.remaining -= len(chunk)
        view[: len(chunk)] = chunk
        return len(chunk)


@router.post("/uploads")
def upload(
    request: Request,
    response: Response,
    runtime: RuntimeDependency,
    file: Annotated[UploadFile, File()],
    operator_name: Annotated[str, Form()],
    idempotency_key: Annotated[str | None, Form()] = None,
) -> object:
    if not operator_name.strip() or (
        idempotency_key is not None and not idempotency_key.strip()
    ):
        raise ApplicationValidationError(
            "operator_name and supplied idempotency_key must be nonempty"
        )
    limit = cast(int, request.app.state.max_upload_bytes)
    with runtime as opened, BufferedReader(LimitedReader(file.file, limit)) as stream:
        result = ingest_file(
            IngestFile(
                cast(BinaryIO, stream),
                file.filename or "upload.csv",
                file.filename or "upload.csv",
                operator_name,
                idempotency_key,
            ),
            opened.uow_factory(),
            opened.source_store,
            opened.clock,
        )
    response.status_code = 200 if result.source_reused else 201
    return present(result)
