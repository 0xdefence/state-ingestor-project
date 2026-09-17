"""LOD-07..09: load recovery over fixed, already-classified evidence."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.application.ports import Run
from services.application.process import ProcessRun, RetryRun, StageResult
from services.domain.runs import RunState
from services.pipeline.classify import ClassificationSummary
from tests.unit.classify.test_rules import (
    CUSTOMER,
    NOW,
    ORDER,
    PRODUCT,
    FixedClock,
    assess,
)


class OrchestrationStore:
    def __init__(
        self,
        state: RunState = RunState.INGESTED,
        *,
        stage_failure: str | None = None,
    ) -> None:
        self.run_id = uuid4()
        self.snapshot_id = uuid4()
        self.run = Run(
            self.run_id,
            uuid4(),
            None,
            0,
            state,
            NOW,
            stage_failure=stage_failure,
            counts={"CLEAN": 31, "NEEDS_REVIEW": 17}
            if state in (RunState.CLASSIFIED, RunState.LOADING, RunState.STAGED)
            else None,
        )
        self.commits = 0

    def advance(self, state: RunState, *, counts: dict[str, int] | None = None) -> None:
        self.run = replace(
            self.run,
            state=state,
            stage_failure=None,
            counts=counts if counts is not None else self.run.counts,
        )

    def uow(self):
        owner = self

        class Work:
            def __enter__(self):
                self.runs = SimpleNamespace(
                    get=lambda _: owner.run,
                    pin_fx_snapshot=self.pin_fx_snapshot,
                    promoted_count=lambda _: 31,
                )
                self.fx = SimpleNamespace(
                    latest=lambda: SimpleNamespace(id=owner.snapshot_id)
                )
                self.checkpoints = SimpleNamespace(
                    get=lambda _run_id, _stage: SimpleNamespace(record_ordinal=52)
                )
                return self

            def pin_fx_snapshot(self, _, snapshot_id):
                owner.run = replace(owner.run, fx_snapshot_id=snapshot_id)

            def commit(self):
                owner.commits += 1

            def __exit__(self, *_):
                pass

        return Work()


def _classification(store: OrchestrationStore) -> ClassificationSummary:
    return SimpleNamespace(counts={"CLEAN": 31, "NEEDS_REVIEW": 17})


def test_process_dispatches_each_stage_until_staged(monkeypatch) -> None:
    from services.application import process

    store = OrchestrationStore()
    calls: list[str] = []

    def parse(*_args, **_kwargs):
        calls.append("parse")
        store.advance(RunState.PARSED)
        return StageResult(store.run_id, 52)

    def normalise(*_args, **_kwargs):
        calls.append("normalise")
        store.advance(RunState.NORMALISED)
        return StageResult(store.run_id, 52)

    def classify(*_args, **_kwargs):
        calls.append("classify")
        store.advance(
            RunState.CLASSIFIED, counts={"CLEAN": 31, "NEEDS_REVIEW": 17}
        )
        return _classification(store)

    def load(*_args, **_kwargs):
        calls.append("load")
        store.advance(RunState.STAGED)
        return SimpleNamespace(run_id=store.run_id, staged_count=31)

    monkeypatch.setattr(process, "parse_run", parse)
    monkeypatch.setattr(process, "normalise_run", normalise)
    monkeypatch.setattr(process, "classify_run", classify)
    monkeypatch.setattr(process, "stage_run", load)

    result = process.process_run(
        ProcessRun(store.run_id, 10), store.uow, SimpleNamespace(), FixedClock()
    )

    assert calls == ["parse", "normalise", "classify", "load"]
    assert store.run.fx_snapshot_id == store.snapshot_id
    assert store.commits == 1
    assert result.parse.record_count == result.normalise.record_count == 52
    assert result.classification_counts == {"CLEAN": 31, "NEEDS_REVIEW": 17}
    assert (result.state, result.promoted_count) == (RunState.STAGED, 31)


def test_staged_run_is_a_no_write_replay(monkeypatch) -> None:
    from services.application import process

    store = OrchestrationStore(RunState.STAGED)
    store.run = replace(store.run, fx_snapshot_id=store.snapshot_id)
    for name in ("parse_run", "normalise_run", "classify_run", "stage_run"):
        monkeypatch.setattr(
            process,
            name,
            lambda *_args, stage=name, **_kwargs: pytest.fail(
                f"staged replay dispatched {stage}"
            ),
        )

    result = process.process_run(
        ProcessRun(store.run_id), store.uow, SimpleNamespace(), FixedClock()
    )

    assert store.commits == 0
    assert result.state is RunState.STAGED
    assert result.parse.record_count == result.normalise.record_count == 52
    assert result.classification_counts == {"CLEAN": 31, "NEEDS_REVIEW": 17}
    assert result.promoted_count == 31


@pytest.mark.parametrize(
    ("state", "failure", "expected"),
    [
        (RunState.PARSING, "parse_failed", ["parse", "normalise", "classify", "load"]),
        (RunState.NORMALISING, "normalise_failed", ["normalise", "classify", "load"]),
        (RunState.NORMALISED, "classify_failed", ["classify", "load"]),
        (RunState.CLASSIFIED, "load_failed", ["load"]),
    ],
)
def test_retry_resumes_from_persisted_stage(
    monkeypatch, state: RunState, failure: str, expected: list[str]
) -> None:
    from services.application import process

    store = OrchestrationStore(state, stage_failure=failure)
    calls: list[str] = []

    def stage(name: str, next_state: RunState, result: object):
        def call(*_args, **_kwargs):
            calls.append(name)
            counts = (
                {"CLEAN": 31, "NEEDS_REVIEW": 17}
                if next_state is RunState.CLASSIFIED
                else None
            )
            store.advance(next_state, counts=counts)
            return result

        return call

    monkeypatch.setattr(
        process,
        "parse_run",
        stage("parse", RunState.PARSED, StageResult(store.run_id, 52)),
    )
    monkeypatch.setattr(
        process,
        "normalise_run",
        stage("normalise", RunState.NORMALISED, StageResult(store.run_id, 52)),
    )
    monkeypatch.setattr(
        process,
        "classify_run",
        stage("classify", RunState.CLASSIFIED, _classification(store)),
    )
    monkeypatch.setattr(
        process,
        "stage_run",
        stage(
            "load",
            RunState.STAGED,
            SimpleNamespace(run_id=store.run_id, staged_count=31),
        ),
    )

    result = process.retry_run(
        RetryRun(store.run_id, 10), store.uow, SimpleNamespace(), FixedClock()
    )

    assert calls == expected
    assert result.state is RunState.STAGED
    assert result.promoted_count == 31


@pytest.mark.parametrize(
    ("state", "failure"),
    [
        (RunState.CLASSIFYING, "classify_failed"),
        (RunState.LOADING, "load_failed"),
    ],
)
def test_retry_rejects_unsupported_transient_state(
    monkeypatch, state: RunState, failure: str
) -> None:
    from services.application import process

    store = OrchestrationStore(state, stage_failure=failure)
    for name in ("parse_run", "normalise_run", "classify_run", "stage_run"):
        monkeypatch.setattr(
            process,
            name,
            lambda *_args, stage=name, **_kwargs: pytest.fail(
                f"transient-state retry dispatched {stage}"
            ),
        )

    with pytest.raises(
        ValueError, match=f"Run cannot be processed from {state.value}"
    ):
        process.retry_run(
            RetryRun(store.run_id, 10), store.uow, SimpleNamespace(), FixedClock()
        )
    assert store.commits == 0


class MemoryLoad:
    """Transactional test store exposing only load's read and write ports."""

    def __init__(self):
        self.summary = assess(CUSTOMER + PRODUCT + ORDER)
        self.run_id = self.summary.graph.run_id
        result = self.summary.results[0]
        self.data = {
            "run": Run(
                self.run_id,
                uuid4(),
                None,
                0,
                RunState.CLASSIFIED,
                NOW,
                fx_snapshot_id=result.fx_snapshot_id,
                rules_version=result.rules_version,
            ),
            "identities": {},
            "revisions": {},
            "keys": {},
            "promotions": [],
            "current": {},
            "events": [],
            "dependencies": {d.id: d for d in self.summary.dependencies},
        }
        self.opened = 0

    def uow(self):
        owner = self

        class Work:
            def __enter__(self):
                owner.opened += 1
                self.local = deepcopy(owner.data)
                self.runs = SimpleNamespace(get=self.get_run, set_state=self.set_state)
                # These ports deliberately have no stage writers: load must read
                # existing evidence, never invoke normalization/classification.
                self.candidates = SimpleNamespace(
                    for_run=lambda _: owner.summary.graph.revisions
                )
                self.classifications = SimpleNamespace(
                    for_revision=lambda revision: tuple(
                        r
                        for r in owner.summary.results
                        if r.candidate_revision_id == revision
                    ),
                    dependencies=lambda classification: tuple(
                        d
                        for d in self.local["dependencies"].values()
                        if d.classification_id == classification
                    ),
                )
                self.events = SimpleNamespace(
                    next_attempt_number=lambda *_: (
                        1
                        + sum(
                            e.event_type in ("stage_started", "stage_retried")
                            for e in self.local["events"]
                        )
                    ),
                    append=self.local["events"].append,
                )
                self.canonicals = SimpleNamespace(
                    activate=self.activate,
                    reobservations=lambda _: (),
                    get_key=lambda kind, value: self.local["keys"].get((kind, value)),
                    add_identity=lambda value: self.local["identities"].setdefault(
                        value.id, value
                    ),
                    add_revision=lambda value: self.local["revisions"].setdefault(
                        value.id, value
                    ),
                    add_key=lambda value: self.local["keys"].setdefault(
                        (value.key_type, value.value), value
                    ),
                    for_candidate=lambda candidate: next(
                        (
                            v
                            for v in self.local["revisions"].values()
                            if v.candidate_revision_id == candidate
                        ),
                        None,
                    ),
                    link_dependency=self.link_dependency,
                )
                return self

            def get_run(self, run_id):
                if run_id != self.local["run"].id:
                    raise LookupError("Unknown run")
                return self.local["run"]

            def activate(self, event):
                self.local["promotions"].append(event)
                self.local["current"][event.identity_id] = event.canonical_revision_id

            def link_dependency(self, dependency_id, identity_id):
                self.local["dependencies"][dependency_id] = replace(
                    self.local["dependencies"][dependency_id],
                    resolved_entity_id=identity_id,
                )

            def set_state(self, _, state, *, stage_failure=None):
                self.local["run"] = replace(
                    self.local["run"], state=state, stage_failure=stage_failure
                )

            def commit(self):
                owner.data = self.local

            def __exit__(self, *_):
                pass

        return Work()


