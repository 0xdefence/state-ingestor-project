"""Canonical repository: verified append-only writes and exact lineage reads."""

from dataclasses import replace
from datetime import UTC
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.domain.canonical import (
    CanonicalBusinessKey,
    CanonicalIdentity,
    CanonicalRevision,
)
from services.domain.issues import DependencyState
from services.domain.observations import PriorObservation, Reobservation
from services.infrastructure.db.derived_codec import evidence_equal
from services.infrastructure.db.derived_repositories import (
    SqlAlchemyCandidateRepository,
)
from services.infrastructure.db.models import (
    CandidateRevisionModel,
    CanonicalBusinessKeyModel,
    CanonicalIdentityModel,
    CanonicalRevisionModel,
    DependencyRecordModel,
    RawRecordModel,
    ReobservationLinkModel,
)
from services.pipeline.rules.duplicates import business_key, same_typed_values


def _revision(row: CanonicalRevisionModel) -> CanonicalRevision:
    return CanonicalRevision(
        row.id,
        row.identity_id,
        row.candidate_revision_id,
        row.revision_number,
        row.staged_at.astimezone(UTC),
    )


def _link(row: ReobservationLinkModel) -> Reobservation:
    return Reobservation(
        row.id, row.candidate_revision_id, row.identity_id, row.canonical_revision_id
    )


class SqlAlchemyCanonicalRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_identity(self, identity: CanonicalIdentity) -> None:
        identity = replace(identity, created_at=identity.created_at.astimezone(UTC))
        row = self._session.get(CanonicalIdentityModel, identity.id)
        if row is not None:
            stored = CanonicalIdentity(
                row.id, row.entity_type, row.created_at.astimezone(UTC)
            )
            if not evidence_equal(stored, identity):
                raise ValueError("Canonical identity mismatch")
            return
        self._session.add(
            CanonicalIdentityModel(
                id=identity.id,
                entity_type=identity.entity_type,
                created_at=identity.created_at,
            )
        )
        self._session.flush()

    def add_revision(self, revision: CanonicalRevision) -> None:
        revision = replace(revision, staged_at=revision.staged_at.astimezone(UTC))
        row = self._session.get(CanonicalRevisionModel, revision.id)
        if row is not None:
            if not evidence_equal(_revision(row), revision):
                raise ValueError("Canonical revision identity mismatch")
            return
        identity = self._session.scalar(
            select(CanonicalIdentityModel)
            .where(CanonicalIdentityModel.id == revision.identity_id)
            .with_for_update()
        )
        candidate = SqlAlchemyCandidateRepository(self._session).get(
            revision.candidate_revision_id
        )
        if identity is None or identity.entity_type != candidate.entity_type:
            raise ValueError("Canonical identity and candidate entity mismatch")
        terminal = self._session.scalar(
            select(func.max(CandidateRevisionModel.revision_number)).where(
                CandidateRevisionModel.raw_record_id == candidate.raw_record_id
            )
        )
        if terminal != candidate.revision_number:
            raise ValueError("Canonical revision requires terminal candidate")
        last = self._session.scalar(
            select(func.max(CanonicalRevisionModel.revision_number)).where(
                CanonicalRevisionModel.identity_id == revision.identity_id
            )
        )
        if revision.revision_number != (last or 0) + 1:
            raise ValueError("Canonical revision history must be consecutive")
        self._session.add(
            CanonicalRevisionModel(
                id=revision.id,
                identity_id=revision.identity_id,
                candidate_revision_id=revision.candidate_revision_id,
                revision_number=revision.revision_number,
                staged_at=revision.staged_at,
            )
        )
        self._session.flush()

    def add_key(self, key: CanonicalBusinessKey) -> None:
        row = self._session.get(CanonicalBusinessKeyModel, (key.key_type, key.value))
        if row is not None:
            stored = CanonicalBusinessKey(
                row.identity_id, row.key_type, row.value, row.effective_revision
            )
            if not evidence_equal(stored, key):
                raise ValueError("Canonical business key identity mismatch")
            return
        revision = self._session.get(CanonicalRevisionModel, key.effective_revision)
        if revision is None or revision.identity_id != key.identity_id:
            raise ValueError("Business key must name its identity's revision")
        candidate = SqlAlchemyCandidateRepository(self._session).get(
            revision.candidate_revision_id
        )
        if business_key(candidate.payload) != (key.key_type, key.value):
            raise ValueError("Business key must match exact accepted candidate")
        self._session.add(
            CanonicalBusinessKeyModel(
                identity_id=key.identity_id,
                key_type=key.key_type,
                value=key.value,
                effective_revision=key.effective_revision,
            )
        )
        self._session.flush()

    def get_key(self, key_type: str, value: str) -> CanonicalBusinessKey | None:
        row = self._session.get(CanonicalBusinessKeyModel, (key_type, value))
        if row is None:
            return None
        return CanonicalBusinessKey(
            row.identity_id, row.key_type, row.value, row.effective_revision
        )

    def for_candidate(self, candidate_id: UUID) -> CanonicalRevision | None:
        row = self._session.scalar(
            select(CanonicalRevisionModel).where(
                CanonicalRevisionModel.candidate_revision_id == candidate_id
            )
        )
        return _revision(row) if row is not None else None

    def prior_observations(self) -> tuple[PriorObservation, ...]:
        latest = (
            select(
                CanonicalRevisionModel.identity_id,
                func.max(CanonicalRevisionModel.revision_number).label("number"),
            )
            .group_by(CanonicalRevisionModel.identity_id)
            .subquery()
        )
        rows = self._session.scalars(
            select(CanonicalRevisionModel)
            .join(
                latest,
                (CanonicalRevisionModel.identity_id == latest.c.identity_id)
                & (CanonicalRevisionModel.revision_number == latest.c.number),
            )
            .order_by(CanonicalRevisionModel.identity_id)
        )
        result: list[PriorObservation] = []
        candidates = SqlAlchemyCandidateRepository(self._session)
        for row in rows:
            candidate = candidates.get(row.candidate_revision_id)
            raw = self._session.get(RawRecordModel, candidate.raw_record_id)
            if raw is None:
                raise ValueError("Canonical candidate has no raw lineage")
            result.append(
                PriorObservation(row.identity_id, row.id, raw.run_id, candidate)
            )
        return tuple(result)

    def add_reobservation(self, link: Reobservation) -> None:
        row = self._session.get(ReobservationLinkModel, link.id)
        if row is not None:
            if not evidence_equal(_link(row), link):
                raise ValueError("Reobservation identity mismatch")
            return
        canonical = self._session.get(
            CanonicalRevisionModel, link.canonical_revision_id
        )
        if canonical is None or canonical.identity_id != link.identity_id:
            raise ValueError("Reobservation canonical lineage mismatch")
        candidates = SqlAlchemyCandidateRepository(self._session)
        prior = candidates.get(canonical.candidate_revision_id)
        current = candidates.get(link.candidate_revision_id)
        if business_key(prior.payload) != business_key(
            current.payload
        ) or not same_typed_values(prior.payload, current.payload):
            raise ValueError("Reobservation value mismatch")
        self._session.add(
            ReobservationLinkModel(
                id=link.id,
                candidate_revision_id=link.candidate_revision_id,
                identity_id=link.identity_id,
                canonical_revision_id=link.canonical_revision_id,
            )
        )
        self._session.flush()

    def reobservations(self, run_id: UUID) -> tuple[Reobservation, ...]:
        return tuple(
            _link(row)
            for row in self._session.scalars(
                select(ReobservationLinkModel)
                .join(
                    CandidateRevisionModel,
                    CandidateRevisionModel.id
                    == ReobservationLinkModel.candidate_revision_id,
                )
                .join(
                    RawRecordModel,
                    RawRecordModel.id == CandidateRevisionModel.raw_record_id,
                )
                .where(RawRecordModel.run_id == run_id)
                .order_by(RawRecordModel.source_line_start)
            )
        )

    def link_dependency(self, dependency_id: UUID, identity_id: UUID) -> None:
        """Only null→verified canonical linkage may enrich a dependency fact."""
        row = self._session.scalar(
            select(DependencyRecordModel)
            .where(DependencyRecordModel.id == dependency_id)
            .with_for_update()
        )
        if (
            row is None
            or row.state != DependencyState.RESOLVED
            or row.target_candidate_revision_id is None
        ):
            raise ValueError("Dependency requires a resolved target candidate")
        target = self.for_candidate(row.target_candidate_revision_id)
        observation = self._session.scalar(
            select(ReobservationLinkModel).where(
                ReobservationLinkModel.candidate_revision_id
                == row.target_candidate_revision_id
            )
        )
        target_identity = (
            target.identity_id
            if target
            else observation.identity_id
            if observation
            else None
        )
        if target_identity != identity_id or row.resolved_entity_id not in (
            None,
            identity_id,
        ):
            raise ValueError("Dependency canonical linkage mismatch")
        row.resolved_entity_id = identity_id
        self._session.flush()
