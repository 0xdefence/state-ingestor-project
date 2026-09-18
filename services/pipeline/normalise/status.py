"""Registered entity-specific statuses; no inferred or case-insensitive mapping."""

from services.domain.fields import FieldState
from services.domain.issues import IssueCode, TransformationCode
from services.pipeline.normalise.evidence import (
    FieldEvidence,
    FieldPath,
    NormalisedField,
)

_CUSTOMER = {
    "Active": "active",
    "ACTIVE": "active",
    "active": "active",
    "Y": "active",
    "Inactive": "inactive",
    "inactive": "inactive",
}
_PRODUCT = {
    value: value
    for value in (
        "in_stock",
        "discontinued",
        "pending_review",
        "backordered",
    )
}
_ORDER = {value: value for value in ("pending", "shipped", "cancelled", "refunded")}
_STATUS = {
    "customer.status": _CUSTOMER,
    "product.status": _PRODUCT,
    "order.status": _ORDER,
}


def normalise_status(raw: str, field: FieldPath) -> NormalisedField[str]:
    evidence = FieldEvidence(raw, field)
    if not evidence.text:
        return evidence.finish(FieldState.ABSENT, None)
    value = _STATUS.get(field.path, {}).get(evidence.text)
    if value is None:
        evidence.issue(IssueCode.INVALID_STATUS, "Unsupported status")
        return evidence.finish(FieldState.UNRESOLVED, None)
    if value != evidence.text:
        evidence.transform(TransformationCode.STATUS_MAPPING, evidence.text, value)
    return evidence.finish(FieldState.KNOWN, value)


def supported_statuses(field_path: str) -> tuple[str, ...]:
    """Exact accepted source tokens from the normaliser's registered mapping."""
    return tuple(_STATUS.get(field_path, {}))
