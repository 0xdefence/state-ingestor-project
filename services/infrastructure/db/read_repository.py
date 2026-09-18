"""Read-only PostgreSQL snapshots, with a deduplicated exact-lineage graph."""

from collections import Counter
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from services.application.dependency_readiness import dependencies_ready
from services.application.errors import ResourceNotFoundError
from services.application.evidence_domains import expected_domain
from services.application.queries import (
    AllFilesScope,
    CurrentFileScope,
    FileScope,
    ReviewDetailQuery,
    ReviewQueueQuery,
    RunDetailQuery,
    WorkspaceQuery,
)
from services.application.views import (
    DecisionView,
    EvidenceNode,
    EvidenceView,
    ObjectView,
    ReviewDetailView,
    ReviewQueueView,
    ReviewRowView,
    RunDetailView,
    RunView,
    Value,
    WorkspaceView,
    code_label,
)
from services.domain.candidates import (
    CustomerCandidate,
    OrderCandidate,
    ProductCandidate,
)
from services.domain.decisions import DecisionOutcome, legal_outcomes
from services.domain.fields import FieldState
from services.domain.issues import Verdict
from services.infrastructure.db import models as m
from services.infrastructure.db.canonical_repository import (
    SqlAlchemyCanonicalRepository,
)
from services.infrastructure.db.decision_repository import SqlAlchemyDecisionRepository
from services.infrastructure.db.derived_codec import decode
from services.infrastructure.db.derived_repositories import (
    SqlAlchemyCandidateRepository,
    SqlAlchemyClassificationRepository,
)


