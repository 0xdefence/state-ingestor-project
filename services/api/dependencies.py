"""HTTP configuration and runtime lifetime; application queries receive a port."""

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import Depends, Query, Request

from services.application.errors import ApplicationValidationError
from services.application.queries import (
    AllFilesScope,
    CurrentFileScope,
    FileScope,
    SelectedFilesScope,
)
from services.application.read_ports import ReadRepository
from services.infrastructure.runtime import Runtime, Settings

type RuntimeFactory = Callable[[Settings], AbstractContextManager[Runtime]]


def get_runtime(request: Request) -> AbstractContextManager[Runtime]:
    factory = cast(RuntimeFactory, request.app.state.runtime_factory)
    settings = cast(Settings, request.app.state.settings)
    return factory(settings)


type RuntimeDependency = Annotated[
    AbstractContextManager[Runtime], Depends(get_runtime)
]


@contextmanager
def _reader(runtime: AbstractContextManager[Runtime]) -> Generator[ReadRepository]:
    with runtime as opened:
        if opened.read_repository is None:
            raise RuntimeError("Runtime has no read repository")
        yield opened.read_repository


def get_reader(runtime: RuntimeDependency) -> AbstractContextManager[ReadRepository]:
    return _reader(runtime)


type ReaderDependency = Annotated[
    AbstractContextManager[ReadRepository], Depends(get_reader)
]


def file_scope(
    scope: Literal["current", "selected", "all"] = "all",
    run_id: Annotated[list[UUID] | None, Query()] = None,
) -> FileScope:
    ids = tuple(run_id or ())
    if scope == "current":
        if len(ids) != 1:
            raise ApplicationValidationError(
                "current scope requires exactly one run_id"
            )
        return CurrentFileScope(ids[0])
    if scope == "selected":
        return SelectedFilesScope(ids)
    if ids:
        raise ApplicationValidationError("all scope does not accept run_id")
    return AllFilesScope()


type ScopeDependency = Annotated[FileScope, Depends(file_scope)]
