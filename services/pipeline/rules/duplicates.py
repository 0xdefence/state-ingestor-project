"""Exact parsed occurrences and governed prior-value comparisons, without writes."""

from dataclasses import fields, is_dataclass
from typing import cast
from uuid import UUID

from services.domain.candidates import (
    CandidatePayload,
    CustomerCandidate,
    OrderCandidate,
    ProductCandidate,
)
from services.domain.fields import CandidateField, SourceRef
from services.domain.ids import deterministic_id
from services.domain.issues import (
    ComparisonScope,
    DataQualityIssue,
    DuplicateRelation,
    IssueCode,
    Severity,
)
from services.domain.observations import ComparisonEvidence, Reobservation
from services.pipeline.rules.base import RuleEffect, RunCandidateGraph


def business_key(payload: CandidatePayload) -> tuple[str, str] | None:
    if isinstance(payload, CustomerCandidate):
        value = payload.customer_id.value
    elif isinstance(payload, ProductCandidate):
        value = payload.sku.value
    elif isinstance(payload, OrderCandidate):
        value = payload.order_id.value
    else:
        return None
    return (str(payload.entity_type), value) if value else None


def _typed_values(value: object) -> object:
    # Provenance differs by occurrence; field states and typed values do not.
    if isinstance(value, CandidateField):
        field = cast(CandidateField[object], value)
        return (field.state, _typed_values(field.value))
    if is_dataclass(value) and not isinstance(value, type):
        return (
            type(value),
            tuple(
                (f.name, _typed_values(getattr(value, f.name))) for f in fields(value)
            ),
        )
    if isinstance(value, tuple):
        return (tuple, tuple(_typed_values(v) for v in cast(tuple[object, ...], value)))
    return (type(value), value)


def same_typed_values(left: object, right: object) -> bool:
    return _typed_values(left) == _typed_values(right)


def comparisons(graph: RunCandidateGraph) -> tuple[RuleEffect, ...]:
    raw_by_id = {r.id: r for r in graph.raw_records}
    exact: dict[tuple[str, ...], UUID] = {}
    keys: dict[tuple[str, str], UUID] = {}
    effects: list[RuleEffect] = []
    snapshot = graph.fx_snapshot.id if graph.fx_snapshot else None
    for revision in graph.terminal:
        raw = raw_by_id[revision.raw_record_id]
        key = business_key(revision.payload)
        earlier = exact.get(raw.fields)
        conflict_refs: tuple[UUID, ...] = ()
        code: IssueCode | None = None
        relations: tuple[DuplicateRelation, ...] = ()
        reobservations: tuple[Reobservation, ...] = ()
        scope = ComparisonScope.SAME_RUN
        if earlier is not None:
            code = IssueCode.DUPLICATE
            conflict_refs = (earlier,)
        elif key is not None and key in keys:
            code = IssueCode.BUSINESS_KEY_CONFLICT
            conflict_refs = (keys[key],)
        elif key is not None:
            prior = tuple(
                sorted(
                    (
                        p
                        for p in graph.prior_observations
                        if p.run_id != graph.run_id
                        and business_key(p.candidate.payload) == key
                    ),
                    key=lambda p: (str(p.identity_id), str(p.canonical_revision_id)),
                )
            )
            if len(prior) == 1 and _typed_values(
                prior[0].candidate.payload
            ) == _typed_values(revision.payload):
                observed = prior[0]
                earlier = observed.candidate.raw_record_id
                scope = ComparisonScope.EARLIER_RUN
                reobservations = (
                    Reobservation(
                        deterministic_id(
                            revision.id,
                            graph.rules_version,
                            snapshot,
                            "reobservation",
                            observed.identity_id,
                            observed.canonical_revision_id,
                        ),
                        revision.id,
                        observed.identity_id,
                        observed.canonical_revision_id,
                    ),
                )
            elif prior:
                code = IssueCode.BUSINESS_KEY_CONFLICT
                conflict_refs = tuple(
                    ref
                    for p in prior
                    for ref in (p.identity_id, p.canonical_revision_id)
                )
        if earlier is not None:
            relations = (
                DuplicateRelation(
                    deterministic_id(
                        revision.id,
                        graph.rules_version,
                        snapshot,
                        "duplicate",
                        scope,
                        earlier,
                    ),
                    raw.id,
                    earlier,
                    scope,
                ),
            )
        issues: tuple[DataQualityIssue, ...] = ()
        evidence: tuple[ComparisonEvidence, ...] = ()
        if code is not None:
            issue = DataQualityIssue(
                deterministic_id(
                    revision.id, graph.rules_version, snapshot, code, *conflict_refs
                ),
                revision.id,
                code,
                Severity.WARNING,
                f"{revision.entity_type}.identity",
                "Exact parsed fields repeat an earlier occurrence."
                if code == IssueCode.DUPLICATE
                else (
                    "Business identity has different observed values; "
                    "existing evidence is retained."
                ),
                (SourceRef(raw.id),),
            )
            issues = (issue,)
            evidence = (ComparisonEvidence(revision.id, issue.id, conflict_refs),)
        effects.append(
            RuleEffect(
                revision.id,
                issues=issues,
                duplicates=relations,
                reobservations=reobservations,
                comparisons=evidence,
            )
        )
        exact.setdefault(raw.fields, raw.id)
        if key is not None:
            keys.setdefault(key, raw.id)
    return tuple(effects)
