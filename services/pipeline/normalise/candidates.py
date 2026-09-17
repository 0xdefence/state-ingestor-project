"""Typed initial revisions from explicit entity/shape rules and field evidence."""

from dataclasses import dataclass, replace
from types import MappingProxyType

from services.application.ports import Clock
from services.domain.candidates import (
    CandidatePayload,
    CandidateRevision,
    CustomerCandidate,
    EntityType,
    OrderCandidate,
    ProductCandidate,
    RejectedCandidateShell,
    candidate_revision_id,
)
from services.domain.fields import CandidateField, FieldState, SourceRef
from services.domain.ids import deterministic_id
from services.domain.issues import (
    DataQualityIssue,
    IssueCode,
    TransformationCode,
    TransformationEvent,
)
from services.domain.raw import RawRecord, RawRecordKind
from services.pipeline.normalise.dates import normalise_date
from services.pipeline.normalise.evidence import (
    FieldEvidence,
    FieldPath,
    NormalisedField,
    PendingIssue,
)
from services.pipeline.normalise.money import normalise_money
from services.pipeline.normalise.status import normalise_status
from services.pipeline.normalise.tags import normalise_tags
from services.pipeline.normalise.text import (
    match_key,
    normalise_integer,
    normalise_text,
)


@dataclass(frozen=True, slots=True)
class NormaliseContext:
    clock: Clock


@dataclass(frozen=True, slots=True)
class NormaliseResult:
    revision: CandidateRevision | None
    issues: tuple[DataQualityIssue, ...] = ()
    transformations: tuple[TransformationEvent, ...] = ()


# The registry deliberately does not generalize a short row into a guessed
# missing-column position. Only observed, approved trailing shapes are accepted.
SALVAGE_REGISTRY = MappingProxyType(
    {
        (EntityType.ORDER, 7): TransformationCode.TRAILING_FIELDS_ABSENT,
        (EntityType.CUSTOMER, 12): TransformationCode.NOTES_REJOINED,
        (EntityType.PRODUCT, 11): TransformationCode.TRAILING_EMPTY_FIELDS,
        (EntityType.ORDER, 11): TransformationCode.TRAILING_EMPTY_FIELDS,
    }
)
_DISCRIMINATORS = {entity.name: entity for entity in EntityType}


