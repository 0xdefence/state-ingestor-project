"""Registered evidence structures and independent assessment/readiness axes."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from services.domain.candidates import EvidenceField
from services.domain.fields import SourceRef


class Verdict(StrEnum):
    CLEAN = "CLEAN"
    AUTO_REPAIRED = "AUTO_REPAIRED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"


class Readiness(StrEnum):
    ELIGIBLE = "eligible"
    BLOCKED_BY_DEPENDENCY = "blocked_by_dependency"
    INELIGIBLE = "ineligible"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class IssueCode(StrEnum):
    INVALID_INTEGER = "INVALID_INTEGER"
    INVALID_MONEY = "INVALID_MONEY"
    AMBIGUOUS_MONEY = "AMBIGUOUS_MONEY"
    INVALID_DATE = "INVALID_DATE"
    INVALID_STATUS = "INVALID_STATUS"
    INVALID_TAGS = "INVALID_TAGS"
    INVALID_DISCRIMINATOR = "INVALID_DISCRIMINATOR"
    INVALID_STRUCTURE = "INVALID_STRUCTURE"
    MISSING_REQUIRED_VALUE = "MISSING_REQUIRED_VALUE"
    NOTES_REJOINED = "NOTES_REJOINED"
    FX_RATE_UNAVAILABLE = "FX_RATE_UNAVAILABLE"
    FX_CONVERTED = "FX_CONVERTED"
    FX_CONVERTED_AT_RUN_DATE = "FX_CONVERTED_AT_RUN_DATE"
    SKU_ZERO_PADDING = "SKU_ZERO_PADDING"
    LINE_TOTAL_REPAIRED = "LINE_TOTAL_REPAIRED"
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    INVALID_EMAIL = "INVALID_EMAIL"
    INVALID_CATEGORY = "INVALID_CATEGORY"
    UNKNOWN_TAG = "UNKNOWN_TAG"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    STOCK_STATUS_CONFLICT = "STOCK_STATUS_CONFLICT"
    REFUND_CONFLICT = "REFUND_CONFLICT"
    UNRESOLVED_REFERENCE = "UNRESOLVED_REFERENCE"
    DUPLICATE = "DUPLICATE"
    BUSINESS_KEY_CONFLICT = "BUSINESS_KEY_CONFLICT"


class TransformationCode(StrEnum):
    TRIM = "TRIM"
    PARSE_MONEY = "PARSE_MONEY"
    PARSE_DATE = "PARSE_DATE"
    PARSE_INTEGER = "PARSE_INTEGER"
    STATUS_MAPPING = "STATUS_MAPPING"
    PARSE_TAGS = "PARSE_TAGS"
    MATCH_KEY = "MATCH_KEY"
    NOTES_REJOINED = "NOTES_REJOINED"
    TRAILING_FIELDS_ABSENT = "TRAILING_FIELDS_ABSENT"
    TRAILING_EMPTY_FIELDS = "TRAILING_EMPTY_FIELDS"
    SKU_ZERO_PADDING = "SKU_ZERO_PADDING"
    LINE_TOTAL_REPAIRED = "LINE_TOTAL_REPAIRED"
    FX_CONVERTED = "FX_CONVERTED"


class DependencyKind(StrEnum):
    CUSTOMER = "customer"
    PRODUCT = "product"
    REFERRAL = "referral"


class DependencyState(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    BLOCKED = "blocked"


class ComparisonScope(StrEnum):
    SAME_RUN = "same_run"
    EARLIER_RUN = "earlier_run"


class ReviewState(StrEnum):
    OPEN = "open"


def require_derived_id(identity: UUID) -> None:
    if identity.version != 5:
        raise ValueError("derived identity must be UUIDv5")


@dataclass(frozen=True, slots=True)
class IssueDefinition:
    code: IssueCode
    severity: Severity
    applicable_fields: tuple[str, ...]
    automatic_repair: TransformationCode | None = None


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    id: UUID
    candidate_revision_id: UUID
    code: IssueCode
    severity: Severity
    field_path: str
    summary: str
    source_refs: tuple[SourceRef, ...]
    tentative_cause: str | None = None

    def __post_init__(self) -> None:
        require_derived_id(self.id)


@dataclass(frozen=True, slots=True)
class TransformationEvent:
    id: UUID
    candidate_revision_id: UUID
    operation: TransformationCode
    field_path: str
    before: EvidenceField
    after: EvidenceField
    sequence: int

    def __post_init__(self) -> None:
        require_derived_id(self.id)
        if self.sequence < 1:
            raise ValueError("transformation sequence must be positive")


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    id: UUID
    candidate_revision_id: UUID
    rules_version: str
    fx_snapshot_id: UUID | None
    verdict: Verdict
    readiness: Readiness
    evaluated_at: datetime

    def __post_init__(self) -> None:
        require_derived_id(self.id)


@dataclass(frozen=True, slots=True)
class DependencyRecord:
    id: UUID
    classification_id: UUID
    kind: DependencyKind
    referenced_business_value: str
    resolved_entity_id: UUID | None
    state: DependencyState

    def __post_init__(self) -> None:
        require_derived_id(self.id)
        if (
            self.resolved_entity_id is not None
            and self.state is not DependencyState.RESOLVED
        ):
            raise ValueError("only a resolved dependency can name a canonical entity")


@dataclass(frozen=True, slots=True)
class DuplicateRelation:
    id: UUID
    later_raw_id: UUID
    earlier_raw_id: UUID
    comparison_scope: ComparisonScope

    def __post_init__(self) -> None:
        require_derived_id(self.id)
        if self.later_raw_id == self.earlier_raw_id:
            raise ValueError("duplicate relation requires distinct raw records")


@dataclass(frozen=True, slots=True)
class ReviewReason:
    issue_id: UUID
    field_path: str
    summary: str
    source_refs: tuple[SourceRef, ...]
    dependency_refs: tuple[UUID, ...] = ()
    conflict_refs: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewItem:
    id: UUID
    run_id: UUID
    raw_record_id: UUID
    classification_id: UUID
    reasons: tuple[ReviewReason, ...]
    created_at: datetime
    display_state: ReviewState = ReviewState.OPEN

    def __post_init__(self) -> None:
        require_derived_id(self.id)
        if not self.reasons:
            raise ValueError("review item requires stable reasons")
