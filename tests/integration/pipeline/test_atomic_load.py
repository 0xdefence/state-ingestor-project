"""Whole-run canonical transactions and concrete prior observation contracts."""

import pytest
from sqlalchemy import text

from services.domain.runs import RunState
from services.pipeline.classify import classify_run
from services.pipeline.rules.registry import default_registry
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.foundation.test_parse_recovery import uow_for
from tests.integration.pipeline.test_classification_core import evidence, prepared
from tests.unit.classify.test_rules import CUSTOMER, ORDER, PRODUCT, FixedClock


def canonical_counts(engine):
    with engine.connect() as conn:
        return tuple(
            conn.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in (
                "canonical_identity",
                "canonical_revision",
                "canonical_business_key",
            )
        )


@pytest.mark.parametrize("insert_number", range(1, 10))
def test_load_failure_rolls_back_entire_staged_set(engine, tmp_path, insert_number):
    from services.application.load import stage_run

    run_id = prepared(engine, tmp_path, CUSTOMER + PRODUCT + ORDER)
    summary = classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    before = evidence(engine)

    def fail(number):
        if number == insert_number:
            raise RuntimeError("injected canonical insert")

    with pytest.raises(RuntimeError, match="injected"):
        stage_run(run_id, lambda: uow_for(engine), FixedClock(), fail)
    assert canonical_counts(engine) == (0, 0, 0)
    assert evidence(engine) == before
    with uow_for(engine) as uow:
        run = uow.runs.get(run_id)
        assert (run.state, run.stage_failure) == (RunState.CLASSIFIED, "load_failed")
    result = stage_run(run_id, lambda: uow_for(engine), FixedClock())
    assert result.staged_count == 3
    assert canonical_counts(engine) == (3, 3, 3)
    with uow_for(engine) as uow:
        observations = uow.canonicals.prior_observations()
        assert {p.candidate.id for p in observations} == {
            r.id for r in summary.graph.terminal
        }
        assert all(
            p.identity_id.version == p.canonical_revision_id.version == 4
            for p in observations
        )
        assert all(
            d.resolved_entity_id is not None
            for r in summary.results
            for d in uow.classifications.dependencies(r.id)
        )
        run = uow.runs.get(run_id)
        assert (run.state, run.stage_failure) == (RunState.STAGED, None)
    stable = evidence(engine)
    assert stage_run(run_id, lambda: uow_for(engine), FixedClock()).staged_count == 3
    assert canonical_counts(engine) == (3, 3, 3)
    assert evidence(engine) == stable


def test_reobservation_and_changed_prior_values_never_append_revision(engine, tmp_path):
    from services.application.load import stage_run

    first = prepared(engine, tmp_path / "first", PRODUCT)
    classify_run(first, default_registry(), uow_for(engine), FixedClock())
    stage_run(first, lambda: uow_for(engine), FixedClock())
    second = prepared(engine, tmp_path / "second", "\n" + PRODUCT)
    summary = classify_run(second, default_registry(), uow_for(engine), FixedClock())
    assert len(summary.graph.reobservations) == 1
    stage_run(second, lambda: uow_for(engine), FixedClock())
    with uow_for(engine) as uow:
        links = uow.canonicals.reobservations(second)
        assert links == summary.graph.reobservations
    assert canonical_counts(engine) == (1, 1, 1)
    third = prepared(engine, tmp_path / "third", PRODUCT.replace(",5,", ",6,"))
    summary = classify_run(third, default_registry(), uow_for(engine), FixedClock())
    assert summary.reviews
    stage_run(third, lambda: uow_for(engine), FixedClock())
    assert canonical_counts(engine) == (1, 1, 1)


def test_stage_preserves_excluded_reviews_and_terminal_repair_lineage(engine, tmp_path):
    from services.application.load import stage_run
    from services.domain.issues import Verdict

    csv = (
        CUSTOMER
        + PRODUCT.replace("SKU-2004", "SKU-00204")
        + PRODUCT.replace("SKU-2004", "SKU-00204")
        + PRODUCT.replace("SKU-2004", "SKU-2005").replace(",5,", ",many,")
        + "UNKNOWN,x\n"
        + ORDER.replace("Sofia Rossi", "Missing Person")
    )
    run_id = prepared(engine, tmp_path, csv)
    summary = classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    before = evidence(engine)
    assert {r.verdict for r in summary.results} == set(Verdict)
    result = stage_run(run_id, lambda: uow_for(engine), FixedClock())
    assert result.staged_count == 2
    with uow_for(engine) as uow:
        assert uow.reviews.for_run(run_id) == tuple(
            sorted(summary.reviews, key=lambda r: r.id)
        )
        accepted = uow.canonicals.prior_observations()
        assert any(p.candidate.revision_number > 1 for p in accepted)
        assert all(p.candidate in summary.graph.terminal for p in accepted)
    after = evidence(engine)
    # Only the separately owned canonical linkage can enrich a resolved dependency.
    assert {k: v for k, v in before.items() if k != "dependency_record"} == {
        k: v for k, v in after.items() if k != "dependency_record"
    }


