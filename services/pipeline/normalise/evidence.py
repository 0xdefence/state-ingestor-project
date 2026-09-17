"""Pure field evidence for the initial revision; assembly binds revision IDs.

Callers must supply actual source references when assembling persisted candidates.
The two-argument normalizer APIs also allow evidence-free standalone value probes.
Child identities include source coordinates, field path, raw text and operation;
they are stable for retries and are never canonical entity identities.
"""

from dataclasses import dataclass
from types import MappingProxyType
from uuid import NAMESPACE_URL, UUID

from services.domain.candidates import FieldValue
from services.domain.fields import CandidateField, FieldState, SourceRef
from services.domain.ids import deterministic_id
from services.domain.issues import (
    DataQualityIssue,
    IssueCode,
    IssueDefinition,
    Severity,
    TransformationCode,
    TransformationEvent,
)


@dataclass(frozen=True, slots=True)
class FieldPath:
    path: str
    source_refs: tuple[SourceRef, ...] = ()


ISSUE_DEFINITIONS = MappingProxyType(
    {
        code: IssueDefinition(code, severity, fields)
        for code, severity, fields in (
            (
                IssueCode.INVALID_MONEY,
                Severity.ERROR,
                ("customer.lifetime_spend", "product.unit_price", "order.unit_price"),
            ),
            (
                IssueCode.AMBIGUOUS_MONEY,
                Severity.WARNING,
                ("customer.lifetime_spend", "product.unit_price", "order.unit_price"),
            ),
            (
                IssueCode.INVALID_DATE,
                Severity.ERROR,
                ("customer.signup_date", "product.listed_date", "order.ordered_at"),
            ),
            (
                IssueCode.INVALID_STATUS,
                Severity.ERROR,
                ("customer.status", "product.status", "order.status"),
            ),
            (
                IssueCode.INVALID_TAGS,
                Severity.ERROR,
                ("customer.tags", "product.tags", "order.tags"),
            ),
            (
                IssueCode.INVALID_INTEGER,
                Severity.ERROR,
                ("product.stock_qty", "order.quantity"),
            ),
        )
    }
)


@dataclass(frozen=True, slots=True)
class PendingIssue:
    id: UUID
    code: IssueCode
    field: FieldPath
    summary: str

    def for_revision(self, revision_id: UUID) -> DataQualityIssue:
        definition = ISSUE_DEFINITIONS[self.code]
        return DataQualityIssue(
            self.id,
            revision_id,
            self.code,
            definition.severity,
            self.field.path,
            self.summary,
            self.field.source_refs,
        )


@dataclass(frozen=True, slots=True)
class PendingTransformation:
    id: UUID
    operation: TransformationCode
    field: FieldPath
    before: CandidateField[FieldValue]
    after: CandidateField[FieldValue]
    sequence: int

    def for_revision(
        self, revision_id: UUID, *, sequence: int | None = None
    ) -> TransformationEvent:
        """Bind evidence, using assembly's revision-global sequence when supplied."""
        return TransformationEvent(
            self.id,
            revision_id,
            self.operation,
            self.field.path,
            self.before,
            self.after,
            self.sequence if sequence is None else sequence,
        )


@dataclass(frozen=True, slots=True)
class NormalisedField[T](CandidateField[T]):
    raw_value: str = ""
    issues: tuple[PendingIssue, ...] = ()
    transformations: tuple[PendingTransformation, ...] = ()

    def as_candidate_field(self) -> CandidateField[T]:
        """Extract the domain value; persist pending evidence separately."""
        return CandidateField(
            self.state,
            self.value,
            self.source_refs,
            self.transformation_refs,
            self.issue_refs,
        )


class FieldEvidence:
    """A call-local builder; returned values and evidence are immutable."""

    def __init__(self, raw: str, field: FieldPath) -> None:
        self.raw = raw
        self.field = field
        self.text = raw.strip()
        self.issues: list[PendingIssue] = []
        self.transformations: list[PendingTransformation] = []
        if self.text != raw:
            self.transform(TransformationCode.TRIM, raw, self.text)

    def identity(self, kind: str, code: str, ordinal: int) -> UUID:
        coordinates = tuple(
            (str(ref.raw_record_id), ref.field_index) for ref in self.field.source_refs
        )
        return deterministic_id(
            NAMESPACE_URL,
            "initial-field-normalisation-v1",
            coordinates,
            self.field.path,
            self.raw,
            kind,
            code,
            ordinal,
        )

    def issue(self, code: IssueCode, summary: str) -> None:
        definition = ISSUE_DEFINITIONS[code]
        if self.field.path not in definition.applicable_fields:
            raise ValueError(f"{code} is not applicable to {self.field.path}")
        self.issues.append(
            PendingIssue(
                self.identity("issue", code, len(self.issues) + 1),
                code,
                self.field,
                f"{summary}; raw value: {self.raw!r}",
            )
        )

    def transform(
        self,
        operation: TransformationCode,
        before: FieldValue,
        after: FieldValue,
    ) -> None:
        sequence = len(self.transformations) + 1
        self.transformations.append(
            PendingTransformation(
                self.identity("transformation", operation, sequence),
                operation,
                self.field,
                CandidateField[FieldValue].known(
                    before, source_refs=self.field.source_refs
                ),
                CandidateField[FieldValue].known(
                    after, source_refs=self.field.source_refs
                ),
                sequence,
            )
        )

    def finish[T](self, state: FieldState, value: T | None) -> NormalisedField[T]:
        return NormalisedField(
            state,
            value,
            self.field.source_refs,
            tuple(event.id for event in self.transformations),
            tuple(issue.id for issue in self.issues),
            self.raw,
            tuple(self.issues),
            tuple(self.transformations),
        )
