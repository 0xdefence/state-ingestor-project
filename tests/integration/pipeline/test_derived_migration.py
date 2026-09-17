"""Derived schema, exact evidence round trips, and transactional repository writes."""

from dataclasses import replace
from uuid import UUID

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from tests.integration.foundation.test_migrations import (
    RUN,
    migration_config,
)
from tests.integration.foundation.test_migrations import (
    migrated_engine as migrated_engine,
)
from tests.integration.foundation.test_migrations import (
    postgres_url as postgres_url,
)

TABLES = {
    "candidate_revision",
    "transformation_event",
    "data_quality_issue",
    "classification_result",
    "dependency_record",
    "duplicate_relation",
    "review_item",
}


def test_derived_migration_upgrade_downgrade_and_model_parity(
    postgres_url: str,
) -> None:
    from services.infrastructure.db.models import Base

    config = migration_config(postgres_url)
    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    try:
        assert TABLES <= set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            assert (
                compare_metadata(MigrationContext.configure(connection), Base.metadata)
                == []
            )
        command.downgrade(config, "0001_source_and_runs")
        assert not TABLES.intersection(inspect(engine).get_table_names())
        command.upgrade(config, "head")
        assert TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_derived_evidence_round_trip_and_rollback(migrated_engine: Engine) -> None:
    from services.domain.candidates import candidate_revision_id
    from services.domain.fields import CandidateField, FieldState
    from services.domain.ids import deterministic_id
    from services.domain.issues import (
        ClassificationResult,
        ComparisonScope,
        DataQualityIssue,
        DependencyKind,
        DependencyRecord,
        DependencyState,
        DuplicateRelation,
        IssueCode,
        Readiness,
        ReviewItem,
        ReviewReason,
        Severity,
        TransformationCode,
        TransformationEvent,
        Verdict,
    )
    from services.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from services.infrastructure.runtime import _repositories
    from tests.unit.normalise.test_candidates import FIXED_TIME, RAW_ID, REFS, revision

    initial = revision()
    second_raw = UUID(int=43)
    with migrated_engine.begin() as connection:
        for raw in (RAW_ID, second_raw):
            connection.execute(
                text(
                    "INSERT INTO raw_record (id,run_id,source_line_start,"
                    "source_line_end,kind,fields,field_count,parse_metadata) "
                    "VALUES (:id,:run,1,1,'data','[]',0,'{}')"
                ),
                {"id": raw, "run": RUN},
            )
    transformation = TransformationEvent(
        deterministic_id(initial.id, "transform", 1),
        initial.id,
        TransformationCode.TRIM,
        "product.name",
        CandidateField.known(" Café ", source_refs=REFS),
        initial.payload.name,
        1,
    )
    issue = DataQualityIssue(
        deterministic_id(initial.id, "issue", "stock"),
        initial.id,
        IssueCode.INVALID_INTEGER,
        Severity.ERROR,
        "product.stock_qty",
        "The source value cannot be parsed as an integer.",
        REFS,
        "The value may be a word.",
    )
    classification = ClassificationResult(
        deterministic_id(initial.id, "classification", "rules"),
        initial.id,
        "rules",
        None,
        Verdict.NEEDS_REVIEW,
        Readiness.INELIGIBLE,
        FIXED_TIME,
    )
    dependency = DependencyRecord(
        deterministic_id(classification.id, "dependency"),
        classification.id,
        DependencyKind.PRODUCT,
        "SKU-1",
        None,
        DependencyState.UNRESOLVED,
    )
    duplicate = DuplicateRelation(
        deterministic_id(second_raw, "duplicate"),
        second_raw,
        RAW_ID,
        ComparisonScope.SAME_RUN,
    )
    review = ReviewItem(
        deterministic_id(classification.id, "review"),
        UUID(RUN),
        RAW_ID,
        classification.id,
        (
            ReviewReason(
                issue.id,
                issue.field_path,
                issue.summary,
                REFS,
                (dependency.id,),
                (duplicate.id,),
            ),
        ),
        FIXED_TIME,
    )

    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessionmaker(migrated_engine), _repositories)

    with uow() as work:
        work.candidates.add(initial)
        work.candidates.add_transformation(transformation)
        work.candidates.add_issue(issue)
        work.classifications.add(classification)
        work.classifications.add_dependency(dependency)
        work.classifications.add_duplicate(duplicate)
        work.reviews.add(review)
        work.commit()
    with uow() as work:
        assert work.candidates.get(initial.id) == initial
        assert work.candidates.for_run(UUID(RUN)) == (initial,)
        assert work.candidates.transformations(initial.id) == (transformation,)
        assert work.candidates.issues(initial.id) == (issue,)
        assert work.classifications.for_revision(initial.id) == (classification,)
        assert work.classifications.dependencies(classification.id) == (dependency,)
        assert work.classifications.duplicates(second_raw) == (duplicate,)
        assert work.reviews.for_run(UUID(RUN)) == (review,)
        # A staged child and its changed field roll back unless explicitly committed.
        child = replace(
            initial,
            id=candidate_revision_id(RAW_ID, 2),
            revision_number=2,
            parent_revision_id=initial.id,
            origin="SKU_ZERO_PADDING",
            payload=replace(
                initial.payload, notes=CandidateField(FieldState.ABSENT, None, REFS)
            ),
        )
        work.candidates.add(child)
    with uow() as work:
        assert work.candidates.for_run(UUID(RUN)) == (initial,)
        with pytest.raises(LookupError):
            work.candidates.get(child.id)
        with pytest.raises(ValueError, match="immutable"):
            work.candidates.add(
                replace(
                    initial,
                    origin="normalise",
                    payload=replace(
                        initial.payload,
                        name=CandidateField.known("changed", source_refs=REFS),
                    ),
                )
            )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE candidate_revision SET entity_type='customer'",
        "UPDATE candidate_revision SET entity_type=NULL",
        "UPDATE candidate_revision SET payload=jsonb_set(payload, "
        "'{entity_type}', 'null')",
        "UPDATE candidate_revision SET payload='{}'",
        "UPDATE candidate_revision SET payload='[]'",
        "UPDATE candidate_revision SET revision_number=0",
        "UPDATE candidate_revision SET parent_revision_id=id",
        "UPDATE classification_result SET verdict='unknown'",
        "UPDATE classification_result SET readiness='CLEAN'",
        "UPDATE review_item SET display_state='closed'",
        "UPDATE candidate_revision SET "
        "raw_record_id='00000000-0000-0000-0000-000000000999'",
        "UPDATE transformation_event SET "
        "candidate_revision_id='00000000-0000-0000-0000-000000000999'",
        "UPDATE data_quality_issue SET "
        "candidate_revision_id='00000000-0000-0000-0000-000000000999'",
        "UPDATE classification_result SET "
        "candidate_revision_id='00000000-0000-0000-0000-000000000999'",
        "UPDATE dependency_record SET "
        "classification_id='00000000-0000-0000-0000-000000000999'",
        "UPDATE duplicate_relation SET "
        "earlier_raw_id='00000000-0000-0000-0000-000000000999'",
        "UPDATE review_item SET run_id='00000000-0000-0000-0000-000000000999'",
        "UPDATE review_item SET "
        "classification_id='00000000-0000-0000-0000-000000000999'",
        "UPDATE data_quality_issue SET source_refs='{}'",
        "UPDATE data_quality_issue SET severity='unknown'",
        "UPDATE transformation_event SET before='[]'",
        "UPDATE dependency_record SET state='unknown'",
        "UPDATE dependency_record SET kind='unknown'",
        "UPDATE duplicate_relation SET comparison_scope='unknown'",
        "UPDATE review_item SET reasons='[]'",
        "UPDATE transformation_event SET sequence=0",
        "UPDATE duplicate_relation SET earlier_raw_id=later_raw_id",
        "INSERT INTO classification_result SELECT "
        "'00000000-0000-5000-8000-000000000099', candidate_revision_id,rul"
        "es_version,fx_snapshot_id,verdict,readiness,evaluated_at FROM "
        "classification_result",
    ],
)
def test_derived_constraints(migrated_engine: Engine, statement: str) -> None:
    test_derived_evidence_round_trip_and_rollback(migrated_engine)
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(text(statement))


