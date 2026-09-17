"""Explicit SKU and price repairs, followed by pinned-date FX derivation."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import cast

from services.domain.candidates import (
    CandidatePayload,
    CandidateRevision,
    CustomerCandidate,
    EvidenceField,
    Money,
    OrderCandidate,
    ProductCandidate,
    candidate_revision_id,
)
from services.domain.fields import CandidateField, FieldState
from services.domain.issues import (
    DataQualityIssue,
    IssueCode,
    Severity,
    TransformationCode,
    TransformationEvent,
)
from services.pipeline.fx import convert_to_gbp
from services.pipeline.rules.base import (
    RuleEffect,
    RunCandidateGraph,
    classification_evidence_id,
)

SKU_REPAIRS = (("SKU-00204", "SKU-2004"),)


def child_revision(
    parent: CandidateRevision, payload: CandidatePayload, origin: str, at: datetime
) -> CandidateRevision:
    number = parent.revision_number + 1
    return CandidateRevision(
        candidate_revision_id(parent.raw_record_id, number),
        parent.raw_record_id,
        number,
        parent.id,
        origin,
        payload,
        at,
    )


def repair_field[T: (str, Money)](
    graph: RunCandidateGraph,
    parent: CandidateRevision,
    before: CandidateField[T],
    value: T,
    operation: TransformationCode,
    path: str,
    code: IssueCode,
) -> tuple[CandidateField[T], TransformationEvent, DataQualityIssue]:
    revision_id = candidate_revision_id(
        parent.raw_record_id, parent.revision_number + 1
    )
    event_id = classification_evidence_id(graph, revision_id, operation, path)
    issue_id = classification_evidence_id(graph, revision_id, code, path, "issue")
    after = replace(
        before,
        value=value,
        transformation_refs=(*before.transformation_refs, event_id),
        issue_refs=(*before.issue_refs, issue_id),
    )
    event = TransformationEvent(
        event_id,
        revision_id,
        operation,
        path,
        cast(EvidenceField, before),
        cast(EvidenceField, after),
        graph.next_sequence(parent),
    )
    issue = DataQualityIssue(
        issue_id,
        revision_id,
        code,
        Severity.INFO,
        path,
        f"Registered {operation} changed {before.value!r} to {value!r}.",
        before.source_refs,
    )
    return after, event, issue


def repair_skus(graph: RunCandidateGraph) -> tuple[RuleEffect, ...]:
    effects: list[RuleEffect] = []
    for parent in graph.terminal:
        payload = parent.payload
        if not isinstance(payload, (ProductCandidate, OrderCandidate)):
            continue
        mapped = dict(SKU_REPAIRS).get(payload.sku.value or "")
        if mapped is None:
            continue
        after, event, issue = repair_field(
            graph,
            parent,
            payload.sku,
            mapped,
            TransformationCode.SKU_ZERO_PADDING,
            f"{payload.entity_type}.sku",
            IssueCode.SKU_ZERO_PADDING,
        )
        child = child_revision(
            parent, replace(payload, sku=after), "SKU_ZERO_PADDING", graph.evaluated_at
        )
        effects.append(RuleEffect(parent.id, child, (issue,), (event,)))
    return tuple(effects)


def repair_line_totals(graph: RunCandidateGraph) -> tuple[RuleEffect, ...]:
    effects: list[RuleEffect] = []
    for parent in graph.terminal:
        payload = parent.payload
        if not isinstance(payload, OrderCandidate):
            continue
        amount, quantity = payload.unit_price.value, payload.quantity.value
        if amount is None or quantity is None or quantity <= 1:
            continue
        matches = [
            r.payload
            for r in graph.distinct_terminal
            if isinstance(r.payload, ProductCandidate)
            and r.payload.sku.value is not None
            and r.payload.sku.value == payload.sku.value
        ]
        if len(matches) != 1 or matches[0].unit_price.value is None:
            continue
        product_price = matches[0].unit_price.value
        # Approved export rule compares the source numeric unit price before FX.
        # It supports equal currencies and the documented USD-order/GBP-product case.
        if (amount.currency, product_price.currency) not in (
            (amount.currency, amount.currency),
            ("USD", "GBP"),
        ):
            continue
        with localcontext() as context:
            context.prec = max(
                28, len(amount.amount.as_tuple().digits) + len(str(quantity)) + 4
            )
            expected = product_price.amount * quantity
        if product_price.amount <= 0 or expected != amount.amount:
            continue
        after, event, issue = repair_field(
            graph,
            parent,
            payload.unit_price,
            Money(product_price.amount, amount.currency),
            TransformationCode.LINE_TOTAL_REPAIRED,
            "order.unit_price",
            IssueCode.LINE_TOTAL_REPAIRED,
        )
        # Carry the total, quantity, SKU link and product price through later FX.
        basis = tuple(
            dict.fromkeys(
                (
                    *after.source_refs,
                    *payload.quantity.source_refs,
                    *payload.sku.source_refs,
                    *matches[0].sku.source_refs,
                    *matches[0].unit_price.source_refs,
                )
            )
        )
        after = replace(after, source_refs=basis)
        event = replace(event, after=after)
        issue = replace(issue, source_refs=basis)
        child = child_revision(
            parent,
            replace(payload, unit_price=after),
            "LINE_TOTAL_REPAIRED",
            graph.evaluated_at,
        )
        effects.append(RuleEffect(parent.id, child, (issue,), (event,)))
    return tuple(effects)


def bind_fx(graph: RunCandidateGraph) -> tuple[RuleEffect, ...]:
    effects: list[RuleEffect] = []
    for parent in graph.terminal:
        payload = parent.payload
        if not isinstance(
            payload, (CustomerCandidate, ProductCandidate, OrderCandidate)
        ):
            continue
        source = (
            payload.lifetime_spend
            if isinstance(payload, CustomerCandidate)
            else payload.unit_price
        )
        amount = source.value
        if amount is None:
            continue
        path = f"{payload.entity_type}." + (
            "lifetime_spend_gbp"
            if isinstance(payload, CustomerCandidate)
            else "unit_price_gbp"
        )
        revision_id = candidate_revision_id(
            parent.raw_record_id, parent.revision_number + 1
        )
        issues: tuple[DataQualityIssue, ...] = ()
        events: tuple[TransformationEvent, ...] = ()
        requested: date | None = None
        operation = TransformationCode.FX_CONVERTED_AT_RUN_DATE
        if isinstance(payload, OrderCandidate):
            ordered = payload.ordered_at.value
            requested = ordered.date() if isinstance(ordered, datetime) else ordered
            operation = TransformationCode.FX_CONVERTED_AT_ORDER_DATE
        elif graph.fx_snapshot is not None:
            requested = graph.fx_snapshot.effective_at.date()
        if amount.currency == "GBP":
            target = CandidateField[Decimal].known(
                amount.amount, source_refs=source.source_refs
            )
        elif graph.fx_snapshot is not None and requested is not None:
            conversion = convert_to_gbp(
                amount.amount,
                amount.currency,
                requested,
                graph.fx_snapshot,
                operation=operation,
            )
            event = conversion.transformation_for_revision(
                revision_id,
                path,
                source.source_refs,
                sequence=graph.next_sequence(parent),
            )
            issue = conversion.issue_for_revision(revision_id, path, source.source_refs)
            if issue is not None:
                issue = replace(
                    issue,
                    id=classification_evidence_id(
                        graph, revision_id, "fx-unavailable", path
                    ),
                )
            if event is not None:
                events = (replace(event, before=source),)
                issue = DataQualityIssue(
                    classification_evidence_id(
                        graph, revision_id, "fx-explanation", path
                    ),
                    revision_id,
                    IssueCode.FX_CONVERTED_AT_RUN_DATE
                    if operation == TransformationCode.FX_CONVERTED_AT_RUN_DATE
                    else IssueCode.FX_CONVERTED,
                    Severity.INFO,
                    path,
                    f"Converted {amount.amount} {amount.currency} using ECB "
                    f"snapshot {graph.fx_snapshot.id} at {requested}.",
                    source.source_refs,
                )
            issues = (issue,) if issue else ()
            target = replace(conversion.gbp, source_refs=source.source_refs)
        else:
            issue = DataQualityIssue(
                classification_evidence_id(graph, revision_id, "fx-unavailable", path),
                revision_id,
                IssueCode.FX_RATE_UNAVAILABLE,
                Severity.ERROR,
                path,
                "FX conversion requires a pinned snapshot and a known conversion date.",
                source.source_refs,
            )
            issues = (issue,)
            target = CandidateField[Decimal](
                FieldState.UNRESOLVED, None, source.source_refs
            )
        target = replace(
            target,
            transformation_refs=(*source.transformation_refs, *(e.id for e in events)),
            issue_refs=(*source.issue_refs, *(i.id for i in issues)),
        )
        updated = (
            replace(payload, lifetime_spend_gbp=target)
            if isinstance(payload, CustomerCandidate)
            else replace(payload, unit_price_gbp=target)
        )
        child = child_revision(parent, updated, "FX_BINDING", graph.evaluated_at)
        effects.append(RuleEffect(parent.id, child, issues, events))
    return tuple(effects)
