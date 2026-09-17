"""Validated CLI input boundaries; application commands remain framework-free."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FilePath, field_validator


class BoundaryInput(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)


class ProcessingInput(BoundaryInput):
    run_id: UUID
    batch_size: Annotated[int, Field(gt=0)] = 1000


class IngestInput(BoundaryInput):
    source: FilePath
    actor_label: Annotated[str, Field(min_length=1)] = "operator"
    idempotency_key: Annotated[str, Field(min_length=1)] | None = None
    process: bool = False
    batch_size: Annotated[int, Field(gt=0)] = 1000

    @field_validator("source")
    @classmethod
    def readable_source(cls, value: Path) -> Path:
        source = value.expanduser().absolute()
        with source.open("rb"):
            pass
        return source

    @field_validator("actor_label", "idempotency_key")
    @classmethod
    def nonblank_label(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Must not be blank")
        return value


class ReprocessInput(BoundaryInput):
    source_file_id: UUID
    source_occurrence_id: UUID