@pytest.mark.parametrize("boundary", [1, 5, 9])
def test_load_retry_reads_classifications_and_stages_exactly_once(boundary):
    from services.application.load import stage_run

    store = MemoryLoad()
    summary = assess(CUSTOMER + PRODUCT + ORDER)

    def interrupt(number):
        if number == boundary:
            raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        stage_run(store.run_id, store.uow, FixedClock(), interrupt)
    assert not store.data["identities"]
    assert not store.data["revisions"]
    assert not store.data["keys"]
    assert not store.data["promotions"]
    assert not store.data["current"]
    assert store.data["run"].stage_failure == "load_failed"
    assert store.data["run"].state is RunState.CLASSIFIED
    assert stage_run(store.run_id, store.uow, FixedClock()).staged_count == 3
    stable = deepcopy(store.data)
    assert stage_run(store.run_id, store.uow, FixedClock()).staged_count == 3
    assert store.data == stable
    assert store.summary == summary
    assert [
        (e.event_type, e.facts["attempt_number"]) for e in store.data["events"]
    ] == [
        ("stage_started", 1),
        ("stage_failed", 1),
        ("stage_retried", 2),
        ("stage_completed", 2),
    ]


def test_entire_eligible_set_is_validated_before_write_unit_of_work():
    from services.application.load import stage_run

    store = MemoryLoad()
    store.summary = replace(store.summary, results=store.summary.results[:-1])
    with pytest.raises(ValueError, match="terminal"):
        stage_run(store.run_id, store.uow, FixedClock())
    # The second UoW records failure; no canonical write UoW is reached.
    assert store.opened == 2
    assert not store.data["identities"]
    assert not store.data["revisions"]
    assert not store.data["keys"]
    assert not store.data["promotions"]
    assert not store.data["current"]
    assert store.data["run"].state is RunState.CLASSIFIED
    assert store.data["run"].stage_failure == "load_failed"
    assert [
        (e.event_type, e.facts["attempt_number"]) for e in store.data["events"]
    ] == [("stage_started", 1), ("stage_failed", 1)]
    assert store.data["events"][-1].facts["error_type"] == "ValueError"


def test_classification_retry_does_not_duplicate_results():
    first = assess(CUSTOMER + PRODUCT + ORDER)
    retried = assess(CUSTOMER + PRODUCT + ORDER)
    assert first == retried
    assert len({r.id for r in first.results + retried.results}) == len(first.results)
    assert len({i.id for i in first.graph.issues + retried.graph.issues}) == len(
        first.graph.issues
    )


@pytest.mark.parametrize("unknown", [False, True])
def test_unknown_run_and_illegal_load_entry_have_no_side_effects(unknown):
    from services.application.load import stage_run

    store = MemoryLoad()
    run_id = uuid4() if unknown else store.run_id
    if not unknown:
        store.data["run"] = replace(store.data["run"], state=RunState.NORMALISED)
    before = deepcopy(store.data)
    with pytest.raises(LookupError if unknown else ValueError):
        stage_run(run_id, store.uow, FixedClock())
    assert store.data == before
    assert store.opened == 1
