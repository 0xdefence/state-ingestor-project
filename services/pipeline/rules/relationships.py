"""Resolve exact SKU/ID and registered name keys without rewriting display text."""

import re

from services.domain.candidates import (
    CandidateRevision,
    CustomerCandidate,
    OrderCandidate,
    ProductCandidate,
)
from services.domain.issues import DependencyKind, DependencyState
from services.pipeline.rules.base import (
    CandidateDependency,
    RuleEffect,
    RunCandidateGraph,
)

REFERRAL_PATTERN = r"\b(?:Referred by|referral from) (CUST-\d{4})\b"


def relationships(graph: RunCandidateGraph) -> tuple[RuleEffect, ...]:
    effects: list[RuleEffect] = []
    for source in graph.terminal:
        payload = source.payload
        references: list[tuple[DependencyKind, str, str | None]] = []
        if isinstance(payload, OrderCandidate):
            references.extend(
                (
                    (
                        DependencyKind.PRODUCT,
                        payload.sku.value or "",
                        payload.sku.value,
                    ),
                    (
                        DependencyKind.CUSTOMER,
                        payload.customer_name_raw.value or "",
                        payload.customer_match_key.value,
                    ),
                )
            )
        elif isinstance(payload, CustomerCandidate) and payload.notes.value:
            references.extend(
                (DependencyKind.REFERRAL, identifier, identifier)
                for identifier in sorted(
                    set(re.findall(REFERRAL_PATTERN, payload.notes.value))
                )
            )
        dependencies: list[CandidateDependency] = []
        for kind, display, key in references:
            matches: list[CandidateRevision] = []
            for target in graph.terminal:
                candidate = target.payload
                if key is None or not key:
                    continue
                if (
                    (
                        kind is DependencyKind.PRODUCT
                        and isinstance(candidate, ProductCandidate)
                        and candidate.sku.value == key
                    )
                    or (
                        kind is DependencyKind.CUSTOMER
                        and isinstance(candidate, CustomerCandidate)
                        and candidate.name_match_key.value == key
                    )
                    or (
                        kind is DependencyKind.REFERRAL
                        and isinstance(candidate, CustomerCandidate)
                        and candidate.customer_id.value == key
                    )
                ):
                    matches.append(target)
            dependencies.append(
                CandidateDependency(
                    source.id,
                    kind,
                    display,
                    matches[0].id if len(matches) == 1 else None,
                    DependencyState.RESOLVED
                    if len(matches) == 1
                    else (
                        DependencyState.AMBIGUOUS
                        if matches
                        else DependencyState.UNRESOLVED
                    ),
                )
            )
        if dependencies:
            effects.append(RuleEffect(source.id, dependencies=tuple(dependencies)))
    return tuple(effects)
