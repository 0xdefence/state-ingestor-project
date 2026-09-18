from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from services.application.queries import CurrentFileScope, SelectedFilesScope


def test_selected_scope_rejects_empty_selection():
    with pytest.raises(ValueError, match="at least one run"):
        SelectedFilesScope(())


def test_scope_is_immutable_and_selection_is_unique():
    run = uuid4()
    scope = SelectedFilesScope((run, run))
    assert scope.run_ids == (run,)
    with pytest.raises(FrozenInstanceError):
        scope.run_ids = ()
    assert CurrentFileScope(run).run_id == run
