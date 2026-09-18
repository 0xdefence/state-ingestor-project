from uuid import UUID

from fastapi import APIRouter

from services.api.dependencies import ReaderDependency, RuntimeDependency
from services.api.presenters import present
from services.application.process import ProcessRun, process_run
from services.application.queries import RunDetailQuery
from services.application.query_services import get_run_detail

router = APIRouter()


@router.post("/runs/{run_id}/process")
def process(run_id: UUID, runtime: RuntimeDependency) -> object:
    with runtime as opened:
        return present(
            process_run(
                ProcessRun(run_id),
                opened.uow_factory,
                opened.source_store,
                opened.clock,
            )
        )


@router.get("/runs/{run_id}")
def run_detail(run_id: UUID, repo: ReaderDependency) -> object:
    with repo as opened:
        return present(get_run_detail(opened, RunDetailQuery(run_id)))
