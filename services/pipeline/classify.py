"""Complete-graph classification; one transaction after the revision-1 barrier."""

from dataclasses import dataclass, fields, replace
from uuid import UUID

from services.application.ports import Clock, UnitOfWork
from services.domain.candidates import (
    CustomerCandidate,
    OrderCandidate,
    RejectedCandidateShell,
)
from services.domain.fields import CandidateField, SourceRef
from services.domain.ids import deterministic_id
from services.domain.issues import (
    ClassificationResult,
    ComparisonScope,
    DataQualityIssue,
    DependencyKind,
    DependencyRecord,
    DependencyState,
    IssueCode,
    Readiness,
    ReviewItem,
    ReviewReason,
    Severity,
    Verdict,
)
from services.domain.observations import PriorObservation
from services.domain.runs import RunState
from services.pipeline.rules.base import RunCandidateGraph
from services.pipeline.rules.registry import RuleRegistry


@dataclass(frozen=True, slots=True)
class ClassificationSummary:
    graph: RunCandidateGraph
    results: tuple[ClassificationResult, ...]
    dependencies: tuple[DependencyRecord, ...]
    reviews: tuple[ReviewItem, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {
            verdict.value: sum(r.verdict == verdict for r in self.results)
            for verdict in Verdict
        }


def classify_graph(
    graph: RunCandidateGraph, registry: RuleRegistry
) -> ClassificationSummary:
    graph = registry.apply(graph)
    duplicate_raws = {
        d.later_raw_id
        for d in graph.duplicates
        if d.comparison_scope == ComparisonScope.SAME_RUN
    }
    observed = {r.candidate_revision_id for r in graph.reobservations}
    comparison_refs = {c.issue_id: c.conflict_refs for c in graph.comparisons}
    verdicts: dict[UUID, Verdict] = {}
    reasons: dict[UUID, list[ReviewReason]] = {}
    for revision in graph.terminal:
        chain = {
            r.id for r in graph.revisions if r.raw_record_id == revision.raw_record_id
        }
        issues = [i for i in graph.issues if i.candidate_revision_id in chain]
        reviewable = [i for i in issues if i.severity != Severity.INFO]
        reasons[revision.id] = [
            ReviewReason(
                i.id,
                i.field_path,
                i.summary,
                i.source_refs,
                conflict_refs=comparison_refs.get(i.id, ()),
            )
            for i in reviewable
        ]
        if revision.raw_record_id in duplicate_raws:
            verdict = Verdict.DUPLICATE
        elif isinstance(revision.payload, RejectedCandidateShell):
            verdict = Verdict.REJECTED
        elif reviewable:
            verdict = Verdict.NEEDS_REVIEW
        elif any(
            r.origin in ("SKU_ZERO_PADDING", "LINE_TOTAL_REPAIRED")
            for r in graph.revisions
            if r.id in chain
        ):
            verdict = Verdict.AUTO_REPAIRED
        else:
            verdict = Verdict.CLEAN
        verdicts[revision.id] = verdict
    ready = {
        revision_id
        for revision_id, verdict in verdicts.items()
        if verdict in (Verdict.CLEAN, Verdict.AUTO_REPAIRED)
    }
    # Fixed point propagates unavailable/unready targets without changing verdicts.
    while True:
        blocked = {
            d.source_revision_id
            for d in graph.dependencies
            if d.state != DependencyState.RESOLVED or d.target_revision_id not in ready
        }
        next_ready = ready - blocked
        if next_ready == ready:
            break
        ready = next_ready
    results: list[ClassificationResult] = []
    dependencies: list[DependencyRecord] = []
    reviews: list[ReviewItem] = []
    dependency_issues: list[DataQualityIssue] = []
    for revision in graph.terminal:
        verdict = verdicts[revision.id]
        readiness = (
            Readiness.ELIGIBLE
            if revision.id in ready
            else (
                Readiness.BLOCKED_BY_DEPENDENCY
                if verdict in (Verdict.CLEAN, Verdict.AUTO_REPAIRED)
                else Readiness.INELIGIBLE
            )
        )
        if revision.id in observed:
            readiness = Readiness.INELIGIBLE
        snapshot_id = graph.fx_snapshot.id if graph.fx_snapshot else None
        result = ClassificationResult(
            deterministic_id(
                revision.id, "classification", registry.rules_version, snapshot_id
            ),
            revision.id,
            registry.rules_version,
            snapshot_id,
            verdict,
            readiness,
            graph.evaluated_at,
        )
        results.append(result)
        for target in graph.dependencies:
            if target.source_revision_id != revision.id:
                continue
            state = target.state
            if (
                state == DependencyState.RESOLVED
                and target.target_revision_id not in ready
            ):
                state = DependencyState.BLOCKED
            dependency = DependencyRecord(
                deterministic_id(
                    result.id,
                    "dependency",
                    target.kind,
                    target.referenced_business_value,
                ),
                result.id,
                target.kind,
                target.referenced_business_value,
                None,
                state,
            )
            dependencies.append(dependency)
            if state != DependencyState.RESOLVED:
                payload = revision.payload
                refs = (SourceRef(revision.raw_record_id),)
                path = f"{revision.entity_type}.{target.kind}"
                if isinstance(payload, OrderCandidate):
                    refs = (
                        payload.sku.source_refs
                        if target.kind == DependencyKind.PRODUCT
                        else payload.customer_name_raw.source_refs
                    )
                elif isinstance(payload, CustomerCandidate):
                    refs = payload.notes.source_refs
                issue = DataQualityIssue(
                    deterministic_id(
                        revision.id,
                        registry.rules_version,
                        "dependency-issue",
                        dependency.id,
                    ),
                    revision.id,
                    IssueCode.UNRESOLVED_REFERENCE,
                    Severity.WARNING,
                    path,
                    f"{target.kind.value.title()} reference "
                    f"{target.referenced_business_value!r} is {state.value}.",
                    refs,
                )
                dependency_issues.append(issue)
                reasons[revision.id].append(
                    ReviewReason(issue.id, path, issue.summary, refs, (dependency.id,))
                )
        if reasons[revision.id]:
            reviews.append(
                ReviewItem(
                    deterministic_id(result.id, "review"),
                    graph.run_id,
                    revision.raw_record_id,
                    result.id,
                    tuple(reasons[revision.id]),
                    graph.evaluated_at,
                )
            )
    return ClassificationSummary(
        replace(graph, issues=(*graph.issues, *dependency_issues)),
        tuple(results),
        tuple(dependencies),
        tuple(reviews),
    )


def classify_run(
    run_id: UUID,
    registry: RuleRegistry,
    uow: UnitOfWork,
    clock: Clock,
    *,
    prior_observations: tuple[PriorObservation, ...] = (),
) -> ClassificationSummary:
    with uow:
        run = uow.runs.get(run_id)
        if run.state not in (RunState.NORMALISED, RunState.CLASSIFIED):
            raise ValueError(f"Cannot classify run in {run.state} state")
        raw = uow.raw_records.for_run(run_id)
        revisions = uow.candidates.for_run(run_id)
        initial = tuple(r for r in revisions if r.revision_number == 1)
        graph = RunCandidateGraph(
            run_id,
            raw,
            initial,
            None,
            clock.now(),
            prior_observations=prior_observations,
        )
        # No writer, including state/event writers, is reached before this check.
        graph.check_barrier()
        # Frozen initial payload refs identify normalization evidence. Validation
        # may also attach issues to revision 1; those are recomputed and verified.
        normalization_issues: set[UUID] = set()
        for revision in initial:
            if isinstance(revision.payload, RejectedCandidateShell):
                normalization_issues.update(revision.payload.issue_refs)
            else:
                for item in fields(revision.payload):
                    value: object = getattr(revision.payload, item.name)
                    if isinstance(value, CandidateField):
                        normalization_issues.update(value.issue_refs)
        graph = replace(
            graph,
            fx_snapshot=uow.fx.get(run.fx_snapshot_id) if run.fx_snapshot_id else None,
            issues=tuple(
                i
                for r in initial
                for i in uow.candidates.issues(r.id)
                if i.id in normalization_issues
            ),
            transformations=tuple(
                t for r in initial for t in uow.candidates.transformations(r.id)
            ),
        )
        summary = classify_graph(graph, registry)
        for revision in summary.graph.revisions:
            if revision.revision_number > 1:
                uow.candidates.add(revision)
        initial_issue_ids = {i.id for i in graph.issues}
        initial_transformation_ids = {t.id for t in graph.transformations}
        for issue in summary.graph.issues:
            if issue.id not in initial_issue_ids:
                uow.candidates.add_issue(issue)
        for event in summary.graph.transformations:
            if event.id not in initial_transformation_ids:
                uow.candidates.add_transformation(event)
        for result in summary.results:
            uow.classifications.add(result)
        for dependency in summary.dependencies:
            uow.classifications.add_dependency(dependency)
        for duplicate in summary.graph.duplicates:
            uow.classifications.add_duplicate(duplicate)
        for review in summary.reviews:
            uow.reviews.add(review)
        uow.runs.complete_classification(run_id, registry.rules_version, summary.counts)
        uow.commit()
        return summary