def test_all_typed_payloads_and_ordered_provenance_round_trip(
    migrated_engine: Engine,
) -> None:
    from datetime import datetime
    from decimal import Decimal

    from services.domain.candidates import (
        CandidateRevision,
        CustomerCandidate,
        OrderCandidate,
        RejectedCandidateShell,
        candidate_revision_id,
    )
    from services.domain.fields import CandidateField, FieldState, SourceRef
    from services.domain.ids import deterministic_id
    from services.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from services.infrastructure.runtime import _repositories
    from tests.unit.normalise.test_candidates import FIXED_TIME, product

    original = product()
    notes = CandidateField[str](FieldState.ABSENT, None, ())
    customer = CustomerCandidate(
        original.sku,
        original.name,
        notes,
        original.unit_price,
        original.listed_date,
        original.status,
        original.tags,
        notes,
    )
    order = OrderCandidate(
        original.sku,
        original.name,
        original.sku,
        original.unit_price,
        CandidateField.known(-2, source_refs=()),
        CandidateField.known(
            datetime.fromisoformat("2023-02-01T12:30:00.123456+02:30"), source_refs=()
        ),
        original.status,
        original.tags,
        original.notes,
    )
    revisions: list[CandidateRevision] = []
    for index, payload in enumerate((original, customer, order, None), 100):
        raw_id = UUID(int=index)
        if payload is None:
            payload = RejectedCandidateShell(raw_id, (SourceRef(raw_id),), ())
        elif index == 100:
            payload = replace(
                original,
                notes=CandidateField.known(
                    "Evidence",
                    source_refs=(SourceRef(raw_id, 3), SourceRef(raw_id, 1)),
                    transformation_refs=(
                        deterministic_id(raw_id, "t2"),
                        deterministic_id(raw_id, "t1"),
                    ),
                    issue_refs=(
                        deterministic_id(raw_id, "i2"),
                        deterministic_id(raw_id, "i1"),
                    ),
                ),
                unit_price_gbp=CandidateField.known(
                    Decimal("0.12000000000000000001"), source_refs=()
                ),
            )
        with migrated_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO raw_record (id,run_id,source_line_start,"
                    "source_line_end,kind,fields,field_count,parse_metadata) "
                    "VALUES (:id,:run,:line,:line,'data','[]',0,'{}')"
                ),
                {"id": raw_id, "run": RUN, "line": index},
            )
        revisions.append(
            CandidateRevision(
                candidate_revision_id(raw_id, 1),
                raw_id,
                1,
                None,
                "normalise",
                payload,
                FIXED_TIME,
            )
        )
    with SqlAlchemyUnitOfWork(sessionmaker(migrated_engine), _repositories) as work:
        for item in revisions:
            work.candidates.add(item)
        work.commit()
    with SqlAlchemyUnitOfWork(sessionmaker(migrated_engine), _repositories) as work:
        assert work.candidates.for_run(UUID(RUN)) == tuple(revisions)
        child = replace(
            revisions[0],
            id=candidate_revision_id(revisions[0].raw_record_id, 2),
            revision_number=2,
            parent_revision_id=revisions[0].id,
            origin="SKU_ZERO_PADDING",
        )
        work.candidates.add(child)
        work.commit()
    with SqlAlchemyUnitOfWork(sessionmaker(migrated_engine), _repositories) as work:
        assert work.candidates.get(child.id) == child
        assert work.candidates.get(revisions[0].id) == revisions[0]