def test_dependency_links_reobserved_canonical_and_replays_immutable_facts(
    engine, tmp_path
):
    from services.application.load import stage_run

    prior = prepared(engine, tmp_path / "prior", CUSTOMER + PRODUCT)
    classify_run(prior, default_registry(), uow_for(engine), FixedClock())
    stage_run(prior, lambda: uow_for(engine), FixedClock())
    current = prepared(engine, tmp_path / "current", CUSTOMER + PRODUCT + ORDER)
    summary = classify_run(current, default_registry(), uow_for(engine), FixedClock())
    assert len(summary.graph.reobservations) == 2
    before = evidence(engine)

    def fail(number):
        raise RuntimeError("injected load after prior observation")

    with pytest.raises(RuntimeError, match="injected load"):
        stage_run(current, lambda: uow_for(engine), FixedClock(), fail)
    assert evidence(engine) == before
    assert canonical_counts(engine) == (2, 2, 2)
    with uow_for(engine) as uow:
        assert uow.canonicals.reobservations(current) == summary.graph.reobservations
    result = stage_run(current, lambda: uow_for(engine), FixedClock())
    assert result.staged_count == 1
    assert canonical_counts(engine) == (3, 3, 3)
    with uow_for(engine) as uow:
        for d in summary.dependencies:
            uow.classifications.add_dependency(d)
            stored = uow.classifications.dependencies(d.classification_id)
            assert all(item.resolved_entity_id is not None for item in stored)
        from dataclasses import replace

        for link in summary.graph.reobservations:
            uow.canonicals.add_reobservation(link)
            other = next(
                item for item in summary.graph.reobservations if item.id != link.id
            )
            for changed in (
                replace(link, candidate_revision_id=other.candidate_revision_id),
                replace(link, identity_id=other.identity_id),
                replace(link, canonical_revision_id=other.canonical_revision_id),
            ):
                with pytest.raises(ValueError, match="identity mismatch"):
                    uow.canonicals.add_reobservation(changed)
        for dependency in summary.dependencies:
            other = next(d for d in summary.dependencies if d.id != dependency.id)
            with pytest.raises(ValueError, match="identity mismatch"):
                uow.classifications.add_dependency(
                    replace(
                        dependency,
                        target_candidate_revision_id=other.target_candidate_revision_id,
                    )
                )
            with pytest.raises(ValueError, match="linkage mismatch"):
                uow.canonicals.link_dependency(
                    dependency.id,
                    next(
                        link.identity_id
                        for link in summary.graph.reobservations
                        if link.candidate_revision_id
                        != dependency.target_candidate_revision_id
                    ),
                )
        uow.commit()


def test_governed_business_key_race_fails_closed(engine, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from services.application.load import stage_run

    first = prepared(engine, tmp_path / "first", PRODUCT)
    second = prepared(engine, tmp_path / "second", "\n" + PRODUCT)
    for run in (first, second):
        classify_run(run, default_registry(), uow_for(engine), FixedClock())
    barrier = Barrier(2)

    def load(run):
        def synchronize(number):
            if number == 2:
                barrier.wait(timeout=10)

        try:
            return stage_run(run, lambda: uow_for(engine), FixedClock(), synchronize)
        except Exception as error:
            return error

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(load, (first, second)))
    assert sum(isinstance(result, Exception) for result in outcomes) == 1
    assert canonical_counts(engine) == (1, 1, 1)
    with uow_for(engine) as uow:
        states = [uow.runs.get(run) for run in (first, second)]
    assert {(r.state, r.stage_failure) for r in states} == {
        (RunState.STAGED, None),
        (RunState.CLASSIFIED, "load_failed"),
    }


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE canonical_identity SET entity_type='customer'",
        "DELETE FROM canonical_business_key",
    ],
)
def test_staged_replay_rejects_changed_canonical_evidence(engine, tmp_path, statement):
    from services.application.load import stage_run

    run_id = prepared(engine, tmp_path, PRODUCT)
    classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    stage_run(run_id, lambda: uow_for(engine), FixedClock())
    with engine.begin() as conn:
        conn.execute(text(statement))
    with pytest.raises(ValueError, match="identity mismatch"):
        stage_run(run_id, lambda: uow_for(engine), FixedClock())


def test_reobservation_persistence_rolls_back_with_classification(
    engine, tmp_path, monkeypatch
):
    from services.application.load import stage_run
    from services.infrastructure.db.canonical_repository import (
        SqlAlchemyCanonicalRepository,
    )

    prior = prepared(engine, tmp_path / "prior", PRODUCT)
    classify_run(prior, default_registry(), uow_for(engine), FixedClock())
    stage_run(prior, lambda: uow_for(engine), FixedClock())
    current = prepared(engine, tmp_path / "current", "\n" + PRODUCT)
    before = evidence(engine)
    original = SqlAlchemyCanonicalRepository.add_reobservation

    def fail(self, link):
        original(self, link)
        raise RuntimeError("injected observation link")

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyCanonicalRepository, "add_reobservation", fail)
        with pytest.raises(RuntimeError, match="injected observation"):
            classify_run(current, default_registry(), uow_for(engine), FixedClock())
    assert evidence(engine) == before
    with uow_for(engine) as uow:
        assert not uow.canonicals.reobservations(current)
        assert uow.runs.get(current).state is RunState.NORMALISED
    summary = classify_run(current, default_registry(), uow_for(engine), FixedClock())
    assert len(summary.graph.reobservations) == 1
    assert canonical_counts(engine) == (1, 1, 1)
