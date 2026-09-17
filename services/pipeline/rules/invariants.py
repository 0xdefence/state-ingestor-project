"""Validation-only refund and stock/status rules."""

from services.domain.candidates import (
    CandidateRevision,
    OrderCandidate,
    ProductCandidate,
)
from services.domain.fields import SourceRef
from services.domain.issues import DataQualityIssue, IssueCode, Severity
from services.pipeline.rules.base import (
    RuleEffect,
    RunCandidateGraph,
    classification_evidence_id,
)


def validation_issue(
    graph: RunCandidateGraph,
    revision: CandidateRevision,
    code: IssueCode,
    path: str,
    summary: str,
    refs: tuple[SourceRef, ...],
) -> RuleEffect:
    return RuleEffect(
        revision.id,
        issues=(
            DataQualityIssue(
                classification_evidence_id(graph, revision.id, code, path),
                revision.id,
                code,
                Severity.ERROR,
                path,
                summary,
                refs,
            ),
        ),
    )


def refund_invariant(graph: RunCandidateGraph) -> tuple[RuleEffect, ...]:
    effects: list[RuleEffect] = []
    for revision in graph.terminal:
        payload = revision.payload
        if not isinstance(payload, OrderCandidate):
            continue
        quantity, status = payload.quantity.value, payload.status.value
        if (
            quantity is not None
            and status is not None
            and (
                quantity < 0
                and status != "refunded"
                or quantity >= 0
                and status == "refunded"
            )
        ):
            effects.append(
                validation_issue(
                    graph,
                    revision,
                    IssueCode.REFUND_CONFLICT,
                    "order.quantity",
                    f"Quantity {quantity} conflicts with order status {status!r}.",
                    (*payload.quantity.source_refs, *payload.status.source_refs),
                )
            )
    return tuple(effects)


def stock_invariant(graph: RunCandidateGraph) -> tuple[RuleEffect, ...]:
    effects: list[RuleEffect] = []
    for revision in graph.terminal:
        payload = revision.payload
        if not isinstance(payload, ProductCandidate):
            continue
        stock, status = payload.stock_qty.value, payload.status.value
        if stock is None or status not in ("backordered", "discontinued", "in_stock"):
            continue
        valid = (
            status == "backordered"
            and stock < 0
            or status == "discontinued"
            and stock == 0
            or status == "in_stock"
            and stock > 0
        )
        if not valid:
            effects.append(
                validation_issue(
                    graph,
                    revision,
                    IssueCode.STOCK_STATUS_CONFLICT,
                    "product.stock_qty",
                    f"Stock quantity {stock} conflicts with product status {status!r}.",
                    (*payload.stock_qty.source_refs, *payload.status.source_refs),
                )
            )
    return tuple(effects)
