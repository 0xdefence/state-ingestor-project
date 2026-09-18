"""Classification lifecycle is visible through the actual run-detail projection."""

import json

import pytest

from services.api.presenters import present
from services.application.queries import RunDetailQuery
from services.infrastructure.db.derived_repositories import (
    SqlAlchemyClassificationRepository,
)
from services.infrastructure.db.read_repository import SqlAlchemyReadRepository
from services.pipeline.classify import classify_run
from services.pipeline.rules.registry import default_registry
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.foundation.test_parse_recovery import uow_for
from tests.integration.pipeline.test_classification_core import evidence, prepared
from tests.unit.classify.test_rules import PRODUCT, FixedClock


def classification_events(detail):
    order = {
        "stage_started": 0,
        "stage_retried": 0,
        "stage_failed": 1,
        "stage_completed": 1,
    }
    return sorted(
        (event for event in detail["events"] if event["stage"] == "classify"),
        key=lambda event: (
            event["facts"]["attempt_number"],
            order[event["event_type"]],
        ),
    )


def projection(engine, run_id):
    return present(SqlAlchemyReadRepository(engine).run_detail(RunDetailQuery(run_id)))


def test_classification_completion_projects_attempt_results_and_time(engine, tmp_path):
    run_id = prepared(engine, tmp_path, PRODUCT)
    summary = classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    detail = projection(engine, run_id)
    (tmp_path / "classification-success.json").write_text(json.dumps(detail, indent=2))
    stages = classification_events(detail)
    assert [event["event_type"] for event in stages] == [
        "stage_started",
        "stage_completed",
    ]
    assert [event["facts"]["attempt_number"] for event in stages] == [1, 1]
    assert stages[-1]["facts"]["counts"] == summary.counts
    assert stages[-1]["facts"]["rules_version"] == default_registry().rules_version
    assert stages[-1]["occurred_at"]["instant"] == "2026-09-17T12:00:00Z"
    assert detail["run"]["state"] == "classified"
    assert detail["run"]["stage_failure"] is None
    before = evidence(engine)
    classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    assert evidence(engine) == before
    assert projection(engine, run_id) == detail


def test_classification_rollback_is_identifiable_after_reload_and_retry(
    engine, tmp_path, monkeypatch
):
    run_id = prepared(engine, tmp_path, PRODUCT)
    before = evidence(engine)
    original = SqlAlchemyClassificationRepository.add

    def fail(self, result):
        original(self, result)
        raise RuntimeError("injected after classification insert")

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyClassificationRepository, "add", fail)
        with pytest.raises(RuntimeError, match="injected"):
            classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    assert evidence(engine) == before
    detail = projection(engine, run_id)  # New projection session, as after reload.
    (tmp_path / "classification-failure.json").write_text(json.dumps(detail, indent=2))
    assert detail["run"]["state"] == "normalised"
    assert detail["run"]["stage_failure"] == "classify_failed"
    stages = classification_events(detail)
    assert [event["event_type"] for event in stages] == [
        "stage_started",
        "stage_failed",
    ]
    assert stages[-1]["facts"]["error_type"] == "RuntimeError"
    assert stages[-1]["facts"]["attempt_number"] == 1
    classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    reloaded = projection(engine, run_id)
    assert reloaded["run"]["state"] == "classified"
    assert reloaded["run"]["stage_failure"] is None
    stages = classification_events(reloaded)
    assert [event["event_type"] for event in stages] == [
        "stage_started",
        "stage_failed",
        "stage_retried",
        "stage_completed",
    ]
    assert [event["facts"]["attempt_number"] for event in stages] == [1, 1, 2, 2]
