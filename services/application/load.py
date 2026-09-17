"""Stage the complete persisted eligible set in one canonical transaction."""

from dataclasses import dataclass
from uuid import UUID

from services.application.ports import Clock, PipelineEvent, UnitOfWork
from services.application.process import (
    PIPELINE_EVENT_NAMESPACE,
    FailureInjector,
    UnitOfWorkFactory,
)
from services.domain.candidates import CandidateRevision
from services.domain.canonical import (
    CanonicalBusinessKey,
    CanonicalIdentity,
    CanonicalRevision,
)
from services.domain.ids import deterministic_id, new_id
from services.domain.issues import (
    ClassificationResult,
    DependencyRecord,
    DependencyState,
    Readiness,
    Verdict,
)
from services.domain.runs import RunState
from services.pipeline.rules.duplicates import business_key


@dataclass(frozen=True, slots=True)
class LoadResult:
    run_id: UUID
    staged_count: int


def eligible_candidates(
    revisions: tuple[CandidateRevision, ...],
    results: tuple[ClassificationResult, ...],
) -> tuple[CandidateRevision, ...]:
    terminal: dict[UUID, CandidateRevision] = {}
    for revision in revisions:
        previous = terminal.get(revision.raw_record_id)
        if previous is None or revision.revision_number > previous.revision_number:
            terminal[revision.raw_record_id] = revision
    by_id = {r.id: r for r in terminal.values()}
    if len(results) != len(by_id) or {r.candidate_revision_id for r in results} != set(
        by_id
    ):
        raise ValueError(
            "Load requires exactly one classification for every terminal candidate"
        )
    return tuple(
        by_id[r.candidate_revision_id]
        for r in results
        if r.verdict in (Verdict.CLEAN, Verdict.AUTO_REPAIRED)
        and r.readiness is Readiness.ELIGIBLE
    )


def _facts(
    uow: UnitOfWork, run_id: UUID
) -> tuple[
    tuple[CandidateRevision, ...],
    tuple[ClassificationResult, ...],
    tuple[DependencyRecord, ...],
]:
    run = uow.runs.get(run_id)
    revisions = uow.candidates.for_run(run_id)
    results = tuple(
        r
        for c in revisions
        for r in uow.classifications.for_revision(c.id)
        if r.rules_version == run.rules_version
        and r.fx_snapshot_id == run.fx_snapshot_id
    )
    dependencies = tuple(
        d for r in results for d in uow.classifications.dependencies(r.id)
    )
    return revisions, results, dependencies


def _event(
    uow: UnitOfWork,
    run_id: UUID,
    kind: str,
    attempt: int,
    clock: Clock,
    facts: dict[str, object],
) -> None:
    uow.events.append(
        PipelineEvent(
            deterministic_id(PIPELINE_EVENT_NAMESPACE, run_id, "load", kind, attempt),
            run_id,
            "load",
            kind,
            {**facts, "attempt_number": attempt},
            clock.now(),
        )
    )


