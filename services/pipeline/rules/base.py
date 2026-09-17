"""Framework-free rule effects; candidate targets are not canonical identities."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol
from uuid import UUID

from services.domain.candidates import CandidateRevision
from services.domain.fx import FxSnapshot
from services.domain.issues import (
    DataQualityIssue,
    DependencyKind,
    DependencyState,
    TransformationEvent,
)
from services.domain.raw import RawRecord, RawRecordKind


@dataclass(frozen=True, slots=True)
class CandidateDependency:
    source_revision_id: UUID
    kind: DependencyKind
    referenced_business_value: str
    target_revision_id: UUID | None
    state: DependencyState


@dataclass(frozen=True, slots=True)
class RunCandidateGraph:
    run_id: UUID
    raw_records: tuple[RawRecord, ...]
    revisions: tuple[CandidateRevision, ...]
    fx_snapshot: FxSnapshot | None
    evaluated_at: datetime
    issues: tuple[DataQualityIssue, ...] = ()
    transformations: tuple[TransformationEvent, ...] = ()
    dependencies: tuple[CandidateDependency, ...] = ()
    rules_version: str = ""

    @property
    def terminal(self) -> tuple[CandidateRevision, ...]:
        latest: dict[UUID, CandidateRevision] = {}
        for revision in self.revisions:
            current = latest.get(revision.raw_record_id)
            if current is None or current.revision_number < revision.revision_number:
                latest[revision.raw_record_id] = revision
        return tuple(
            latest[raw.id]
            for raw in sorted(
                self.raw_records, key=lambda r: (r.source_line_start, str(r.id))
            )
            if raw.kind is RawRecordKind.DATA and raw.id in latest
        )

    def check_barrier(self) -> None:
        data_ids = {r.id for r in self.raw_records if r.kind is RawRecordKind.DATA}
        initial_ids = {
            r.raw_record_id for r in self.revisions if r.revision_number == 1
        }
        if data_ids != initial_ids or any(
            r.run_id != self.run_id for r in self.raw_records
        ):
            raise ValueError("Incomplete initial candidate barrier")

    def next_sequence(self, revision: CandidateRevision) -> int:
        chain = {
            r.id for r in self.revisions if r.raw_record_id == revision.raw_record_id
        }
        return (
            max(
                (
                    t.sequence
                    for t in self.transformations
                    if t.candidate_revision_id in chain
                ),
                default=0,
            )
            + 1
        )


@dataclass(frozen=True, slots=True)
class RuleEffect:
    candidate_revision_id: UUID
    revision: CandidateRevision | None = None
    issues: tuple[DataQualityIssue, ...] = ()
    transformations: tuple[TransformationEvent, ...] = ()
    dependencies: tuple[CandidateDependency, ...] = ()


class Rule(Protocol):
    def apply(self, graph: RunCandidateGraph) -> tuple[RuleEffect, ...]: ...


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    id: str
    version: int
    repairable: bool
    applies: Callable[[RunCandidateGraph], bool]
    evaluate: Callable[[RunCandidateGraph], tuple[RuleEffect, ...]]
    order: int = 100
    # Content-bearing configuration is hashed, independently of Python discovery.
    parameters: tuple[tuple[str, str], ...] = ()

    def apply(self, graph: RunCandidateGraph) -> tuple[RuleEffect, ...]:
        effects = self.evaluate(graph) if self.applies(graph) else ()
        if not self.repairable and any(e.revision is not None for e in effects):
            raise ValueError(f"validation-only rule {self.id} cannot append revisions")
        return effects


def apply_effects(graph: RunCandidateGraph, rule: RuleDefinition) -> RunCandidateGraph:
    effects = rule.apply(graph)
    terminal = {r.id: r for r in graph.terminal}
    revisions: list[CandidateRevision] = []
    for effect in effects:
        parent = terminal.get(effect.candidate_revision_id)
        if parent is None:
            raise ValueError("Rule effect must target a terminal candidate")
        child = effect.revision
        if child is not None:
            if (
                child.parent_revision_id != parent.id
                or child.raw_record_id != parent.raw_record_id
                or child.origin != rule.id
                or any(r.raw_record_id == child.raw_record_id for r in revisions)
            ):
                raise ValueError(
                    "Rule repair must append one immediate child per record"
                )
            revisions.append(child)
        target_id = child.id if child else parent.id
        if any(i.candidate_revision_id != target_id for i in effect.issues) or any(
            t.candidate_revision_id != target_id for t in effect.transformations
        ):
            raise ValueError(
                "Rule evidence must reference its exact candidate revision"
            )
    return replace(
        graph,
        revisions=(*graph.revisions, *revisions),
        issues=(*graph.issues, *(i for e in effects for i in e.issues)),
        transformations=(
            *graph.transformations,
            *(t for e in effects for t in e.transformations),
        ),
        dependencies=(
            *graph.dependencies,
            *(d for e in effects for d in e.dependencies),
        ),
    )
