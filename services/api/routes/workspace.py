from fastapi import APIRouter

from services.api.dependencies import ReaderDependency, ScopeDependency
from services.api.presenters import present
from services.application.queries import WorkspaceQuery
from services.application.query_services import get_workspace

router = APIRouter()


@router.get("/workspace")
def workspace(repo: ReaderDependency, scope: ScopeDependency) -> object:
    with repo as opened:
        return present(get_workspace(opened, WorkspaceQuery(scope)))