def test_all_derived_repository_writes_rollback_together(
    migrated_engine: Engine,
) -> None:
    from services.domain.candidates import candidate_revision_id
    from services.domain.ids import deterministic_id
    from services.domain.issues import ComparisonScope
    from services.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from services.infrastructure.runtime import _repositories
    from tests.unit.normalise.test_candidates import RAW_ID, revision

    test_derived_evidence_round_trip_and_rollback(migrated_engine)
    initial = revision()
    with SqlAlchemyUnitOfWork(sessionmaker(migrated_engine), _repositories) as work:
        child = replace(
            initial,
            id=candidate_revision_id(RAW_ID, 2),
            revision_number=2,
            parent_revision_id=initial.id,
            origin="SKU_ZERO_PADDING",
        )
        result = work.classifications.for_revision(initial.id)[0]
        dependency = work.classifications.dependencies(result.id)[0]
        duplicate = work.classifications.duplicates(UUID(int=43))[0]
        review = work.reviews.for_run(UUID(RUN))[0]
        issue = work.candidates.issues(initial.id)[0]
        event = work.candidates.transformations(initial.id)[0]
        work.candidates.add(child)
        work.candidates.add_transformation(
            replace(
                event,
                id=deterministic_id(child.id, "event"),
                candidate_revision_id=child.id,
            )
        )
        work.candidates.add_issue(
            replace(
                issue,
                id=deterministic_id(child.id, "issue"),
                candidate_revision_id=child.id,
            )
        )
        result = replace(
            result,
            id=deterministic_id(child.id, "classification"),
            candidate_revision_id=child.id,
        )
        work.classifications.add(result)
        work.classifications.add_dependency(
            replace(
                dependency,
                id=deterministic_id(result.id, "dependency"),
                classification_id=result.id,
            )
        )
        work.classifications.add_duplicate(
            replace(
                duplicate,
                id=deterministic_id(result.id, "duplicate"),
                comparison_scope=ComparisonScope.EARLIER_RUN,
            )
        )
        work.reviews.add(
            replace(
                review,
                id=deterministic_id(result.id, "review"),
                classification_id=result.id,
            )
        )
        # All seven repositories have flushed; none owns the commit boundary.
    with migrated_engine.connect() as connection:
        for table in TABLES:
            assert connection.scalar(text(f'SELECT count(*) FROM "{table}"')) == 1
        assert connection.scalar(text("SELECT count(*) FROM raw_record")) == 2