def freeze(value: object) -> Value:
    if value is None or isinstance(
        value, (str, int, bool, UUID, datetime, date, Decimal)
    ):
        return value
    if isinstance(value, Mapping):
        return ObjectView(
            tuple(
                (str(k), freeze(v))
                for k, v in cast(Mapping[object, object], value).items()
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in cast(list[object], value))
    if is_dataclass(value) and not isinstance(value, type):
        return ObjectView(
            tuple((f.name, freeze(getattr(value, f.name))) for f in fields(value))
        )
    raise TypeError("Unsupported projection value")


def _object(values: Mapping[str, object]) -> ObjectView:
    return cast(ObjectView, freeze(values))


def _attributes(row: m.Base) -> ObjectView:
    data: dict[str, object] = {}
    for column in row.__table__.columns:
        value: object = getattr(row, column.name)
        if column.name in {"payload", "before", "after", "source_refs", "reasons"}:
            value = decode(value)
        # Storage paths and pipeline exception text are not product-facing evidence.
        if column.name == "locator":
            continue
        if column.name == "facts" and isinstance(value, dict):
            value = {
                k: v
                for k, v in cast(dict[str, object], value).items()
                if k != "message"
            }
        data[column.name] = value
        if column.name in {
            "state",
            "verdict",
            "readiness",
            "code",
            "operation",
            "outcome",
            "kind",
            "entity_type",
            "origin",
            "stage",
            "event_type",
            "severity",
            "action",
        } and isinstance(value, str):
            data[column.name + "_label"] = code_label(value)
    return _object(data)


class SqlAlchemyReadRepository:
    """Each public method opens and closes exactly one repeatable-read snapshot."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def _snapshot(self) -> Generator[Session]:
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                with Session(bind=conn) as session:
                    yield session

    def _runs(self, session: Session, scope: FileScope) -> tuple[m.RunModel, ...]:
        statement = select(m.RunModel).order_by(m.RunModel.created_at, m.RunModel.id)
        if not isinstance(scope, AllFilesScope):
            ids = (
                (scope.run_id,)
                if isinstance(scope, CurrentFileScope)
                else scope.run_ids
            )
            statement = statement.where(m.RunModel.id.in_(ids))
            rows = tuple(session.scalars(statement))
            if {row.id for row in rows} != set(ids):
                raise ResourceNotFoundError("Requested run was not found")
            return rows
        return tuple(session.scalars(statement))

    def _run(self, session: Session, row: m.RunModel) -> RunView:
        filename = session.scalar(
            select(m.SourceOccurrenceModel.filename)
            .where(m.SourceOccurrenceModel.source_file_id == row.source_file_id)
            .order_by(m.SourceOccurrenceModel.ingested_at, m.SourceOccurrenceModel.id)
            .limit(1)
        )
        processed_at = (
            session.scalar(
                select(m.PipelineEventModel.occurred_at)
                .where(
                    m.PipelineEventModel.run_id == row.id,
                    m.PipelineEventModel.stage == "load",
                    m.PipelineEventModel.event_type == "stage_completed",
                )
                .order_by(m.PipelineEventModel.occurred_at.desc())
                .limit(1)
            )
            if row.state == "staged"
            else None
        )
        return RunView(
            row.id,
            row.source_file_id,
            row.state,
            code_label(row.state),
            row.stage_failure,
            row.created_at,
            processed_at,
            _object(row.counts or {}),
            filename,
            row.predecessor_run_id,
            row.reprocess_sequence,
            session.get_one(m.SourceFileModel, row.source_file_id).sha256,
            session.get_one(m.SourceFileModel, row.source_file_id).byte_size,
            row.fx_snapshot_id,
            row.requested_fx_snapshot_date,
            row.rules_version,
            row.build_revision,
        )

    def _queue(self, session: Session, query: ReviewQueueQuery) -> ReviewQueueView:
        runs = self._runs(session, query.scope)
        statement = (
            select(
                m.ReviewItemModel,
                m.ClassificationResultModel,
                m.CandidateRevisionModel,
                m.RawRecordModel,
            )
            .join(
                m.ClassificationResultModel,
                m.ReviewItemModel.classification_id == m.ClassificationResultModel.id,
            )
            .join(
                m.CandidateRevisionModel,
                m.ClassificationResultModel.candidate_revision_id
                == m.CandidateRevisionModel.id,
            )
            .join(
                m.RawRecordModel, m.ReviewItemModel.raw_record_id == m.RawRecordModel.id
            )
            .where(m.ReviewItemModel.run_id.in_([r.id for r in runs]))
            .order_by(
                m.ReviewItemModel.created_at,
                m.RawRecordModel.source_line_start,
                m.ReviewItemModel.id,
            )
        )
        items: list[ReviewRowView] = []
        for review, classification, candidate, raw in session.execute(statement):
            item = self._review_row(session, review, classification, candidate, raw)
            if (
                query.effective_state is not None
                and item.effective_state != query.effective_state
            ):
                continue
            if query.effective_state == "pending" and item.current_status == "promoted":
                continue
            if query.verdict is not None and item.verdict != query.verdict:
                continue
            items.append(item)
        return ReviewQueueView(query.scope, tuple(items))

    def _review_row(
        self,
        session: Session,
        review: m.ReviewItemModel,
        classification: m.ClassificationResultModel,
        candidate: m.CandidateRevisionModel,
        raw: m.RawRecordModel,
    ) -> ReviewRowView:
        history = SqlAlchemyDecisionRepository(session).for_review(review.id)
        latest = history[-1] if history else None
        state = latest.effective_state if latest else "pending"
        reasons = cast(tuple[object, ...], decode(review.reasons))
        canonicals = SqlAlchemyCanonicalRepository(session)
        classifications = SqlAlchemyClassificationRepository(session)
        ready = dependencies_ready(
            classifications.get(classification.id),
            classifications,
            SqlAlchemyCandidateRepository(session),
            canonicals,
        )
        revision = canonicals.for_candidate(candidate.id)
        canonical_effect = (
            "current"
            if revision and canonicals.current(revision.identity_id) == revision
            else "historical"
            if revision
            else "not_promoted"
        )
        current_status = (
            "promoted"
            if canonical_effect == "current" and latest is None
            else "blocked_by_dependency"
            if not ready and state == "pending"
            else state
        )
        return ReviewRowView(
            review.id,
            review.run_id,
            raw.id,
            classification.id,
            candidate.id,
            candidate.entity_type,
            _business_identifier(candidate),
            classification.verdict,
            code_label(classification.verdict),
            classification.readiness,
            code_label(classification.readiness),
            state,
            code_label(state),
            latest.sequence if latest else 0,
            latest.id if latest else None,
            raw.source_line_start,
            raw.source_line_end,
            tuple(str(getattr(r, "summary")) for r in reasons),
            "ready" if ready else "blocked_by_dependency",
            canonical_effect,
            revision.id if revision else None,
            current_status,
            code_label(current_status),
        )

    def workspace(self, query: WorkspaceQuery) -> WorkspaceView:
        with self._snapshot() as session:
            runs = tuple(
                self._run(session, r) for r in self._runs(session, query.scope)
            )
            queue = self._queue(session, ReviewQueueQuery(query.scope))
            counts = Counter(
                "promoted"
                if item.current_status == "promoted"
                else item.effective_state
                for item in queue.items
            )
            return WorkspaceView(
                query.scope,
                runs,
                _object(
                    {
                        state: counts[state]
                        for state in (
                            "pending",
                            "approved",
                            "rejected",
                            "acknowledged",
                            "promoted",
                        )
                    }
                ),
                queue.items,
            )

    def _occurrence(self, session: Session, row: m.SourceOccurrenceModel) -> ObjectView:
        links = tuple(
            _attributes(link)
            for link in session.scalars(
                select(m.RunSourceOccurrenceModel)
                .where(m.RunSourceOccurrenceModel.source_occurrence_id == row.id)
                .order_by(
                    m.RunSourceOccurrenceModel.linked_at,
                    m.RunSourceOccurrenceModel.run_id,
                )
            )
        )
        return ObjectView((*_attributes(row).fields, ("run_links", links)))

    def run_detail(self, query: RunDetailQuery) -> RunDetailView:
        with self._snapshot() as session:
            run = self._runs(session, CurrentFileScope(query.run_id))[0]
            # Every occurrence of these exact bytes, even if a later reprocess run
            # was explicitly requested from only one occurrence.
            occurrences = tuple(
                self._occurrence(session, r)
                for r in session.scalars(
                    select(m.SourceOccurrenceModel)
                    .where(m.SourceOccurrenceModel.source_file_id == run.source_file_id)
                    .order_by(
                        m.SourceOccurrenceModel.ingested_at, m.SourceOccurrenceModel.id
                    )
                )
            )
            checkpoints = tuple(
                _attributes(r)
                for r in session.scalars(
                    select(m.PipelineCheckpointModel)
                    .where(m.PipelineCheckpointModel.run_id == run.id)
                    .order_by(m.PipelineCheckpointModel.stage)
                )
            )
            events = tuple(
                _attributes(r)
                for r in session.scalars(
                    select(m.PipelineEventModel)
                    .where(m.PipelineEventModel.run_id == run.id)
                    .order_by(m.PipelineEventModel.occurred_at, m.PipelineEventModel.id)
                )
            )
            raw_records = tuple(
                session.scalars(
                    select(m.RawRecordModel)
                    .where(m.RawRecordModel.run_id == run.id)
                    .order_by(m.RawRecordModel.source_line_start, m.RawRecordModel.id)
                )
            )
            evidence = self._evidence(session, *raw_records)
            records: list[ObjectView] = []
            for raw in raw_records:
                candidate = session.scalar(
                    select(m.CandidateRevisionModel)
                    .where(m.CandidateRevisionModel.raw_record_id == raw.id)
                    .order_by(m.CandidateRevisionModel.revision_number.desc())
                    .limit(1)
                )
                classification = (
                    session.scalar(
                        select(m.ClassificationResultModel)
                        .where(
                            m.ClassificationResultModel.candidate_revision_id
                            == candidate.id
                        )
                        .order_by(m.ClassificationResultModel.evaluated_at.desc())
                        .limit(1)
                    )
                    if candidate
                    else None
                )
                review = session.scalar(
                    select(m.ReviewItemModel).where(
                        m.ReviewItemModel.raw_record_id == raw.id
                    )
                )
                records.append(
                    _object(
                        {
                            "id": raw.id,
                            "kind": raw.kind,
                            "source_line_start": raw.source_line_start,
                            "source_line_end": raw.source_line_end,
                            "candidate_revision_id": candidate.id
                            if candidate
                            else None,
                            "business_identifier": _business_identifier(candidate)
                            if candidate
                            else None,
                            "classification_id": classification.id
                            if classification
                            else None,
                            "verdict": classification.verdict
                            if classification
                            else None,
                            "review_item_id": review.id if review else None,
                        }
                    )
                )
            return RunDetailView(
                self._run(session, run),
                occurrences,
                checkpoints,
                events,
                tuple(records),
                evidence,
            )

    def review_queue(self, query: ReviewQueueQuery) -> ReviewQueueView:
        with self._snapshot() as session:
            return self._queue(session, query)

    def review_detail(self, query: ReviewDetailQuery) -> ReviewDetailView:
        with self._snapshot() as session:
            review = session.get(m.ReviewItemModel, query.review_item_id)
            if review is None:
                raise ResourceNotFoundError("Requested review item was not found")
            classification = session.get_one(
                m.ClassificationResultModel, review.classification_id
            )
            candidate = session.get_one(
                m.CandidateRevisionModel, classification.candidate_revision_id
            )
            raw = session.get_one(m.RawRecordModel, review.raw_record_id)
            item = self._review_row(session, review, classification, candidate, raw)
            history = SqlAlchemyDecisionRepository(session).for_review(review.id)
            allowed = legal_outcomes(Verdict(classification.verdict))
            if item.current_readiness == "blocked_by_dependency":
                allowed = tuple(o for o in allowed if o is not DecisionOutcome.APPROVE)
            if history:
                allowed = tuple(
                    outcome for outcome in allowed if outcome != history[-1].outcome
                )
            return ReviewDetailView(
                item,
                self._evidence(session, review),
                tuple(DecisionView(d, code_label(d.outcome)) for d in history),
                allowed,
            )

    def _evidence(self, session: Session, *roots: m.Base) -> EvidenceView:
        # Follow explicit references and owning records, never copy candidate
        # payloads into queue rows or comparison summaries.
        models: tuple[type[m.Base], ...] = (
            m.SourceFileModel,
            m.SourceOccurrenceModel,
            m.RunModel,
            m.RawRecordModel,
            m.CandidateRevisionModel,
            m.ClassificationResultModel,
            m.DataQualityIssueModel,
            m.TransformationEventModel,
            m.DependencyRecordModel,
            m.DuplicateRelationModel,
            m.ReviewItemModel,
            m.CanonicalIdentityModel,
            m.CanonicalRevisionModel,
            m.CanonicalPromotionEventModel,
            m.ReviewDecisionModel,
            m.FxSnapshotModel,
            m.ReobservationLinkModel,
        )
        nodes: dict[tuple[str, UUID], EvidenceNode] = {}
        pending: list[m.Base] = list(roots)
        searched: set[UUID] = set()
        while pending:
            row = pending.pop()
            identity = cast(UUID, getattr(row, "id"))
            key = (row.__tablename__, identity)
            if key in nodes:
                continue
            attrs = (
                self._occurrence(session, row)
                if isinstance(row, m.SourceOccurrenceModel)
                else _attributes(row)
            )
            if isinstance(row, m.DataQualityIssueModel):
                attrs = ObjectView(
                    (
                        *attrs.fields,
                        ("expected_domain", expected_domain(row.code, row.field_path)),
                    )
                )
            if isinstance(row, m.CanonicalIdentityModel):
                current = session.get(m.CanonicalCurrentModel, identity)
                attrs = ObjectView(
                    (
                        *attrs.fields,
                        (
                            "current_revision_id",
                            current.canonical_revision_id if current else None,
                        ),
                    )
                )
            nodes[key] = EvidenceNode(key[0], identity, attrs)
            for ref in _references(attrs):
                if ref in searched:
                    continue
                searched.add(ref)
                for model in models:
                    target = session.get(model, ref)
                    if target is not None:
                        pending.append(target)
                        break
            if isinstance(row, m.RawRecordModel):
                pending.extend(
                    session.scalars(
                        select(m.CandidateRevisionModel).where(
                            m.CandidateRevisionModel.raw_record_id == identity
                        )
                    )
                )
                pending.extend(
                    session.scalars(
                        select(m.DuplicateRelationModel).where(
                            m.DuplicateRelationModel.later_raw_id == identity
                        )
                    )
                )
            if isinstance(row, m.CandidateRevisionModel):
                for model in (
                    m.DataQualityIssueModel,
                    m.TransformationEventModel,
                    m.ClassificationResultModel,
                    m.CanonicalRevisionModel,
                    m.ReobservationLinkModel,
                ):
                    pending.extend(
                        session.scalars(
                            select(model).where(model.candidate_revision_id == identity)
                        )
                    )
            if isinstance(row, m.ClassificationResultModel):
                pending.extend(
                    session.scalars(
                        select(m.DependencyRecordModel).where(
                            m.DependencyRecordModel.classification_id == identity
                        )
                    )
                )
            if isinstance(row, m.RawRecordModel):
                pending.extend(
                    session.scalars(
                        select(m.ReviewItemModel).where(
                            m.ReviewItemModel.raw_record_id == identity
                        )
                    )
                )
            if isinstance(row, m.ReviewItemModel):
                pending.extend(
                    session.scalars(
                        select(m.ReviewDecisionModel).where(
                            m.ReviewDecisionModel.review_item_id == identity
                        )
                    )
                )
            if isinstance(row, m.SourceFileModel):
                pending.extend(
                    session.scalars(
                        select(m.SourceOccurrenceModel).where(
                            m.SourceOccurrenceModel.source_file_id == identity
                        )
                    )
                )
            if isinstance(row, m.CanonicalRevisionModel):
                pending.extend(
                    session.scalars(
                        select(m.CanonicalPromotionEventModel).where(
                            m.CanonicalPromotionEventModel.canonical_revision_id
                            == identity
                        )
                    )
                )
        return EvidenceView(
            tuple(nodes[k] for k in sorted(nodes, key=lambda k: (k[0], str(k[1]))))
        )


def _business_identifier(candidate: m.CandidateRevisionModel) -> str | None:
    payload = decode(candidate.payload)
    if isinstance(payload, CustomerCandidate):
        field = payload.customer_id
    elif isinstance(payload, ProductCandidate):
        field = payload.sku
    elif isinstance(payload, OrderCandidate):
        field = payload.order_id
    else:
        return None
    return field.value if field.state is FieldState.KNOWN else None


def _references(value: Value) -> set[UUID]:
    if isinstance(value, UUID):
        return {value}
    if isinstance(value, ObjectView):
        return set[UUID]().union(*(_references(v) for _, v in value.fields))
    if isinstance(value, tuple):
        return set[UUID]().union(*(_references(v) for v in value))
    return set()