class _Assembly:
    def __init__(self, raw: RawRecord, entity: EntityType | None) -> None:
        self.raw = raw
        self.entity = entity
        self.revision_id = candidate_revision_id(raw.id, 1)
        self.issues: list[DataQualityIssue] = []
        self.transformations: list[TransformationEvent] = []
        self.values = raw.fields
        self.structural: TransformationEvent | None = None
        self.notes_issue: DataQualityIssue | None = None

    def field(self, index: int, name: str) -> FieldPath:
        if index >= self.raw.field_count:
            refs = (SourceRef(self.raw.id),)
        elif index == 9 and self.raw.field_count == 12:
            refs = tuple(SourceRef(self.raw.id, i) for i in range(9, 12))
        else:
            refs = (SourceRef(self.raw.id, index),)
        return FieldPath(f"{self.entity}.{name}", refs)

    def collect[T](self, normalized: NormalisedField[T]) -> CandidateField[T]:
        self.issues.extend(i.for_revision(self.revision_id) for i in normalized.issues)
        for event in normalized.transformations:
            self.transformations.append(
                event.for_revision(
                    self.revision_id, sequence=len(self.transformations) + 1
                )
            )
        return normalized.as_candidate_field()

    def text(self, index: int, name: str) -> CandidateField[str]:
        return self.collect(normalise_text(self.values[index], self.field(index, name)))

    def matching(self, name: str) -> CandidateField[str]:
        evidence = FieldEvidence(self.values[2], self.field(2, name))
        if not evidence.text:
            return self.collect(evidence.finish(FieldState.ABSENT, None))
        key = match_key(evidence.text)
        evidence.transform(TransformationCode.MATCH_KEY, evidence.text, key)
        return self.collect(evidence.finish(FieldState.KNOWN, key))

    def reject(self, code: IssueCode) -> RejectedCandidateShell:
        refs = tuple(
            SourceRef(self.raw.id, i) for i in range(self.raw.field_count)
        ) or (SourceRef(self.raw.id),)
        evidence = FieldEvidence(repr(self.raw.fields), FieldPath("record", refs))
        evidence.issue(
            code,
            "Unknown discriminator"
            if code is IssueCode.INVALID_DISCRIMINATOR
            else "Unregistered structural shape; no realignment attempted",
        )
        unresolved: NormalisedField[str] = evidence.finish(FieldState.UNRESOLVED, None)
        self.collect(unresolved)
        return RejectedCandidateShell(
            self.raw.id, refs, tuple(i.id for i in self.issues)
        )

    def salvage(self) -> bool:
        if self.raw.field_count == 10:
            return True
        assert self.entity is not None
        operation = SALVAGE_REGISTRY.get((self.entity, self.raw.field_count))
        if operation is None:
            return False
        if operation is TransformationCode.TRAILING_EMPTY_FIELDS:
            if any(self.raw.fields[10:]):
                return False
            self.values = self.raw.fields[:10]
        elif operation is TransformationCode.TRAILING_FIELDS_ABSENT:
            self.values = (*self.raw.fields, "", "", "")
        else:
            self.values = (*self.raw.fields[:9], ",".join(self.raw.fields[9:]))
        notes_rejoined = operation is TransformationCode.NOTES_REJOINED
        refs = tuple(
            SourceRef(self.raw.id, i)
            for i in range(9 if notes_rejoined else 0, self.raw.field_count)
        )
        path = "customer.notes" if notes_rejoined else "record"
        evidence = FieldEvidence(repr(self.raw.fields), FieldPath(path, refs))
        evidence.transform(
            operation,
            self.raw.fields[9:] if notes_rejoined else self.raw.fields,
            self.values[9] if notes_rejoined else self.values,
        )
        if notes_rejoined:
            evidence.issue(
                IssueCode.NOTES_REJOINED,
                "Lossy structural repair: rejoined unquoted final-column note fields",
            )
        unresolved: NormalisedField[str] = evidence.finish(FieldState.UNRESOLVED, None)
        self.collect(unresolved)
        self.structural = self.transformations[-1]
        self.notes_issue = self.issues[-1] if notes_rejoined else None
        return True

    def payload(self) -> CandidatePayload:
        if self.entity is None:
            return self.reject(IssueCode.INVALID_DISCRIMINATOR)
        if not self.salvage():
            return self.reject(IssueCode.INVALID_STRUCTURE)
        entity = self.entity
        identity = self.text(
            1,
            {
                EntityType.CUSTOMER: "customer_id",
                EntityType.PRODUCT: "sku",
                EntityType.ORDER: "order_id",
            }[entity],
        )
        name = self.text(
            2, "customer_name_raw" if entity is EntityType.ORDER else "name"
        )
        contact = self.text(
            3,
            {
                EntityType.CUSTOMER: "email",
                EntityType.PRODUCT: "category",
                EntityType.ORDER: "sku",
            }[entity],
        )
        money = normalise_money(
            self.values[4],
            self.field(
                4, "lifetime_spend" if entity is EntityType.CUSTOMER else "unit_price"
            ),
        )
        amount = self.collect(money.field)
        quantity = (
            self.collect(
                normalise_integer(
                    self.values[5],
                    self.field(
                        5, "stock_qty" if entity is EntityType.PRODUCT else "quantity"
                    ),
                )
            )
            if entity is not EntityType.CUSTOMER
            else None
        )
        date = self.collect(
            normalise_date(
                self.values[6],
                self.field(
                    6,
                    {
                        EntityType.CUSTOMER: "signup_date",
                        EntityType.PRODUCT: "listed_date",
                        EntityType.ORDER: "ordered_at",
                    }[entity],
                ),
            )
        )
        status = self.collect(normalise_status(self.values[7], self.field(7, "status")))
        if status.state is FieldState.ABSENT:
            issue = PendingIssue(
                deterministic_id(self.revision_id, "missing-status"),
                IssueCode.MISSING_REQUIRED_VALUE,
                self.field(7, "status"),
                f"Required status is absent; raw value: {self.values[7]!r}",
            ).for_revision(self.revision_id)
            self.issues.append(issue)
            status = replace(status, issue_refs=(*status.issue_refs, issue.id))
        tags = (
            self.collect(normalise_tags(self.values[8], self.field(8, "tags")))
            if self.raw.field_count > 8
            else CandidateField[tuple[str, ...]](
                FieldState.ABSENT, None, (SourceRef(self.raw.id),)
            )
        )
        notes = self.text(9, "notes")
        if self.structural is not None:
            if self.structural.operation is TransformationCode.NOTES_REJOINED:
                assert self.notes_issue is not None
                notes = replace(
                    notes,
                    transformation_refs=(
                        self.structural.id,
                        *notes.transformation_refs,
                    ),
                    issue_refs=(self.notes_issue.id, *notes.issue_refs),
                )
            elif self.structural.operation is TransformationCode.TRAILING_FIELDS_ABSENT:
                status = replace(
                    status,
                    transformation_refs=(
                        self.structural.id,
                        *status.transformation_refs,
                    ),
                )
                tags = replace(
                    tags,
                    transformation_refs=(self.structural.id, *tags.transformation_refs),
                )
                notes = replace(
                    notes,
                    transformation_refs=(
                        self.structural.id,
                        *notes.transformation_refs,
                    ),
                )
        if entity is EntityType.CUSTOMER:
            return CustomerCandidate(
                identity,
                name,
                contact,
                amount,
                date,
                status,
                tags,
                notes,
                name_match_key=self.matching("name_match_key"),
                lifetime_spend_annotation=money.annotation,
            )
        assert quantity is not None
        if entity is EntityType.PRODUCT:
            return ProductCandidate(
                identity,
                name,
                contact,
                amount,
                quantity,
                date,
                status,
                tags,
                notes,
                unit_price_annotation=money.annotation,
            )
        return OrderCandidate(
            identity,
            name,
            contact,
            amount,
            quantity,
            date,
            status,
            tags,
            notes,
            customer_match_key=self.matching("customer_match_key"),
            unit_price_annotation=money.annotation,
        )


def build_initial_candidate(
    raw: RawRecord, context: NormaliseContext
) -> NormaliseResult:
    """Preserve raw evidence; non-data mechanics have no candidate or issue."""
    if raw.kind is not RawRecordKind.DATA:
        return NormaliseResult(None)
    if raw.field_count != len(raw.fields):
        raise ValueError("Raw field count does not match preserved fields")
    assembly = _Assembly(
        raw, _DISCRIMINATORS.get(raw.fields[0]) if raw.fields else None
    )
    payload = assembly.payload()
    revision = CandidateRevision(
        assembly.revision_id, raw.id, 1, None, "normalise", payload, context.clock.now()
    )
    return NormaliseResult(
        revision, tuple(assembly.issues), tuple(assembly.transformations)
    )