def stage_run(
    run_id: UUID,
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    failure_injector: FailureInjector | None = None,
) -> LoadResult:
    # Finish selection and key validation before opening the write UoW.
    with uow_factory() as read:
        run = read.runs.get(run_id)
        if run.state not in (
            RunState.CLASSIFIED,
            RunState.STAGED,
        ) or run.stage_failure not in (None, "load_failed"):
            raise ValueError(f"Run cannot enter load from {run.state}")
        frozen = _facts(read, run_id)
        eligible = eligible_candidates(frozen[0], frozen[1])
        links = read.canonicals.reobservations(run_id)
        keys = {c.id: business_key(c.payload) for c in eligible}
        if any(key is None for key in keys.values()):
            raise ValueError("Eligible canonical candidate requires a governed key")
        if len(set(keys.values())) != len(keys):
            raise ValueError("Eligible set has conflicting governed business keys")
    try:
        with uow_factory() as write:
            # Run lock serializes identical staging attempts. Cross-run governed
            # key races are rejected by the database's unique key constraint.
            attempt = write.events.next_attempt_number(run_id, "load")
            current = write.runs.get(run_id)
            if current.state is RunState.STAGED:
                for candidate in eligible:
                    existing = write.canonicals.for_candidate(candidate.id)
                    if existing is None:
                        raise ValueError("Staged run is missing canonical evidence")
                    key = keys[candidate.id]
                    assert key is not None
                    write.canonicals.add_identity(
                        CanonicalIdentity(
                            existing.identity_id, key[0], existing.staged_at
                        )
                    )
                    write.canonicals.add_revision(
                        CanonicalRevision(
                            existing.id,
                            existing.identity_id,
                            candidate.id,
                            1,
                            existing.staged_at,
                        )
                    )
                    if write.canonicals.get_key(*key) != CanonicalBusinessKey(
                        existing.identity_id, key[0], key[1], existing.id
                    ):
                        raise ValueError("Canonical business key identity mismatch")
                return LoadResult(run_id, len(eligible))
            if (
                current.state is not RunState.CLASSIFIED
                or _facts(write, run_id) != frozen
                or write.canonicals.reobservations(run_id) != links
            ):
                raise ValueError("Classified evidence changed before canonical load")
            _event(
                write,
                run_id,
                "stage_retried" if current.stage_failure else "stage_started",
                attempt,
                clock,
                {},
            )
            write.runs.set_state(run_id, RunState.LOADING)
            identities = {
                link.candidate_revision_id: link.identity_id for link in links
            }
            inserted = 0

            def after_insert() -> None:
                nonlocal inserted
                inserted += 1
                if failure_injector is not None:
                    failure_injector(inserted)

            # Materialize identities/revisions/keys first, then resolve canonical
            # references. This also supports mutually referring eligible customers.
            for candidate in eligible:
                if write.canonicals.for_candidate(candidate.id) is not None:
                    raise ValueError("Unstaged run already contains canonical evidence")
                key = keys[candidate.id]
                assert key is not None
                staged_at = clock.now()
                identity = CanonicalIdentity(new_id(), key[0], staged_at)
                revision = CanonicalRevision(
                    new_id(), identity.id, candidate.id, 1, staged_at
                )
                write.canonicals.add_identity(identity)
                after_insert()
                write.canonicals.add_revision(revision)
                after_insert()
                write.canonicals.add_key(
                    CanonicalBusinessKey(identity.id, key[0], key[1], revision.id)
                )
                after_insert()
                identities[candidate.id] = identity.id
            for dependency in frozen[2]:
                target = dependency.target_candidate_revision_id
                if (
                    dependency.state is DependencyState.RESOLVED
                    and target in identities
                ):
                    assert target is not None
                    write.canonicals.link_dependency(dependency.id, identities[target])
            write.runs.set_state(run_id, RunState.STAGED)
            _event(
                write,
                run_id,
                "stage_completed",
                attempt,
                clock,
                {"staged_count": len(eligible)},
            )
            write.commit()
    except Exception as error:
        # The failed canonical transaction has exited and rolled back completely.
        with uow_factory() as failure:
            attempt = failure.events.next_attempt_number(run_id, "load")
            current = failure.runs.get(run_id)
            if current.state is not RunState.STAGED:
                _event(
                    failure,
                    run_id,
                    "stage_retried" if current.stage_failure else "stage_started",
                    attempt,
                    clock,
                    {},
                )
                failure.runs.set_state(
                    run_id, RunState.CLASSIFIED, stage_failure="load_failed"
                )
                _event(
                    failure,
                    run_id,
                    "stage_failed",
                    attempt,
                    clock,
                    {"error_type": type(error).__name__, "message": str(error)},
                )
                failure.commit()
        raise
    return LoadResult(run_id, len(eligible))
