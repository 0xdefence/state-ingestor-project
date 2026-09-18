# Human Field Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let operators append run-scoped, hashed, reasoned edits to records under review. Each edit is re-checked against the rest of its run and still needs a human decision before it reaches canonical data.

**Architecture:** An edit appends a `HUMAN_EDIT` candidate revision, a `human_edit` row (a hash chain per record), and a new `review_assessment` row that becomes the review item's current assessment. A pure re-check (`services/pipeline/recheck.py`) runs the existing rule registry on the whole run, keeps only the edited record's effects, and applies citations (rule waivers) and dismissals (cross-record findings). A named check catalogue gives every business-rule issue a stable `check_id`. The FastAPI adapter exposes edit context, preview, and append endpoints with specific error codes. The React app adds a comparison-table `<dialog>` and an edit history.

**Tech Stack:** Python 3.12+, SQLAlchemy 2, Alembic, PostgreSQL, FastAPI, pytest; React 19, TanStack Query 5, Vitest, Testing Library, axe-core, Playwright.

**Spec:** [`docs/superpowers/specs/2026-09-18-human-field-editing-design.md`](../specs/2026-09-18-human-field-editing-design.md). Read it before starting any task; section numbers below (§) refer to it.

## Global Constraints

- Branch: `feature/human-field-editing`. Commit after every task. End commit messages with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- Tests are behavioural contracts: exact inputs, exact outputs, and explicit assertions that nothing else changed. Write each test first, watch it fail, then implement. Do not weaken an assertion to make it pass; if the spec is wrong, stop and ask.
- Append-only: never update or delete `candidate_revision`, `human_edit`, `review_assessment`, `review_decision`, `transformation_event`, `data_quality_issue`, or `edit_attempt_failure` rows.
- Edits are tied to their record's run. No edit data may affect another run until approved.
- An edit never promotes by itself. Only `decide_review` promotes an edited record.
- New identities: `human_edit.id` and `edit_attempt_failure.id` are UUIDv4 (`new_id()`); derived evidence ids are UUIDv5 (`deterministic_id`).
- The server clock sets every timestamp. Timestamps are timezone-aware.
- Error envelope `{error: {code, message, details}}`. Messages are plain language; database or driver text never crosses HTTP.
- Edit lock timeout: 5000 ms (`LOCK_TIMEOUT_MS`). Note limit: 2000 characters, trimmed. Preview debounce in the UI: 300 ms.
- Python checks: `.venv/bin/pytest -q`, `.venv/bin/ruff check services tests`, `.venv/bin/pyright services` (strict). Web checks (from `apps/web`): `bun run test`, `bun run typecheck`.
- PostgreSQL tests need `TEST_POSTGRES_URL` (default `postgresql+psycopg://alexis:alexis@127.0.0.1:55432/alexis`) with `CREATEDB`.
- UI copy: plain operational language, absolute timestamps with timezone, color never the only signal, Geist tokens from `apps/web/src/styles/tokens.css` (`--ink --muted --border --surface --canvas --field --focus --green --amber --red --purple --grey --radius-control --radius-panel --font-mono`).

## File Map

| File | Responsibility |
|---|---|
| `services/domain/edits.py` (new) | Edit reasons, citations, dismissals, field changes, `HumanEdit`, `ReviewAssessment`, `EditAttemptFailure`, canonical hashing and chain verification |
| `services/domain/issues.py` | `TransformationCode.HUMAN_EDIT`; `DataQualityIssue.check_id` |
| `services/domain/decisions.py` | `legal_outcomes(verdict, edited=...)`; `current_effective_state` |
| `db/migrations/versions/0006_human_edits.py` (new) | Tables, `check_id` column, backfills |
| `services/infrastructure/db/models.py` | ORM mirrors of the migration |
| `services/pipeline/rules/catalogue.py` (new) | Named check catalogue and citation validation |
| `services/pipeline/rules/domains.py`, `invariants.py`, `registry.py` | Emit `check_id`; catalogue in rules hash; `apply(..., only_raw_record_id=)` |
| `services/pipeline/rules/base.py`, `repairs.py` | Effect isolation; human-set fields; repairs skip them |
| `services/pipeline/edit_input.py` (new) | Operator input through CSV normalisers; builds the edited revision |
| `services/pipeline/recheck.py` (new) | Pure assessment of the edited record: waivers, dismissals, symmetric conflicts, verdict, readiness |
| `services/application/edit_ports.py` (new) | `EditRepository` protocol |
| `services/application/errors.py` | `CodedError`, `DatabaseUnavailable`, processing errors |
| `services/application/edit_errors.py` (new) | Edit error classes and codes |
| `services/application/edit_rules.py` (new) | Editability and dismissible findings, shared by commands and reads |
| `services/application/edits.py` (new) | `append_edit`, `preview_edit`, failure recording |
| `services/application/decisions.py`, `dependency_readiness.py` | Assessments, edit-aware legality, cascade guard, terminal-following readiness |
| `services/infrastructure/db/edit_repository.py` (new), `errors.py` (new) | Edit persistence; database error classification |
| `services/infrastructure/db/read_repository.py`, `services/application/views.py`, `queries.py`, `read_ports.py`, `query_services.py` | Current-assessment reads, edit history, edit context |
| `services/api/routes/edits.py` (new), `models.py`, `errors.py`, `routes/runs.py`, `app.py` | Endpoints and error mapping |
| `apps/web/src/api/contracts.ts`, `queries.ts`, `labels.ts` | Web contracts, calls, copy |
| `apps/web/src/features/edits/` (new) | `editModel.ts`, `EditDialog.tsx`, `EditHistory.tsx`, tests, fixtures |
| `apps/web/src/features/reviews/ReviewDetail.tsx`, `features/runs/EvidencePanel.tsx`, `styles/global.css` | Entry points, provenance labels, styles |
| `apps/web/e2e/operator-flow.spec.ts` | `E2E-02` |

---

### Task 1: Domain edit model and hash chain

**Files:**
- Create: `services/domain/edits.py`
- Test: `tests/unit/edits/__init__.py` (empty), `tests/unit/edits/test_edit_domain.py`

**Interfaces:**
- Produces: `EditReason`, `DismissalKind`, `Citation(check_id, field_path, accepted_value=None)`, `Dismissal(kind, counterpart_id, detail=None)`, `FieldInput(field_path, input_text)`, `FieldChange(field_path, input_text, before, after)`, `EditReasonMismatch`, `validate_reason(reason, changes, citations, dismissals)`, `canonical(value)`, `document_hash(mapping) -> str`, `HumanEdit`, `hash_edit(edit, payload) -> str`, `seal(edit, payload) -> HumanEdit`, `verify_chain(edits, payloads) -> int | None`, `ReviewAssessment`, `EditStage`, `EditAttemptFailure`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/edits/test_edit_domain.py
"""EDT-04 (pairing), EDT-06, EDT-07: edit invariants and the hash chain."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from services.domain.edits import (
    Citation,
    Dismissal,
    DismissalKind,
    EditReason,
    EditReasonMismatch,
    FieldChange,
    HumanEdit,
    ReviewAssessment,
    hash_edit,
    seal,
    validate_reason,
    verify_chain,
)
from services.domain.fields import CandidateField, FieldState
from tests.unit.classify.test_rules import PRODUCT, graph_for

EDIT_NOW = datetime(2026, 9, 18, 13, 7, 31, 123456, tzinfo=UTC)
PAYLOAD = graph_for(PRODUCT).revisions[0].payload
IDS = [UUID(f"5b0d7c2e-8a4f-4c1e-9f3a-2d6b8e1c7a{n:02d}") for n in range(10)]
CITE = Citation("vocab.product_category", "product.category", "Garden")
DISMISS = Dismissal(DismissalKind.DUPLICATE, IDS[9])
CHANGE = FieldChange(
    "product.stock_qty",
    "7",
    CandidateField[object](FieldState.KNOWN, 5, ()),
    CandidateField[object](FieldState.KNOWN, 7, ()),
)


def edit(number: int = 1, parent_hash: str | None = None, **changes: object) -> HumanEdit:
    base = HumanEdit(
        id=IDS[number],
        run_id=IDS[5],
        raw_record_id=IDS[6],
        review_item_id=IDS[7],
        edit_number=number,
        parent_revision_id=IDS[8],
        candidate_revision_id=IDS[number],
        reason=EditReason.DATA_WRONG,
        note="Checked with supplier",
        field_changes=(CHANGE,),
        citations=(),
        dismissals=(),
        rules_version="r" * 64,
        operator_name="Alexis",
        edited_at=EDIT_NOW,
        idempotency_key=f"key-{number}",
        request_fingerprint="f" * 64,
        parent_hash=parent_hash,
    )
    return replace(base, **changes)


@pytest.mark.parametrize(
    ("reason", "changes", "citations", "dismissals"),
    [
        (EditReason.DATA_WRONG, 1, (CITE,), ()),
        (EditReason.DATA_WRONG, 0, (), ()),
        (EditReason.BUSINESS_RULE_UPDATED, 1, (), ()),
        (EditReason.BUSINESS_RULE_UPDATED, 0, (CITE,), (DISMISS,)),
        (EditReason.GRAPH_WRONG, 1, (CITE,), ()),
        (EditReason.GRAPH_WRONG, 0, (), ()),
    ],
)
def test_reason_pairing_is_enforced(reason, changes, citations, dismissals):
    with pytest.raises(EditReasonMismatch):
        validate_reason(reason, changes, citations, dismissals)


@pytest.mark.parametrize(
    ("reason", "changes", "citations", "dismissals"),
    [
        (EditReason.DATA_WRONG, 1, (), ()),
        (EditReason.BUSINESS_RULE_UPDATED, 0, (CITE,), ()),
        (EditReason.GRAPH_WRONG, 1, (), ()),
        (EditReason.GRAPH_WRONG, 0, (), (DISMISS,)),
    ],
)
def test_reason_pairing_accepts_valid_edits(reason, changes, citations, dismissals):
    validate_reason(reason, changes, citations, dismissals)


def test_edit_invariants():
    with pytest.raises(ValueError, match="UUIDv4"):
        edit(id=UUID("00000000-0000-5000-8000-000000000000"))
    with pytest.raises(ValueError, match="parent hash"):
        edit(number=2, parent_hash=None)
    with pytest.raises(ValueError, match="parent hash"):
        edit(number=1, parent_hash="a" * 64)
    with pytest.raises(ValueError, match="note"):
        edit(note=" padded ")
    with pytest.raises(ValueError, match="note"):
        edit(note="x" * 2001)
    with pytest.raises(ValueError, match="timezone"):
        edit(edited_at=EDIT_NOW.replace(tzinfo=None))
    with pytest.raises(EditReasonMismatch):
        edit(field_changes=())


def test_hash_is_deterministic_and_sensitive_to_every_input():
    sealed = seal(edit(), PAYLOAD)
    assert len(sealed.content_hash) == 64
    assert sealed.content_hash == hash_edit(edit(), PAYLOAD)
    variants = [
        edit(note="Different note"),
        edit(operator_name="Sam"),
        edit(edited_at=EDIT_NOW + timedelta(microseconds=1)),
        edit(
            reason=EditReason.BUSINESS_RULE_UPDATED,
            citations=(CITE,),
        ),
        edit(field_changes=(replace(CHANGE, input_text="8"),)),
    ]
    assert all(hash_edit(v, PAYLOAD) != sealed.content_hash for v in variants)


def test_chain_links_parent_hashes_and_detects_tampering():
    first = seal(edit(1), PAYLOAD)
    second = seal(edit(2, first.content_hash, idempotency_key="k2"), PAYLOAD)
    third = seal(edit(3, second.content_hash, idempotency_key="k3"), PAYLOAD)
    payloads = {e.candidate_revision_id: PAYLOAD for e in (first, second, third)}
    assert first.parent_hash is None
    assert second.parent_hash == first.content_hash
    assert verify_chain((first, second, third), payloads) is None
    tampered = replace(second, note="Tampered")
    assert verify_chain((first, tampered, third), payloads) == 2


def test_assessment_sequence_and_edit_link():
    ReviewAssessment(IDS[7], 1, IDS[1], None, (), EDIT_NOW)
    ReviewAssessment(IDS[7], 2, IDS[2], IDS[3], (), EDIT_NOW)
    with pytest.raises(ValueError):
        ReviewAssessment(IDS[7], 2, IDS[2], None, (), EDIT_NOW)
    with pytest.raises(ValueError):
        ReviewAssessment(IDS[7], 1, IDS[2], IDS[3], (), EDIT_NOW)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q tests/unit/edits/test_edit_domain.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.domain.edits'`.

- [ ] **Step 3: Implement `services/domain/edits.py`**

```python
"""Append-only operator edits: reasons, citations, dismissals and hash chain."""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, cast
from uuid import UUID

from services.domain.fields import CandidateField
from services.domain.issues import ReviewReason

NOTE_LIMIT = 2000
_HASH = re.compile(r"[0-9a-f]{64}")

type EditStage = Literal["lock", "parse", "recheck", "persist"]


class EditReason(StrEnum):
    DATA_WRONG = "data_wrong"
    BUSINESS_RULE_UPDATED = "business_rule_updated"
    GRAPH_WRONG = "graph_wrong"


class DismissalKind(StrEnum):
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    DEPENDENCY_MATCH = "dependency_match"


@dataclass(frozen=True, slots=True)
class Citation:
    check_id: str
    field_path: str
    accepted_value: str | None = None


@dataclass(frozen=True, slots=True)
class Dismissal:
    kind: DismissalKind
    counterpart_id: UUID
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class FieldInput:
    """`input_text=None` asks for the field to be marked empty (absent)."""

    field_path: str
    input_text: str | None


@dataclass(frozen=True, slots=True)
class FieldChange:
    field_path: str
    input_text: str | None
    before: CandidateField[object]
    after: CandidateField[object]


class EditReasonMismatch(ValueError):
    """Field changes, citations and dismissals do not fit the edit reason."""


def validate_reason(
    reason: EditReason,
    changes: int,
    citations: Sequence[Citation],
    dismissals: Sequence[Dismissal],
) -> None:
    if reason is EditReason.DATA_WRONG:
        if citations or dismissals:
            raise EditReasonMismatch(
                "A data correction cannot cite rules or dismiss findings."
            )
        if changes < 1:
            raise EditReasonMismatch("A data correction must change at least one field.")
    elif reason is EditReason.BUSINESS_RULE_UPDATED:
        if dismissals:
            raise EditReasonMismatch(
                "A business-rule edit cannot dismiss cross-record findings."
            )
        if not citations:
            raise EditReasonMismatch("Name at least one outdated business rule.")
    else:
        if citations:
            raise EditReasonMismatch("A record-links edit cannot cite business rules.")
        if changes + len(dismissals) < 1:
            raise EditReasonMismatch(
                "Change a reference field or mark at least one finding as wrong."
            )


def canonical(value: object) -> object:
    """Lossless, order-stable JSON form used only for hashing."""
    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, datetime):
        instant = value.astimezone(UTC) if value.tzinfo else value
        return {"$datetime": instant.isoformat(timespec="microseconds")}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [canonical(item) for item in cast(Sequence[object], value)]
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {str(key): canonical(item) for key, item in mapping.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "$type": type(value).__name__,
            **{item.name: canonical(getattr(value, item.name)) for item in fields(value)},
        }
    raise TypeError(f"Unsupported value in edit hash: {type(value).__name__}")


def document_hash(document: Mapping[str, object]) -> str:
    encoded = json.dumps(
        canonical(document),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class HumanEdit:
    id: UUID
    run_id: UUID
    raw_record_id: UUID
    review_item_id: UUID
    edit_number: int
    parent_revision_id: UUID
    candidate_revision_id: UUID
    reason: EditReason
    note: str | None
    field_changes: tuple[FieldChange, ...]
    citations: tuple[Citation, ...]
    dismissals: tuple[Dismissal, ...]
    rules_version: str
    operator_name: str
    edited_at: datetime
    idempotency_key: str
    request_fingerprint: str
    parent_hash: str | None
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.id.version != 4:
            raise ValueError("edit identity requires UUIDv4")
        if type(self.edit_number) is not int or self.edit_number < 1:
            raise ValueError("edit number must be positive")
        if (self.parent_hash is None) != (self.edit_number == 1):
            raise ValueError("only the first edit has no parent hash")
        for value in (self.parent_hash, self.content_hash or None):
            if value is not None and not _HASH.fullmatch(value):
                raise ValueError("hashes are 64 lowercase hex characters")
        if not _HASH.fullmatch(self.request_fingerprint):
            raise ValueError("request fingerprint must be a SHA-256 hex digest")
        if not self.operator_name.strip() or not self.idempotency_key.strip():
            raise ValueError("operator and idempotency key must be nonempty")
        if self.note is not None and (
            not self.note or self.note != self.note.strip() or len(self.note) > NOTE_LIMIT
        ):
            raise ValueError("note must be trimmed, nonempty and at most 2000 characters")
        if self.edited_at.tzinfo is None:
            raise ValueError("edit time requires a timezone")
        paths = [change.field_path for change in self.field_changes]
        if paths != sorted(set(paths)):
            raise ValueError("field changes must be unique and sorted by path")
        validate_reason(
            self.reason, len(self.field_changes), self.citations, self.dismissals
        )


def hash_edit(edit: HumanEdit, payload: object) -> str:
    """Hash everything an operator or the pipeline could later dispute."""
    return document_hash(
        {
            "run_id": edit.run_id,
            "raw_record_id": edit.raw_record_id,
            "review_item_id": edit.review_item_id,
            "edit_number": edit.edit_number,
            "parent_revision_id": edit.parent_revision_id,
            "parent_hash": edit.parent_hash,
            "candidate_revision_id": edit.candidate_revision_id,
            "payload": payload,
            "reason": edit.reason,
            "note": edit.note,
            "field_changes": edit.field_changes,
            "citations": edit.citations,
            "dismissals": edit.dismissals,
            "rules_version": edit.rules_version,
            "operator_name": edit.operator_name,
            "edited_at": edit.edited_at,
        }
    )


def seal(edit: HumanEdit, payload: object) -> HumanEdit:
    return replace(edit, content_hash=hash_edit(edit, payload))


def verify_chain(
    edits: Sequence[HumanEdit], payloads: Mapping[UUID, object]
) -> int | None:
    """Return the first edit number whose hash or parent link is wrong."""
    previous: HumanEdit | None = None
    for edit in sorted(edits, key=lambda item: item.edit_number):
        expected_parent = previous.content_hash if previous else None
        if edit.parent_hash != expected_parent or edit.content_hash != hash_edit(
            edit, payloads[edit.candidate_revision_id]
        ):
            return edit.edit_number
        previous = edit
    return None


@dataclass(frozen=True, slots=True)
class ReviewAssessment:
    review_item_id: UUID
    sequence: int
    classification_id: UUID
    human_edit_id: UUID | None
    reasons: tuple[ReviewReason, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("assessment sequence must be positive")
        if (self.human_edit_id is None) != (self.sequence == 1):
            raise ValueError("only the first assessment has no edit")


@dataclass(frozen=True, slots=True)
class EditAttemptFailure:
    id: UUID
    review_item_id: UUID
    run_id: UUID
    stage: EditStage
    code: str
    reference: UUID
    error_type: str
    operator_name: str
    occurred_at: datetime
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest -q tests/unit/edits/test_edit_domain.py && .venv/bin/pyright services/domain/edits.py`
Expected: all PASS; pyright `0 errors`.

- [ ] **Step 5: Commit**

```bash
git add services/domain/edits.py tests/unit/edits/__init__.py tests/unit/edits/test_edit_domain.py
git commit -m "feat: add append-only edit domain model and hash chain"
```

---

### Task 2: Migration 0006 and ORM models

**Files:**
- Create: `db/migrations/versions/0006_human_edits.py`
- Modify: `services/infrastructure/db/models.py` (add three models; `check_id` column; raw-record unique constraint)
- Test: `tests/integration/edits/__init__.py` (empty), `tests/integration/edits/support.py`, `tests/integration/edits/test_edit_migration.py`

**Interfaces:**
- Produces tables `human_edit`, `review_assessment`, `edit_attempt_failure`, column `data_quality_issue.check_id`, constraint `uq_raw_record_id_run`. ORM classes `HumanEditModel`, `ReviewAssessmentModel`, `EditAttemptFailureModel`; `DataQualityIssueModel.check_id: Mapped[str | None]`.
- Produces test helpers in `tests/integration/edits/support.py`: `EDIT_NOW`, `EditClock`, CSV constants, `processed(engine, tmp_path, csv) -> UUID`, `review_for(engine, run_id, identifier, occurrence=0) -> tuple[ReviewItem, CandidateRevision]`, `count(engine, table) -> int`. Later tasks add `edit(...)`.

- [ ] **Step 1: Write the shared test support module**

```python
# tests/integration/edits/support.py
"""Shared PostgreSQL setup for edit tests: processed runs and review lookups."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from services.application.load import stage_run
from services.domain.candidates import CandidateRevision
from services.domain.issues import ReviewItem
from services.pipeline.classify import classify_run
from services.pipeline.rules.duplicates import business_key
from services.pipeline.rules.registry import default_registry
from tests.integration.pipeline.test_classification_core import prepared
from tests.integration.review.test_decisions import uow_for
from tests.unit.classify.test_rules import CUSTOMER, ORDER, PRODUCT, FixedClock

EDIT_NOW = datetime(2026, 9, 18, 13, 7, 31, 123456, tzinfo=UTC)
GARDEN_PRODUCT = PRODUCT.replace("Electronics", "Garden")
PAUSED_CUSTOMER = CUSTOMER.replace(",active,", ",Paused,")
PADDED_ORDER = "ORDER,ORD-3001,Sofia Rossi,SKU-00204,19.99,2,2024-01-07,Shipped,,\n"
UNLINKED_ORDER = "ORDER,ORD-3001,S. Rossi,SKU-2004,19.99,1,2024-01-07,shipped,,\n"
ZERO_QUANTITY_ORDER = "ORDER,ORD-3002,Sofia Rossi,SKU-2004,19.99,0,2024-01-07,shipped,,\n"
MANY_QUANTITY_ORDER = "ORDER,ORD-3003,Sofia Rossi,SKU-2004,19.99,many,2024-01-07,shipped,,\n"
__all__ = ["CUSTOMER", "ORDER", "PRODUCT", "uow_for"]


class EditClock:
    def now(self) -> datetime:
        return EDIT_NOW


def processed(engine, tmp_path, csv: str) -> UUID:
    run = prepared(engine, tmp_path, csv)
    classify_run(run, default_registry(), uow_for(engine), FixedClock())
    stage_run(run, lambda: uow_for(engine), FixedClock())
    return run


def review_for(
    engine, run_id: UUID, identifier: str, occurrence: int = 0
) -> tuple[ReviewItem, CandidateRevision]:
    with uow_for(engine) as uow:
        lines = {raw.id: raw.source_line_start for raw in uow.raw_records.for_run(run_id)}
        matches = []
        for item in uow.reviews.for_run(run_id):
            terminal = uow.candidates.terminal(item.raw_record_id)
            key = business_key(terminal.payload)
            if key is not None and key[1] == identifier:
                matches.append((lines[item.raw_record_id], item, terminal))
    matches.sort(key=lambda match: match[0])
    _, item, terminal = matches[occurrence]
    return item, terminal


def count(engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.scalar(text(f"SELECT count(*) FROM {table}"))
```

`test_classification_core.prepared` and `uow_for` already use the migrated `engine` fixture; import it into each test module with `from tests.integration.foundation.test_concurrent_ingest import engine as engine`.

- [ ] **Step 2: Write the failing migration tests**

```python
# tests/integration/edits/test_edit_migration.py
"""EDT-30, EDT-40: run-scoped edit rows, backfills, downgrade and model parity."""

from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from services.infrastructure.db.models import Base
from tests.integration.edits.support import (
    CUSTOMER,
    GARDEN_PRODUCT,
    MANY_QUANTITY_ORDER,
    PRODUCT,
    ZERO_QUANTITY_ORDER,
    processed,
    review_for,
    uow_for,
)
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.foundation.test_migrations import migration_config

INSERT_EDIT = text(
    """INSERT INTO human_edit (id, run_id, raw_record_id, review_item_id,
    edit_number, parent_revision_id, candidate_revision_id, reason, note,
    field_changes, citations, dismissals, rules_version, operator_name, edited_at,
    idempotency_key, request_fingerprint, content_hash, parent_hash)
    VALUES (:id, :run, :raw, :review, 1, :parent, :candidate, 'data_wrong', NULL,
    '[{}]'::jsonb, '[]'::jsonb, '[]'::jsonb, 'v', 'Alexis', now(), :key,
    :hash, :hash, NULL)"""
)


def test_edit_run_must_match_record_run(engine, tmp_path):
    first = processed(engine, tmp_path / "a", CUSTOMER + GARDEN_PRODUCT)
    second = processed(engine, tmp_path / "b", PRODUCT)
    item, terminal = review_for(engine, first, "SKU-2004")
    with uow_for(engine) as uow:
        chain = [
            r for r in uow.candidates.for_run(first) if r.raw_record_id == item.raw_record_id
        ]
    values = {
        "raw": item.raw_record_id,
        "review": item.id,
        "parent": chain[0].id,
        "candidate": terminal.id,
        "hash": "a" * 64,
    }
    with engine.begin() as connection, pytest.raises(IntegrityError, match="fk_human_edit_record_run"):
        connection.execute(INSERT_EDIT, {**values, "id": uuid4(), "run": second, "key": "x"})
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(INSERT_EDIT, {**values, "id": uuid4(), "run": first, "key": "y"})
        transaction.rollback()


def test_migration_backfills_assessments_and_check_ids(engine, tmp_path):
    csv = CUSTOMER + GARDEN_PRODUCT + ZERO_QUANTITY_ORDER + MANY_QUANTITY_ORDER
    processed(engine, tmp_path, csv)
    config = migration_config(engine.url.render_as_string(hide_password=False))
    command.downgrade(config, "0005_review_decisions")
    tables = inspect(engine).get_table_names()
    assert {"human_edit", "review_assessment", "edit_attempt_failure"}.isdisjoint(tables)
    assert "check_id" not in {c["name"] for c in inspect(engine).get_columns("data_quality_issue")}
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        items = connection.execute(
            text("SELECT id, classification_id, reasons FROM review_item ORDER BY id")
        ).all()
        assessments = connection.execute(
            text(
                "SELECT review_item_id, sequence, classification_id, human_edit_id, "
                "reasons FROM review_assessment ORDER BY review_item_id"
            )
        ).all()
        assert [(a[0], a[1], a[2], a[3], a[4]) for a in assessments] == [
            (i[0], 1, i[1], None, i[2]) for i in items
        ]
        mapped = dict(
            connection.execute(
                text(
                    "SELECT code || ' ' || field_path || ' ' || summary, check_id "
                    "FROM data_quality_issue WHERE code IN "
                    "('INVALID_CATEGORY','INVALID_INTEGER')"
                )
            ).all()
        )
    assert mapped[
        "INVALID_CATEGORY product.category Category 'Garden' is outside the "
        "registered vocabulary."
    ] == "vocab.product_category"
    assert mapped[
        "INVALID_INTEGER order.quantity Order quantity must be non-zero."
    ] == "limit.order_quantity_nonzero"
    parse_failures = [k for k in mapped if "Expected an integer" in k]
    assert len(parse_failures) == 1 and mapped[parse_failures[0]] is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q tests/integration/edits/test_edit_migration.py`
Expected: FAIL (`relation "human_edit" does not exist`, or downgrade target unknown).

- [ ] **Step 4: Write the migration**

```python
# db/migrations/versions/0006_human_edits.py
"""Human edits: append-only edits, review assessments, failures and check ids."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_human_edits"
down_revision = "0005_review_decisions"
branch_labels = None
depends_on = None

UUID4 = (
    "substring(id::text, 15, 1) = '4' "
    "AND substring(id::text, 20, 1) IN ('8','9','a','b')"
)
HEX = "'^[0-9a-f]{64}$'"
CHECK_BACKFILL = """
UPDATE data_quality_issue SET check_id = CASE
  WHEN code = 'INVALID_CATEGORY' AND field_path = 'product.category'
    THEN 'vocab.product_category'
  WHEN code = 'INVALID_STATUS' AND summary LIKE '%is outside the registered domain.'
    THEN 'vocab.' || split_part(field_path, '.', 1) || '_status'
  WHEN code = 'UNKNOWN_TAG' THEN 'vocab.' || split_part(field_path, '.', 1) || '_tags'
  WHEN code = 'INVALID_IDENTIFIER' AND field_path = 'customer.customer_id'
    THEN 'format.customer_id'
  WHEN code = 'INVALID_IDENTIFIER' AND field_path IN ('product.sku', 'order.sku')
    THEN 'format.sku'
  WHEN code = 'INVALID_IDENTIFIER' AND field_path = 'order.order_id'
    THEN 'format.order_id'
  WHEN code = 'INVALID_EMAIL' THEN 'format.email'
  WHEN code = 'INVALID_DATE' AND summary = 'Expected a date without a time component.'
    THEN 'format.date_only'
  WHEN code = 'MISSING_REQUIRED_VALUE' AND summary LIKE 'Required field is %'
    THEN 'limit.required_field'
  WHEN code = 'INVALID_AMOUNT' AND field_path IN ('product.unit_price', 'order.unit_price')
    THEN 'limit.positive_amount'
  WHEN code = 'INVALID_AMOUNT' AND field_path = 'customer.lifetime_spend'
    THEN 'limit.nonnegative_amount'
  WHEN code = 'INVALID_INTEGER' AND summary = 'Order quantity must be non-zero.'
    THEN 'limit.order_quantity_nonzero'
  WHEN code = 'INVALID_STRUCTURE' AND field_path = 'customer.quantity'
    THEN 'limit.customer_quantity_empty'
  WHEN code = 'REFUND_CONFLICT' THEN 'invariant.refund'
  WHEN code = 'STOCK_STATUS_CONFLICT' THEN 'invariant.stock_status'
END
"""


def upgrade() -> None:
    op.create_unique_constraint("uq_raw_record_id_run", "raw_record", ["id", "run_id"])
    op.create_table(
        "human_edit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("raw_record_id", sa.Uuid(), nullable=False),
        sa.Column("review_item_id", sa.Uuid(), sa.ForeignKey("review_item.id"), nullable=False),
        sa.Column("edit_number", sa.Integer(), nullable=False),
        sa.Column(
            "parent_revision_id", sa.Uuid(), sa.ForeignKey("candidate_revision.id"), nullable=False
        ),
        sa.Column(
            "candidate_revision_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_revision.id"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("field_changes", JSONB(), nullable=False),
        sa.Column("citations", JSONB(), nullable=False),
        sa.Column("dismissals", JSONB(), nullable=False),
        sa.Column("rules_version", sa.String(), nullable=False),
        sa.Column("operator_name", sa.String(), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("parent_hash", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["raw_record_id", "run_id"],
            ["raw_record.id", "raw_record.run_id"],
            name="fk_human_edit_record_run",
        ),
        sa.UniqueConstraint("raw_record_id", "edit_number", name="uq_human_edit_number"),
        sa.UniqueConstraint("candidate_revision_id", name="uq_human_edit_revision"),
        sa.UniqueConstraint("idempotency_key", name="uq_human_edit_idempotency"),
        sa.CheckConstraint("edit_number > 0", name="ck_human_edit_number"),
        sa.CheckConstraint(
            "reason IN ('data_wrong','business_rule_updated','graph_wrong')",
            name="ck_human_edit_reason",
        ),
        sa.CheckConstraint(
            "(parent_hash IS NULL) = (edit_number = 1)", name="ck_human_edit_parent_hash"
        ),
        sa.CheckConstraint(
            f"content_hash ~ {HEX} AND request_fingerprint ~ {HEX} "
            f"AND (parent_hash IS NULL OR parent_hash ~ {HEX})",
            name="ck_human_edit_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(field_changes) = 'array' AND jsonb_typeof(citations) = 'array' "
            "AND jsonb_typeof(dismissals) = 'array'",
            name="ck_human_edit_arrays",
        ),
        sa.CheckConstraint(
            "(reason = 'business_rule_updated') = (jsonb_array_length(citations) > 0)",
            name="ck_human_edit_citations",
        ),
        sa.CheckConstraint(
            "reason = 'graph_wrong' OR jsonb_array_length(dismissals) = 0",
            name="ck_human_edit_dismissals",
        ),
        sa.CheckConstraint(
            "reason <> 'graph_wrong' OR "
            "jsonb_array_length(dismissals) + jsonb_array_length(field_changes) > 0",
            name="ck_human_edit_graph",
        ),
        sa.CheckConstraint(
            "reason <> 'data_wrong' OR jsonb_array_length(field_changes) > 0",
            name="ck_human_edit_data",
        ),
        sa.CheckConstraint(
            "note IS NULL OR (length(note) BETWEEN 1 AND 2000 AND note = btrim(note))",
            name="ck_human_edit_note",
        ),
        sa.CheckConstraint(
            "length(trim(operator_name)) > 0 AND length(trim(idempotency_key)) > 0",
            name="ck_human_edit_actor",
        ),
        sa.CheckConstraint(UUID4, name="ck_human_edit_uuid4"),
    )
    op.create_table(
        "review_assessment",
        sa.Column("review_item_id", sa.Uuid(), sa.ForeignKey("review_item.id"), primary_key=True),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column(
            "classification_id",
            sa.Uuid(),
            sa.ForeignKey("classification_result.id"),
            nullable=False,
        ),
        sa.Column("human_edit_id", sa.Uuid(), sa.ForeignKey("human_edit.id"), nullable=True),
        sa.Column("reasons", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("classification_id", name="uq_assessment_classification"),
        sa.UniqueConstraint("human_edit_id", name="uq_assessment_edit"),
        sa.CheckConstraint("sequence > 0", name="ck_assessment_sequence"),
        sa.CheckConstraint("(human_edit_id IS NULL) = (sequence = 1)", name="ck_assessment_edit"),
        sa.CheckConstraint("jsonb_typeof(reasons) = 'array'", name="ck_assessment_reasons"),
    )
    op.execute(
        """INSERT INTO review_assessment
           (review_item_id, sequence, classification_id, human_edit_id, reasons, created_at)
           SELECT id, 1, classification_id, NULL, reasons, created_at FROM review_item"""
    )
    op.create_table(
        "edit_attempt_failure",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("review_item_id", sa.Uuid(), sa.ForeignKey("review_item.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("reference", sa.Uuid(), nullable=False),
        sa.Column("error_type", sa.String(), nullable=False),
        sa.Column("operator_name", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("reference", name="uq_edit_failure_reference"),
        sa.CheckConstraint(
            "stage IN ('lock','parse','recheck','persist')", name="ck_edit_failure_stage"
        ),
        sa.CheckConstraint(UUID4, name="ck_edit_failure_uuid4"),
    )
    op.add_column("data_quality_issue", sa.Column("check_id", sa.String(), nullable=True))
    op.execute(CHECK_BACKFILL)


def downgrade() -> None:
    op.drop_column("data_quality_issue", "check_id")
    op.drop_table("edit_attempt_failure")
    op.drop_table("review_assessment")
    op.drop_table("human_edit")
    op.drop_constraint("uq_raw_record_id_run", "raw_record", type_="unique")
```

- [ ] **Step 5: Mirror the migration in `services/infrastructure/db/models.py`**

In `RawRecordModel.__table_args__`, add `UniqueConstraint("id", "run_id", name="uq_raw_record_id_run"),`. In `DataQualityIssueModel`, add after `tentative_cause`:

```python
    check_id: Mapped[str | None]
```

Append at the end of the file:

```python
_UUID4 = (
    "substring(id::text, 15, 1) = '4' "
    "AND substring(id::text, 20, 1) IN ('8','9','a','b')"
)


class HumanEditModel(Base):
    __tablename__ = "human_edit"
    __table_args__ = (
        ForeignKeyConstraint(
            ["raw_record_id", "run_id"],
            ["raw_record.id", "raw_record.run_id"],
            name="fk_human_edit_record_run",
        ),
        UniqueConstraint("raw_record_id", "edit_number", name="uq_human_edit_number"),
        UniqueConstraint("candidate_revision_id", name="uq_human_edit_revision"),
        UniqueConstraint("idempotency_key", name="uq_human_edit_idempotency"),
        CheckConstraint("edit_number > 0", name="ck_human_edit_number"),
        CheckConstraint(_UUID4, name="ck_human_edit_uuid4"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID]
    raw_record_id: Mapped[UUID]
    review_item_id: Mapped[UUID] = mapped_column(ForeignKey("review_item.id"))
    edit_number: Mapped[int]
    parent_revision_id: Mapped[UUID] = mapped_column(ForeignKey("candidate_revision.id"))
    candidate_revision_id: Mapped[UUID] = mapped_column(ForeignKey("candidate_revision.id"))
    reason: Mapped[str]
    note: Mapped[str | None]
    field_changes: Mapped[list[object]] = mapped_column(JSONB)
    citations: Mapped[list[object]] = mapped_column(JSONB)
    dismissals: Mapped[list[object]] = mapped_column(JSONB)
    rules_version: Mapped[str]
    operator_name: Mapped[str]
    edited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str]
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    parent_hash: Mapped[str | None] = mapped_column(String(64))


class ReviewAssessmentModel(Base):
    __tablename__ = "review_assessment"
    __table_args__ = (
        UniqueConstraint("classification_id", name="uq_assessment_classification"),
        UniqueConstraint("human_edit_id", name="uq_assessment_edit"),
        CheckConstraint("sequence > 0", name="ck_assessment_sequence"),
    )
    review_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("review_item.id"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(primary_key=True)
    classification_id: Mapped[UUID] = mapped_column(ForeignKey("classification_result.id"))
    human_edit_id: Mapped[UUID | None] = mapped_column(ForeignKey("human_edit.id"))
    reasons: Mapped[list[object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EditAttemptFailureModel(Base):
    __tablename__ = "edit_attempt_failure"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_edit_failure_reference"),
        CheckConstraint(_UUID4, name="ck_edit_failure_uuid4"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    review_item_id: Mapped[UUID] = mapped_column(ForeignKey("review_item.id"))
    run_id: Mapped[UUID] = mapped_column(ForeignKey("run.id"))
    stage: Mapped[str]
    code: Mapped[str]
    reference: Mapped[UUID]
    error_type: Mapped[str]
    operator_name: Mapped[str]
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

Check constraints are not compared by `compare_metadata`, so the model lists only the ones the ORM benefits from; the migration is authoritative for the rest.

- [ ] **Step 6: Run the new and existing migration tests**

Run: `.venv/bin/pytest -q tests/integration/edits/test_edit_migration.py tests/integration/foundation/test_migrations.py tests/integration/review/test_decision_migration.py tests/integration/pipeline/test_canonical_migration.py`
Expected: PASS. If `test_models_match_migrated_schema` reports a diff, fix the model to match the migration exactly (column types, nullability, constraint names).

- [ ] **Step 7: Commit**

```bash
git add db/migrations/versions/0006_human_edits.py services/infrastructure/db/models.py tests/integration/edits
git commit -m "feat: add human edit, assessment and failure tables with backfills"
```

---

### Task 3: Check catalogue and check ids on issues

**Files:**
- Create: `services/pipeline/rules/catalogue.py`
- Modify: `services/domain/issues.py` (add `TransformationCode.HUMAN_EDIT`; add `check_id` to `DataQualityIssue`), `services/pipeline/rules/invariants.py`, `services/pipeline/rules/domains.py`, `services/pipeline/rules/registry.py`, `services/infrastructure/db/derived_repositories.py` (persist/read `check_id`)
- Test: `tests/unit/edits/test_catalogue.py`

**Interfaces:**
- Consumes: `Citation` (Task 1), `DataQualityIssueModel.check_id` (Task 2).
- Produces: `CheckKind`, `CatalogueCheck(id, kind, fields, issue_code, label)`, `CATALOGUE`, `BY_ID`, `entity_checks(entity) -> tuple[CatalogueCheck, ...]`, `CitationError(ValueError)`, `validate_citation(entity, citation)`. `DataQualityIssue.check_id: str | None = None` (last field). `validation_issue(graph, revision, code, path, summary, refs, check_id)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/edits/test_catalogue.py
"""EDT-14 and catalogue integrity: every rule issue carries its stable check id."""

import pytest

from services.domain.edits import Citation
from services.domain.issues import IssueCode
from services.pipeline.rules.catalogue import (
    BY_ID,
    CATALOGUE,
    CheckKind,
    CitationError,
    entity_checks,
    validate_citation,
)
from services.pipeline.rules.domains import TerminalDomainConfig
from services.pipeline.rules.registry import RuleRegistry, default_registry
from tests.unit.classify.test_rules import CUSTOMER, ORDER, PRODUCT, graph_for


def test_catalogue_ids_are_unique_and_required_fields_match_config():
    assert len(BY_ID) == len(CATALOGUE) == 25
    assert BY_ID["limit.required_field"].fields == TerminalDomainConfig().required_fields
    assert all(c.issue_code is None for c in CATALOGUE if c.kind is CheckKind.INTERPRETATION)
    assert all(
        c.issue_code is not None for c in CATALOGUE if c.kind is not CheckKind.INTERPRETATION
    )


def test_entity_checks_only_list_that_entity():
    product = entity_checks("product")
    assert "vocab.product_category" in {c.id for c in product}
    assert "vocab.order_status" not in {c.id for c in product}
    assert all(f.startswith("product.") for c in product for f in c.fields)


@pytest.mark.parametrize(
    ("line", "code", "path", "check"),
    [
        (PRODUCT.replace("Electronics", "Garden"), IssueCode.INVALID_CATEGORY, "product.category", "vocab.product_category"),
        (PRODUCT.replace("accessories", "gift"), IssueCode.UNKNOWN_TAG, "product.tags", "vocab.product_tags"),
        (CUSTOMER.replace("s@example.org", "not an email"), IssueCode.INVALID_EMAIL, "customer.email", "format.email"),
        (CUSTOMER.replace("CUST-1001", "CUST-10"), IssueCode.INVALID_IDENTIFIER, "customer.customer_id", "format.customer_id"),
        (CUSTOMER.replace("2024-01-01", "2024/02/03 14:22:00"), IssueCode.INVALID_DATE, "customer.signup_date", "format.date_only"),
        (PRODUCT.replace(",Widget,", ",,"), IssueCode.MISSING_REQUIRED_VALUE, "product.name", "limit.required_field"),
        (PRODUCT.replace(",19.99,", ",-1,"), IssueCode.INVALID_AMOUNT, "product.unit_price", "limit.positive_amount"),
        (ORDER.replace(",1,", ",0,"), IssueCode.INVALID_INTEGER, "order.quantity", "limit.order_quantity_nonzero"),
        (ORDER.replace(",1,", ",-1,"), IssueCode.REFUND_CONFLICT, "order.quantity", "invariant.refund"),
        (PRODUCT.replace(",5,", ",0,"), IssueCode.STOCK_STATUS_CONFLICT, "product.stock_qty", "invariant.stock_status"),
    ],
)
def test_rule_issues_carry_their_check_id(line, code, path, check):
    graph = default_registry().apply(graph_for(CUSTOMER + PRODUCT + line))
    matching = [i for i in graph.issues if i.code is code and i.field_path == path]
    assert matching and {i.check_id for i in matching} == {check}


def test_normaliser_issues_have_no_check_id():
    graph = default_registry().apply(graph_for(ORDER.replace(",1,", ",many,")))
    parse = [i for i in graph.issues if i.code is IssueCode.INVALID_INTEGER]
    assert len(parse) == 1 and parse[0].check_id is None


@pytest.mark.parametrize(
    ("entity", "citation"),
    [
        ("product", Citation("vocab.order_status", "order.status", "held")),
        ("order", Citation("format.sku", "order.order_id")),
        ("product", Citation("vocab.product_category", "product.category")),
        ("product", Citation("format.sku", "product.sku", "SKU-1")),
        ("product", Citation("unknown.check", "product.sku")),
        ("product", Citation("vocab.product_category", "product.category", " Garden ")),
    ],
)
def test_invalid_citations_are_refused(entity, citation):
    with pytest.raises(CitationError):
        validate_citation(entity, citation)


def test_valid_citations_pass():
    validate_citation("product", Citation("vocab.product_category", "product.category", "Garden"))
    validate_citation("order", Citation("interpret.slash_date_mdy", "order.ordered_at"))
    validate_citation("order", Citation("limit.order_quantity_nonzero", "order.quantity"))


def test_catalogue_is_part_of_the_rules_version(monkeypatch):
    registry = default_registry()
    import services.pipeline.rules.registry as module

    monkeypatch.setattr(module, "CATALOGUE", module.CATALOGUE[:-1])
    assert RuleRegistry(registry.rules).rules_version != registry.rules_version
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q tests/unit/edits/test_catalogue.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.pipeline.rules.catalogue'`.

- [ ] **Step 3: Extend `services/domain/issues.py`**

Add `HUMAN_EDIT = "HUMAN_EDIT"` as the last member of `TransformationCode`. Add `check_id` as the last field of `DataQualityIssue`:

```python
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
    check_id: str | None = None
```

- [ ] **Step 4: Create `services/pipeline/rules/catalogue.py`**

```python
"""Named business-rule checks: stable ids for issues, waivers and citations."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from services.domain.edits import Citation
from services.domain.issues import IssueCode


class CheckKind(StrEnum):
    VOCABULARY = "vocabulary"
    FORMAT = "format"
    LIMIT = "limit"
    INVARIANT = "invariant"
    INTERPRETATION = "interpretation"


@dataclass(frozen=True, slots=True)
class CatalogueCheck:
    id: str
    kind: CheckKind
    fields: tuple[str, ...]
    issue_code: IssueCode | None
    label: str


REQUIRED_FIELDS = (
    "customer.customer_id",
    "customer.name",
    "customer.lifetime_spend",
    "customer.signup_date",
    "customer.status",
    "product.sku",
    "product.name",
    "product.category",
    "product.unit_price",
    "product.stock_qty",
    "product.status",
    "order.order_id",
    "order.customer_name_raw",
    "order.sku",
    "order.unit_price",
    "order.quantity",
    "order.ordered_at",
    "order.status",
)
_MONEY = ("customer.lifetime_spend", "product.unit_price", "order.unit_price")
_V, _F, _L, _I, _P = (
    CheckKind.VOCABULARY,
    CheckKind.FORMAT,
    CheckKind.LIMIT,
    CheckKind.INVARIANT,
    CheckKind.INTERPRETATION,
)

CATALOGUE: tuple[CatalogueCheck, ...] = (
    CatalogueCheck("vocab.product_category", _V, ("product.category",), IssueCode.INVALID_CATEGORY, "Category vocabulary"),
    CatalogueCheck("vocab.customer_status", _V, ("customer.status",), IssueCode.INVALID_STATUS, "Customer status vocabulary"),
    CatalogueCheck("vocab.product_status", _V, ("product.status",), IssueCode.INVALID_STATUS, "Product status vocabulary"),
    CatalogueCheck("vocab.order_status", _V, ("order.status",), IssueCode.INVALID_STATUS, "Order status vocabulary"),
    CatalogueCheck("vocab.customer_tags", _V, ("customer.tags",), IssueCode.UNKNOWN_TAG, "Customer tag vocabulary"),
    CatalogueCheck("vocab.product_tags", _V, ("product.tags",), IssueCode.UNKNOWN_TAG, "Product tag vocabulary"),
    CatalogueCheck("vocab.order_tags", _V, ("order.tags",), IssueCode.UNKNOWN_TAG, "Order tag vocabulary"),
    CatalogueCheck("format.customer_id", _F, ("customer.customer_id",), IssueCode.INVALID_IDENTIFIER, "Customer identifier format"),
    CatalogueCheck("format.sku", _F, ("product.sku", "order.sku"), IssueCode.INVALID_IDENTIFIER, "Product identifier format"),
    CatalogueCheck("format.order_id", _F, ("order.order_id",), IssueCode.INVALID_IDENTIFIER, "Order identifier format"),
    CatalogueCheck("format.email", _F, ("customer.email",), IssueCode.INVALID_EMAIL, "Email format"),
    CatalogueCheck("format.date_only", _F, ("customer.signup_date", "product.listed_date"), IssueCode.INVALID_DATE, "Date without a time"),
    CatalogueCheck("limit.required_field", _L, REQUIRED_FIELDS, IssueCode.MISSING_REQUIRED_VALUE, "Required field"),
    CatalogueCheck("limit.positive_amount", _L, ("product.unit_price", "order.unit_price"), IssueCode.INVALID_AMOUNT, "Price above zero"),
    CatalogueCheck("limit.nonnegative_amount", _L, ("customer.lifetime_spend",), IssueCode.INVALID_AMOUNT, "Lifetime spend not negative"),
    CatalogueCheck("limit.order_quantity_nonzero", _L, ("order.quantity",), IssueCode.INVALID_INTEGER, "Order quantity not zero"),
    CatalogueCheck("limit.customer_quantity_empty", _L, ("customer.quantity",), IssueCode.INVALID_STRUCTURE, "Customer quantity empty"),
    CatalogueCheck("invariant.refund", _I, ("order.quantity",), IssueCode.REFUND_CONFLICT, "Refund consistency"),
    CatalogueCheck("invariant.stock_status", _I, ("product.stock_qty",), IssueCode.STOCK_STATUS_CONFLICT, "Stock and status consistency"),
    CatalogueCheck("interpret.slash_date_mdy", _P, ("customer.signup_date", "product.listed_date", "order.ordered_at"), None, "Slash dates read month/day/year"),
    CatalogueCheck("interpret.single_comma_thousands", _P, _MONEY, None, "Single comma read as thousands"),
    CatalogueCheck("interpret.status_mapping", _P, ("customer.status", "product.status", "order.status"), None, "Status spelling mapping"),
    CatalogueCheck("interpret.sku_zero_padding", _P, ("product.sku", "order.sku"), None, "SKU zero-padding repair"),
    CatalogueCheck("interpret.line_total_repair", _P, ("order.unit_price",), None, "Line-total repair"),
    CatalogueCheck("interpret.fx_rate_date", _P, _MONEY, None, "FX conversion date"),
)
BY_ID = MappingProxyType({check.id: check for check in CATALOGUE})


class CitationError(ValueError):
    """A citation names an unknown check, a wrong field or a wrong value shape."""


def entity_checks(entity: str) -> tuple[CatalogueCheck, ...]:
    prefix = f"{entity}."
    return tuple(
        CatalogueCheck(c.id, c.kind, tuple(f for f in c.fields if f.startswith(prefix)), c.issue_code, c.label)
        for c in CATALOGUE
        if any(f.startswith(prefix) for f in c.fields)
    )


def validate_citation(entity: str, citation: Citation) -> None:
    check = BY_ID.get(citation.check_id)
    if check is None:
        raise CitationError(f"Unknown business rule {citation.check_id!r}.")
    if citation.field_path not in check.fields or not citation.field_path.startswith(
        f"{entity}."
    ):
        raise CitationError(f"{check.label} does not apply to {citation.field_path}.")
    value = citation.accepted_value
    if (check.kind is CheckKind.VOCABULARY) != (value is not None):
        raise CitationError(
            "Vocabulary rules need exactly one accepted value; other rules take none."
        )
    if value is not None and (not value or value != value.strip()):
        raise CitationError("The accepted value must be trimmed and nonempty.")
```

Run `ruff format services/pipeline/rules/catalogue.py` after writing; it will wrap the long `CATALOGUE` lines.

- [ ] **Step 5: Emit check ids from the rules**

In `services/pipeline/rules/invariants.py`, give `validation_issue` a required `check_id: str` parameter after `refs` and pass it into `DataQualityIssue(..., refs, check_id=check_id)`. In `refund_invariant` pass `"invariant.refund"`; in `stock_invariant` pass `"invariant.stock_status"`.

In `services/pipeline/rules/domains.py`, change `checks` to carry the id: `checks: list[tuple[str, IssueCode, str, str]] = []` and append four-tuples:

| Existing append | Fourth element |
|---|---|
| required-field `MISSING_REQUIRED_VALUE` | `"limit.required_field"` |
| identifier `INVALID_IDENTIFIER` | `_IDENTIFIER_CHECKS[path]` with `_IDENTIFIER_CHECKS = {"customer.customer_id": "format.customer_id", "product.sku": "format.sku", "order.sku": "format.sku", "order.order_id": "format.order_id"}` (module level) |
| positive amount | `"limit.positive_amount"` |
| non-negative amount | `"limit.nonnegative_amount"` |
| date-only `INVALID_DATE` | `"format.date_only"` |
| `INVALID_EMAIL` | `"format.email"` |
| `INVALID_CATEGORY` | `"vocab.product_category"` |
| order quantity non-zero | `"limit.order_quantity_nonzero"` |
| `INVALID_STATUS` | `f"vocab.{entity}_status"` |
| `UNKNOWN_TAG` | `f"vocab.{entity}_tags"` |

The customer-quantity `validation_issue(...)` call gets `"limit.customer_quantity_empty"`. The final loop becomes:

```python
        for path, code, summary, check_id in checks:
            effects.append(
                validation_issue(
                    graph, revision, code, path, summary, values[path].source_refs, check_id
                )
            )
```

- [ ] **Step 6: Hash the catalogue into the rules version**

In `services/pipeline/rules/registry.py`, import `from services.pipeline.rules.catalogue import CATALOGUE` and change the hashed content to:

```python
        content = json.dumps(
            {
                "rules": definitions,
                "normalizer": config,
                "catalogue": [
                    [c.id, c.kind.value, list(c.fields), c.issue_code] for c in CATALOGUE
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
```

`IssueCode` is a `StrEnum`, so `json.dumps` writes its value. `CATALOGUE` is read at construction time through the module global, which is what the monkeypatch test relies on.

- [ ] **Step 7: Persist `check_id`**

In `services/infrastructure/db/derived_repositories.py`: `_issue` passes `check_id=row.check_id` to `DataQualityIssue`; `add_issue` passes `check_id=issue.check_id` to `DataQualityIssueModel(...)`.

- [ ] **Step 8: Run the tests and fix expectations the new field changes**

Run: `.venv/bin/pytest -q tests/unit/edits/test_catalogue.py`
Expected: PASS.

Run: `.venv/bin/pytest -q tests/unit tests/integration`
Expected: PASS. If a test builds an expected `DataQualityIssue` for a domain or invariant rule and now fails only on `check_id`, add `check_id=` with the id from the Step 5 table. Change nothing else in those tests. The golden fixture compares against `default_registry().rules_version`, so the version change needs no edit.

Run: `.venv/bin/ruff check services tests && .venv/bin/pyright services`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add services/pipeline/rules services/domain/issues.py services/infrastructure/db/derived_repositories.py tests
git commit -m "feat: add business-rule check catalogue and stable check ids"
```

---

### Task 4: Decision rules for edited records

**Files:**
- Modify: `services/domain/decisions.py`
- Test: `tests/unit/review/test_edit_decisions.py`

**Interfaces:**
- Produces: `legal_outcomes(verdict: Verdict, edited: bool = False)`, `current_effective_state(history: Sequence[ReviewDecision], terminal_revision_id: UUID) -> EffectiveReviewState`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/review/test_edit_decisions.py
"""EDT-23 (domain): legal outcomes and effective state follow the current version."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from services.domain.decisions import (
    DecisionOutcome,
    EffectiveReviewState,
    ReviewDecision,
    current_effective_state,
    legal_outcomes,
)
from services.domain.issues import Verdict

APPROVE_REJECT = (DecisionOutcome.APPROVE, DecisionOutcome.REJECT)
ACK_REJECT = (DecisionOutcome.ACKNOWLEDGE, DecisionOutcome.REJECT)
OLD, NEW = UUID(int=1), UUID(int=2)


def decision(outcome: DecisionOutcome, revision: UUID) -> ReviewDecision:
    return ReviewDecision(
        uuid4(), UUID(int=9), revision, 1, outcome, "Alexis", "why", "k", None,
        datetime(2026, 9, 18, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("verdict", "unedited", "edited"),
    [
        (Verdict.CLEAN, (), APPROVE_REJECT),
        (Verdict.AUTO_REPAIRED, (), APPROVE_REJECT),
        (Verdict.NEEDS_REVIEW, APPROVE_REJECT, APPROVE_REJECT),
        (Verdict.DUPLICATE, ACK_REJECT, ACK_REJECT),
        (Verdict.REJECTED, ACK_REJECT, ACK_REJECT),
    ],
)
def test_legal_outcomes_for_edited_records(verdict, unedited, edited):
    assert legal_outcomes(verdict) == unedited
    assert legal_outcomes(verdict, edited=True) == edited


def test_effective_state_resets_when_the_version_changes():
    assert current_effective_state((), NEW) is EffectiveReviewState.PENDING
    rejected = (decision(DecisionOutcome.REJECT, OLD),)
    assert current_effective_state(rejected, OLD) is EffectiveReviewState.REJECTED
    assert current_effective_state(rejected, NEW) is EffectiveReviewState.PENDING
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest -q tests/unit/review/test_edit_decisions.py`
Expected: FAIL with `ImportError: cannot import name 'current_effective_state'`.

- [ ] **Step 3: Implement**

In `services/domain/decisions.py`, add `from collections.abc import Sequence` and replace `legal_outcomes`:

```python
def legal_outcomes(
    verdict: Verdict, edited: bool = False
) -> tuple[DecisionOutcome, ...]:
    """Manual decisions for governed verdicts; edited records always need one."""
    if verdict in (Verdict.REJECTED, Verdict.DUPLICATE):
        return (DecisionOutcome.ACKNOWLEDGE, DecisionOutcome.REJECT)
    if verdict is Verdict.NEEDS_REVIEW or edited:
        return (DecisionOutcome.APPROVE, DecisionOutcome.REJECT)
    return ()
```

Append after `ReviewDecision`:

```python
def current_effective_state(
    history: Sequence[ReviewDecision], terminal_revision_id: UUID
) -> EffectiveReviewState:
    """A decision only governs the version it was made on."""
    latest = history[-1] if history else None
    if latest is None or latest.candidate_revision_id != terminal_revision_id:
        return EffectiveReviewState.PENDING
    return latest.effective_state
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest -q tests/unit/review tests/unit/api && .venv/bin/pyright services/domain`
Expected: PASS, 0 errors.

- [ ] **Step 5: Commit**

```bash
git add services/domain/decisions.py tests/unit/review/test_edit_decisions.py
git commit -m "feat: make decisions follow the current record version"
```

---

### Task 5: Operator input parsing and edited revisions

**Files:**
- Create: `services/pipeline/edit_input.py`
- Test: `tests/unit/edits/test_edit_input.py`

**Interfaces:**
- Consumes: `FieldChange` (Task 1), `TransformationCode.HUMAN_EDIT` (Task 3).
- Produces: `EDITABLE_FIELDS: Mapping[str, tuple[str, ...]]` (keys `customer`, `product`, `order`), `FieldNotEditable(ValueError)` with `.field_path`, `ParsedInput(field_path, input_text, state, value, issues, annotation)`, `parse_input(field_path, input_text, *, accepted_statuses=frozenset()) -> ParsedInput`, `EditedRevision(revision, changes, issues, transformations)`, `build_edited_revision(parent, inputs, *, first_sequence, at) -> EditedRevision`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/edits/test_edit_input.py
"""EDT-02, EDT-03, EDT-25 (parsing) and the shape of an edited revision."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from services.domain.candidates import Money
from services.domain.fields import FieldState
from services.domain.issues import IssueCode, TransformationCode
from services.pipeline.edit_input import (
    EDITABLE_FIELDS,
    FieldNotEditable,
    build_edited_revision,
    parse_input,
)
from tests.unit.classify.test_rules import CUSTOMER, ORDER, graph_for

EDIT_NOW = datetime(2026, 9, 18, 13, 7, 31, 123456, tzinfo=UTC)


@pytest.mark.parametrize(
    ("path", "text", "state", "value"),
    [
        ("order.unit_price", "$1,240.50", FieldState.KNOWN, Money(Decimal("1240.50"), "USD")),
        ("order.ordered_at", "03/04/2023", FieldState.KNOWN, date(2023, 3, 4)),
        ("product.unit_price", "TBD", FieldState.DEFERRED, None),
        ("order.notes", None, FieldState.ABSENT, None),
        ("order.tags", "Express", FieldState.KNOWN, ("express",)),
    ],
)
def test_input_uses_csv_normalisers(path, text, state, value):
    parsed = parse_input(path, text)
    assert (parsed.state, parsed.value, parsed.issues) == (state, value, ())


def test_unparseable_input_is_unresolved_with_its_issue():
    parsed = parse_input("order.quantity", "many")
    assert parsed.state is FieldState.UNRESOLVED and parsed.value is None
    assert [code for code, _ in parsed.issues] == [IssueCode.INVALID_INTEGER]


@pytest.mark.parametrize(
    "path",
    ["order.customer_match_key", "order.unit_price_gbp", "order.entity_type", "order.nonexistent"],
)
def test_non_editable_fields_are_refused(path):
    with pytest.raises(FieldNotEditable) as error:
        parse_input(path, "x")
    assert error.value.field_path == path
    assert path not in EDITABLE_FIELDS["order"]


def test_status_vocabulary_citation_extends_the_status_parser():
    assert parse_input("product.status", "on_hold").state is FieldState.UNRESOLVED
    accepted = frozenset({"on_hold"})
    held = parse_input("product.status", " on_hold ", accepted_statuses=accepted)
    assert (held.state, held.value, held.issues) == (FieldState.KNOWN, "on_hold", ())
    other = parse_input("product.status", "paused", accepted_statuses=accepted)
    assert other.state is FieldState.UNRESOLVED


def test_edited_revision_appends_one_child_with_edit_evidence():
    parent = graph_for(ORDER).revisions[0]
    edited = build_edited_revision(
        parent,
        [parse_input("order.quantity", "3"), parse_input("order.notes", "Checked")],
        first_sequence=7,
        at=EDIT_NOW,
    )
    revision = edited.revision
    assert (revision.revision_number, revision.parent_revision_id, revision.origin) == (
        2,
        parent.id,
        "HUMAN_EDIT",
    )
    assert revision.payload.quantity.value == 3
    assert revision.payload.quantity.source_refs == parent.payload.quantity.source_refs
    assert revision.payload.unit_price_gbp.state is FieldState.ABSENT
    assert [(t.field_path, t.operation, t.sequence) for t in edited.transformations] == [
        ("order.notes", TransformationCode.HUMAN_EDIT, 7),
        ("order.quantity", TransformationCode.HUMAN_EDIT, 8),
    ]
    assert [c.field_path for c in edited.changes] == ["order.notes", "order.quantity"]
    assert edited.changes[1].before.value == 1 and edited.changes[1].after.value == 3


def test_unchanged_inputs_are_not_changes_and_issues_get_new_ids():
    parent = graph_for(ORDER).revisions[0]
    edited = build_edited_revision(
        parent,
        [parse_input("order.quantity", "1"), parse_input("order.sku", "SKU-2004")],
        first_sequence=1,
        at=EDIT_NOW,
    )
    assert edited.changes == () and edited.transformations == ()
    bad = build_edited_revision(
        parent, [parse_input("order.quantity", "many")], first_sequence=1, at=EDIT_NOW
    )
    (issue,) = bad.issues
    assert issue.candidate_revision_id == bad.revision.id
    assert bad.revision.payload.quantity.issue_refs == (issue.id,)


def test_name_edits_recompute_match_keys():
    parent = graph_for(CUSTOMER).revisions[0]
    edited = build_edited_revision(
        parent, [parse_input("customer.name", "  Wei Zhang  ")], first_sequence=1, at=EDIT_NOW
    )
    assert edited.revision.payload.name.value == "Wei Zhang"
    assert edited.revision.payload.name_match_key.value == "wei zhang"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest -q tests/unit/edits/test_edit_input.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/pipeline/edit_input.py`**

```python
"""Operator field input, parsed by the same normalisers as CSV cells."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from types import MappingProxyType
from typing import Any, cast

from services.domain.candidates import (
    CandidateRevision,
    EvidenceField,
    RejectedCandidateShell,
    candidate_revision_id,
)
from services.domain.edits import FieldChange
from services.domain.fields import CandidateField, FieldState
from services.domain.ids import deterministic_id
from services.domain.issues import (
    DataQualityIssue,
    IssueCode,
    TransformationCode,
    TransformationEvent,
)
from services.pipeline.normalise.dates import normalise_date
from services.pipeline.normalise.evidence import ISSUE_DEFINITIONS, FieldPath, NormalisedField
from services.pipeline.normalise.money import normalise_money
from services.pipeline.normalise.status import normalise_status
from services.pipeline.normalise.tags import normalise_tags
from services.pipeline.normalise.text import match_key, normalise_integer, normalise_text
from services.pipeline.rules.duplicates import same_typed_values

type Parser = Callable[[str, FieldPath], NormalisedField[Any]]
_MONEY = "money"
_PARSERS: Mapping[str, Parser | str] = MappingProxyType(
    {
        "customer.customer_id": normalise_text,
        "customer.name": normalise_text,
        "customer.email": normalise_text,
        "customer.lifetime_spend": _MONEY,
        "customer.signup_date": normalise_date,
        "customer.status": normalise_status,
        "customer.tags": normalise_tags,
        "customer.notes": normalise_text,
        "product.sku": normalise_text,
        "product.name": normalise_text,
        "product.category": normalise_text,
        "product.unit_price": _MONEY,
        "product.stock_qty": normalise_integer,
        "product.listed_date": normalise_date,
        "product.status": normalise_status,
        "product.tags": normalise_tags,
        "product.notes": normalise_text,
        "order.order_id": normalise_text,
        "order.customer_name_raw": normalise_text,
        "order.sku": normalise_text,
        "order.unit_price": _MONEY,
        "order.quantity": normalise_integer,
        "order.ordered_at": normalise_date,
        "order.status": normalise_status,
        "order.tags": normalise_tags,
        "order.notes": normalise_text,
    }
)
EDITABLE_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        entity: tuple(path for path in _PARSERS if path.startswith(f"{entity}."))
        for entity in ("customer", "product", "order")
    }
)
_ANNOTATIONS = {
    "customer.lifetime_spend": "lifetime_spend_annotation",
    "product.unit_price": "unit_price_annotation",
    "order.unit_price": "unit_price_annotation",
}
_MATCH_KEYS = {"customer.name": "name_match_key", "order.customer_name_raw": "customer_match_key"}
_GBP = {"customer": "lifetime_spend_gbp", "product": "unit_price_gbp", "order": "unit_price_gbp"}


class FieldNotEditable(ValueError):
    def __init__(self, field_path: str) -> None:
        super().__init__(f"{field_path} cannot be edited")
        self.field_path = field_path


@dataclass(frozen=True, slots=True)
class ParsedInput:
    field_path: str
    input_text: str | None
    state: FieldState
    value: object
    issues: tuple[tuple[IssueCode, str], ...] = ()
    annotation: str | None = None


def parse_input(
    field_path: str,
    input_text: str | None,
    *,
    accepted_statuses: frozenset[str] = frozenset(),
) -> ParsedInput:
    parser = _PARSERS.get(field_path)
    if parser is None:
        raise FieldNotEditable(field_path)
    if input_text is None:
        return ParsedInput(field_path, None, FieldState.ABSENT, None)
    if field_path.endswith(".status") and input_text.strip() in accepted_statuses:
        return ParsedInput(field_path, input_text, FieldState.KNOWN, input_text.strip())
    annotation: str | None = None
    if parser == _MONEY:
        money = normalise_money(input_text, FieldPath(field_path))
        field: NormalisedField[Any] = money.field
        annotation = money.annotation
    else:
        field = cast(Parser, parser)(input_text, FieldPath(field_path))
    return ParsedInput(
        field_path,
        input_text,
        field.state,
        field.value,
        tuple((issue.code, issue.summary) for issue in field.issues),
        annotation,
    )


@dataclass(frozen=True, slots=True)
class EditedRevision:
    revision: CandidateRevision
    changes: tuple[FieldChange, ...]
    issues: tuple[DataQualityIssue, ...]
    transformations: tuple[TransformationEvent, ...]


def _match_field(field: CandidateField[object]) -> CandidateField[object]:
    if field.state is FieldState.KNOWN and isinstance(field.value, str) and field.value:
        return CandidateField[object].known(match_key(field.value), source_refs=field.source_refs)
    return CandidateField[object](FieldState.ABSENT, None, field.source_refs)


def build_edited_revision(
    parent: CandidateRevision,
    inputs: Sequence[ParsedInput],
    *,
    first_sequence: int,
    at: datetime,
) -> EditedRevision:
    payload = parent.payload
    if isinstance(payload, RejectedCandidateShell):
        raise FieldNotEditable("record")
    number = parent.revision_number + 1
    revision_id = candidate_revision_id(parent.raw_record_id, number)
    updates: dict[str, object] = {}
    changes: list[FieldChange] = []
    issues: list[DataQualityIssue] = []
    events: list[TransformationEvent] = []
    sequence = first_sequence
    for parsed in sorted(inputs, key=lambda item: item.field_path):
        name = parsed.field_path.split(".", 1)[1]
        before = cast(CandidateField[object], getattr(payload, name))
        annotation_name = _ANNOTATIONS.get(parsed.field_path)
        current_annotation = getattr(payload, annotation_name) if annotation_name else None
        proposed = CandidateField[object](parsed.state, parsed.value, before.source_refs)
        if same_typed_values(proposed, before) and parsed.annotation == current_annotation:
            continue
        field_issues = tuple(
            DataQualityIssue(
                deterministic_id(revision_id, "human-edit-issue", parsed.field_path, code, ordinal),
                revision_id,
                code,
                ISSUE_DEFINITIONS[code].severity,
                parsed.field_path,
                summary,
                before.source_refs,
            )
            for ordinal, (code, summary) in enumerate(parsed.issues, start=1)
        )
        event_id = deterministic_id(revision_id, "human-edit", parsed.field_path)
        after = CandidateField[object](
            parsed.state,
            parsed.value,
            before.source_refs,
            (*before.transformation_refs, event_id),
            tuple(issue.id for issue in field_issues),
        )
        events.append(
            TransformationEvent(
                event_id,
                revision_id,
                TransformationCode.HUMAN_EDIT,
                parsed.field_path,
                cast(EvidenceField, before),
                cast(EvidenceField, after),
                sequence,
            )
        )
        sequence += 1
        updates[name] = after
        if annotation_name:
            updates[annotation_name] = parsed.annotation
        if parsed.field_path in _MATCH_KEYS:
            updates[_MATCH_KEYS[parsed.field_path]] = _match_field(after)
        changes.append(FieldChange(parsed.field_path, parsed.input_text, before, after))
        issues.extend(field_issues)
    # A conversion copied from the parent would describe superseded values.
    updates[_GBP[payload.entity_type.value]] = CandidateField[object](FieldState.ABSENT, None, ())
    revision = CandidateRevision(
        revision_id,
        parent.raw_record_id,
        number,
        parent.id,
        "HUMAN_EDIT",
        replace(payload, **updates),
        at,
    )
    return EditedRevision(revision, tuple(changes), tuple(issues), tuple(events))
```

- [ ] **Step 4: Run tests and type checks**

Run: `.venv/bin/pytest -q tests/unit/edits/test_edit_input.py && .venv/bin/pyright services/pipeline/edit_input.py`
Expected: PASS; 0 errors. If pyright rejects a `CandidateField[object]` assignment, add a `cast` at that site only; do not loosen types elsewhere.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/edit_input.py tests/unit/edits/test_edit_input.py
git commit -m "feat: parse operator input with CSV normalisers into edited revisions"
```

---

### Task 6: Rule engine isolation and human-set fields

**Files:**
- Modify: `services/pipeline/rules/base.py`, `services/pipeline/rules/registry.py`, `services/pipeline/rules/repairs.py`
- Test: `tests/unit/edits/test_rule_isolation.py`

**Interfaces:**
- Consumes: `build_edited_revision`, `parse_input` (Task 5).
- Produces: `apply_effects(graph, rule, *, only_raw_record_id: UUID | None = None)`, `RuleRegistry.apply(graph, *, only_raw_record_id: UUID | None = None)`, `human_set_fields(graph, revision) -> frozenset[str]`.
- Adds to `tests/unit/edits/support.py` (new): `EDIT_NOW`, `with_edit(csv, identifier, inputs, occurrence=0, accepted_statuses=frozenset())` returning `(summary, base_graph, parent, edited)`; later tasks reuse it.

- [ ] **Step 1: Write the unit-test support module**

```python
# tests/unit/edits/support.py
"""Pure graphs as persisted after processing, plus one appended edit."""

from dataclasses import replace
from datetime import UTC, datetime

from services.pipeline.classify import ClassificationSummary, classify_graph
from services.pipeline.edit_input import EditedRevision, build_edited_revision, parse_input
from services.pipeline.rules.base import RunCandidateGraph
from services.pipeline.rules.duplicates import business_key
from services.pipeline.rules.registry import default_registry
from services.domain.candidates import CandidateRevision
from tests.unit.classify.test_rules import graph_for

EDIT_NOW = datetime(2026, 9, 18, 13, 7, 31, 123456, tzinfo=UTC)


def persisted(csv: str) -> tuple[ClassificationSummary, RunCandidateGraph]:
    """Revisions, issues and transformations exactly as classification stores them."""
    summary = classify_graph(graph_for(csv), default_registry())
    done = summary.graph
    base = RunCandidateGraph(
        done.run_id,
        done.raw_records,
        done.revisions,
        done.fx_snapshot,
        EDIT_NOW,
        issues=done.issues,
        transformations=done.transformations,
    )
    return summary, base


def with_edit(
    csv: str,
    identifier: str,
    inputs: tuple[tuple[str, str | None], ...] = (),
    *,
    occurrence: int = 0,
    accepted_statuses: frozenset[str] = frozenset(),
) -> tuple[ClassificationSummary, RunCandidateGraph, CandidateRevision, EditedRevision]:
    summary, base = persisted(csv)
    matches = [
        r for r in base.terminal
        if (key := business_key(r.payload)) is not None and key[1] == identifier
    ]
    parent = matches[occurrence]
    edited = build_edited_revision(
        parent,
        [parse_input(p, t, accepted_statuses=accepted_statuses) for p, t in inputs],
        first_sequence=base.next_sequence(parent),
        at=EDIT_NOW,
    )
    graph = replace(
        base,
        revisions=(*base.revisions, edited.revision),
        issues=(*base.issues, *edited.issues),
        transformations=(*base.transformations, *edited.transformations),
    )
    return summary, graph, parent, edited
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/unit/edits/test_rule_isolation.py
"""EDT-15 and EDT-17 (graph level): isolation and human-set fields."""

from services.pipeline.rules.base import human_set_fields
from services.pipeline.rules.registry import default_registry
from tests.unit.classify.test_rules import CUSTOMER, PRODUCT
from tests.unit.edits.support import with_edit

SHIPPED = "ORDER,ORD-3001,Sofia Rossi,SKU-2004,19.99,1,2024-01-07,Shipped,,\n"
PADDED = "ORDER,ORD-3001,Sofia Rossi,SKU-00204,19.99,2,2024-01-07,Shipped,,\n"


def chain(graph, raw_id):
    return sorted(
        (r for r in graph.revisions if r.raw_record_id == raw_id),
        key=lambda r: r.revision_number,
    )


def test_human_set_sku_is_not_repaired():
    _, graph, parent, edited = with_edit(
        CUSTOMER + PRODUCT + SHIPPED, "ORD-3001", (("order.sku", "SKU-00204"),)
    )
    assert human_set_fields(graph, edited.revision) == frozenset({"order.sku"})
    after = default_registry().apply(graph, only_raw_record_id=parent.raw_record_id)
    origins = [r.origin for r in chain(after, parent.raw_record_id)]
    assert "SKU_ZERO_PADDING" not in origins[origins.index("HUMAN_EDIT"):]
    assert chain(after, parent.raw_record_id)[-1].payload.sku.value == "SKU-00204"


def test_repaired_value_carries_forward_when_not_edited():
    _, graph, parent, _ = with_edit(
        CUSTOMER + PRODUCT + PADDED, "ORD-3001", (("order.quantity", "3"),)
    )
    after = default_registry().apply(graph, only_raw_record_id=parent.raw_record_id)
    terminal = chain(after, parent.raw_record_id)[-1]
    assert terminal.payload.sku.value == "SKU-2004"
    assert terminal.payload.quantity.value == 3
    assert [r.origin for r in chain(after, parent.raw_record_id)] == [
        "normalise",
        "SKU_ZERO_PADDING",
        "FX_BINDING",
        "HUMAN_EDIT",
        "FX_BINDING",
    ]


def test_only_the_edited_record_receives_effects():
    _, graph, parent, _ = with_edit(
        CUSTOMER + PRODUCT + SHIPPED, "ORD-3001", (("order.quantity", "2"),)
    )
    after = default_registry().apply(graph, only_raw_record_id=parent.raw_record_id)
    others = {r.id for r in graph.raw_records} - {parent.raw_record_id}

    def owned(g):
        revision_ids = {r.id for r in g.revisions if r.raw_record_id in others}
        return (
            sorted(str(r.id) for r in g.revisions if r.raw_record_id in others),
            sorted(str(i.id) for i in g.issues if i.candidate_revision_id in revision_ids),
            sorted(str(t.id) for t in g.transformations if t.candidate_revision_id in revision_ids),
        )

    assert owned(after) == owned(graph)
    assert {d.source_revision_id for d in after.dependencies} <= {
        r.id for r in after.revisions if r.raw_record_id == parent.raw_record_id
    }
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/pytest -q tests/unit/edits/test_rule_isolation.py`
Expected: FAIL with `ImportError: cannot import name 'human_set_fields'`.

- [ ] **Step 4: Implement in `services/pipeline/rules/base.py`**

Add `TransformationCode` to the `services.domain.issues` import. Replace the start of `apply_effects` and add `human_set_fields`:

```python
def apply_effects(
    graph: RunCandidateGraph,
    rule: RuleDefinition,
    *,
    only_raw_record_id: UUID | None = None,
) -> RunCandidateGraph:
    effects = rule.apply(graph)
    terminal = {r.id: r for r in graph.terminal}
    if only_raw_record_id is not None:
        # Re-checks read every record as context but write effects for one.
        effects = tuple(
            effect
            for effect in effects
            if (target := terminal.get(effect.candidate_revision_id)) is not None
            and target.raw_record_id == only_raw_record_id
        )
    revisions: list[CandidateRevision] = []
    # ... the rest of the existing body is unchanged ...


def human_set_fields(
    graph: RunCandidateGraph, revision: CandidateRevision
) -> frozenset[str]:
    """Fields whose latest transformation in the record's chain is an operator edit."""
    chain = {r.id for r in graph.revisions if r.raw_record_id == revision.raw_record_id}
    latest: dict[str, TransformationEvent] = {}
    for event in graph.transformations:
        if event.candidate_revision_id not in chain:
            continue
        current = latest.get(event.field_path)
        if current is None or current.sequence < event.sequence:
            latest[event.field_path] = event
    return frozenset(
        path
        for path, event in latest.items()
        if event.operation is TransformationCode.HUMAN_EDIT
    )
```

In `services/pipeline/rules/registry.py`, import `UUID` and change `apply`:

```python
    def apply(
        self, graph: RunCandidateGraph, *, only_raw_record_id: UUID | None = None
    ) -> RunCandidateGraph:
        graph.check_barrier()
        graph = replace(graph, rules_version=self.rules_version)
        for rule in self.rules:
            graph = apply_effects(graph, rule, only_raw_record_id=only_raw_record_id)
        return graph
```

In `services/pipeline/rules/repairs.py`, import `human_set_fields` from `base`. In `repair_skus`, after the `isinstance` guard add:

```python
        if f"{payload.entity_type}.sku" in human_set_fields(graph, parent):
            continue
```

In `repair_line_totals`, after its `isinstance` guard add:

```python
        if "order.unit_price" in human_set_fields(graph, parent):
            continue
```

- [ ] **Step 5: Run the new tests and the existing rule suite**

Run: `.venv/bin/pytest -q tests/unit/edits tests/unit/classify tests/integration/test_messy_sample_data.py && .venv/bin/pyright services/pipeline`
Expected: PASS; 0 errors. Existing behaviour is unchanged because pipeline graphs contain no `HUMAN_EDIT` transformations.

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/rules tests/unit/edits/support.py tests/unit/edits/test_rule_isolation.py
git commit -m "feat: isolate rule effects to one record and protect human-set fields"
```

---

### Task 7: Pure re-check assessment

**Files:**
- Create: `services/pipeline/recheck.py`
- Test: `tests/unit/edits/test_recheck.py`

**Interfaces:**
- Consumes: `Citation`, `Dismissal`, `DismissalKind` (Task 1); `BY_ID`, `CheckKind` (Task 3); `RuleRegistry.apply(..., only_raw_record_id=)` (Task 6); `tests/unit/edits/support.with_edit` (Task 6).
- Produces: `Assessment(terminal, new_revisions, issues, transformations, classification, dependencies, reobservations, reasons, live_issues)`, `assess(graph, raw_record_id, first_new_revision, *, citations, dismissals, target_ready, domain_config=TerminalDomainConfig()) -> Assessment`, `symmetric_conflicts(graph, terminal, existing_refs)`, `waived(issue, payload, citations, config) -> bool`.

Rules this module implements (§7): live issues are those referenced by the terminal payload's fields plus those raised on the terminal revision; citations waive by check id + field (+ value for vocabularies); parse issues (`check_id is None`) never waive; dismissals drop the named duplicate, conflict or dependency match; the verdict is derived before dependency issues are added (as `classify_graph` does); other records' readiness comes from `target_ready`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/edits/test_recheck.py
"""EDT-10..13, 16, 18..22, 25, 26: the edited record's re-check."""

from decimal import Decimal

from services.domain.edits import Citation, Dismissal, DismissalKind
from services.domain.fields import FieldState
from services.domain.issues import (
    DependencyState,
    IssueCode,
    Readiness,
    TransformationCode,
    Verdict,
)
from services.pipeline.recheck import assess
from services.pipeline.rules.registry import default_registry
from tests.unit.classify.test_rules import CUSTOMER, PRODUCT
from tests.unit.edits.support import with_edit

ORDER_LINE = "ORDER,ORD-3001,{name},SKU-2004,{price},{quantity},2024-01-07,{status},,\n"
GARDEN = PRODUCT.replace("Electronics", "Garden")
PAUSED = CUSTOMER.replace(",active,", ",Paused,")
SECOND_CUSTOMER = "CUSTOMER,CUST-1002,Wei Zhang,w@example.org,50,,2024-02-01,active,,\n"


def order(name="Sofia Rossi", price="19.99", quantity="1", status="shipped"):
    return ORDER_LINE.format(name=name, price=price, quantity=quantity, status=status)


def run(csv, identifier, inputs=(), *, citations=(), dismissals=(), occurrence=0,
        accepted_statuses=frozenset(), ready=None):
    summary, graph, parent, edited = with_edit(
        csv, identifier, inputs, occurrence=occurrence, accepted_statuses=accepted_statuses
    )
    after = default_registry().apply(graph, only_raw_record_id=parent.raw_record_id)
    eligible = ready if ready is not None else {
        r.candidate_revision_id for r in summary.results if r.readiness is Readiness.ELIGIBLE
    }
    assessment = assess(
        after,
        parent.raw_record_id,
        edited.revision.revision_number,
        citations=tuple(citations),
        dismissals=tuple(dismissals),
        target_ready=lambda target: target in eligible,
    )
    return summary, parent, assessment


def codes(assessment):
    return sorted(i.code.value for i in assessment.live_issues if i.severity.value != "info")


def raw_id(summary, identifier, occurrence=0):
    from services.pipeline.rules.duplicates import business_key

    matches = [
        r.raw_record_id for r in summary.graph.terminal
        if (key := business_key(r.payload)) and key[1] == identifier
    ]
    return matches[occurrence]


def test_vocabulary_waiver_is_value_specific():
    accept = Citation("vocab.product_category", "product.category", "Garden")
    _, _, waived = run(CUSTOMER + GARDEN, "SKU-2004", citations=[accept])
    assert codes(waived) == [] and waived.classification.verdict is Verdict.CLEAN
    other = PRODUCT.replace("Electronics", "Gardening")
    _, _, kept = run(CUSTOMER + other, "SKU-2004", citations=[accept])
    assert codes(kept) == ["INVALID_CATEGORY"]


def test_tag_waiver_must_cover_every_unknown_tag():
    tagged = PRODUCT.replace("accessories", "gift|promo")
    gift = Citation("vocab.product_tags", "product.tags", "gift")
    promo = Citation("vocab.product_tags", "product.tags", "promo")
    _, _, partial = run(CUSTOMER + tagged, "SKU-2004", citations=[gift])
    assert codes(partial) == ["UNKNOWN_TAG"]
    _, _, full = run(CUSTOMER + tagged, "SKU-2004", citations=[gift, promo])
    assert codes(full) == []


def test_field_waiver_is_check_and_field_specific():
    csv = CUSTOMER + PRODUCT + order(quantity="0", status="refunded")
    nonzero = Citation("limit.order_quantity_nonzero", "order.quantity")
    refund = Citation("invariant.refund", "order.quantity")
    _, _, a = run(csv, "ORD-3001", citations=[nonzero])
    assert codes(a) == ["REFUND_CONFLICT"]
    _, _, b = run(csv, "ORD-3001", citations=[refund])
    assert codes(b) == ["INVALID_INTEGER"]


def test_parse_failures_are_never_waivable():
    csv = CUSTOMER + PRODUCT + order(quantity="many")
    citations = [
        Citation("limit.order_quantity_nonzero", "order.quantity"),
        Citation("invariant.refund", "order.quantity"),
        Citation("limit.required_field", "order.quantity"),
    ]
    _, _, assessment = run(csv, "ORD-3001", citations=citations)
    assert codes(assessment) == ["INVALID_INTEGER"]
    assert assessment.classification.verdict is Verdict.NEEDS_REVIEW


def test_interpretation_citation_drops_nothing():
    csv = CUSTOMER + PRODUCT + order(quantity="many")
    cite = Citation("interpret.slash_date_mdy", "order.ordered_at")
    _, _, assessment = run(
        csv, "ORD-3001", (("order.ordered_at", "2023-04-03"),), citations=[cite]
    )
    assert codes(assessment) == ["INVALID_INTEGER"]
    assert str(assessment.terminal.payload.ordered_at.value) == "2023-04-03"


def test_fx_is_recomputed_from_edited_values():
    csv = CUSTOMER + PRODUCT + order(price="$19.99", status="Shipped")
    _, _, assessment = run(csv, "ORD-3001", (("order.unit_price", "$25.00"),))
    terminal = assessment.terminal
    assert terminal.origin == "FX_BINDING"
    assert terminal.payload.unit_price_gbp.value == Decimal("20.00")
    assert [t.operation for t in assessment.transformations if t.candidate_revision_id == terminal.id] == [
        TransformationCode.FX_CONVERTED_AT_ORDER_DATE
    ]


def test_duplicate_dismissal():
    csv = CUSTOMER + PRODUCT + PRODUCT
    earlier = raw_id(classify(csv), "SKU-2004", 0)
    _, _, dismissed = run(
        csv, "SKU-2004", occurrence=1,
        dismissals=[Dismissal(DismissalKind.DUPLICATE, earlier)],
    )
    assert dismissed.classification.verdict is Verdict.CLEAN
    assert dismissed.issues and all(
        i.code is not IssueCode.DUPLICATE for i in dismissed.live_issues
    )
    _, _, kept = run(
        CUSTOMER + PRODUCT + PRODUCT, "SKU-2004", (("product.name", "Widget 2"),), occurrence=1
    )
    assert kept.classification.verdict is Verdict.DUPLICATE


def classify(csv):
    from tests.unit.edits.support import persisted

    return persisted(csv)[0]


def test_conflict_dismissal_is_per_counterpart():
    csv = PRODUCT + PRODUCT.replace("Widget", "Gadget") + PRODUCT.replace("Widget", "Gizmo")
    summary = classify(csv)
    first, third = raw_id(summary, "SKU-2004", 0), raw_id(summary, "SKU-2004", 2)
    _, _, assessment = run(
        csv, "SKU-2004", occurrence=1,
        dismissals=[Dismissal(DismissalKind.CONFLICT, first, "same_run")],
    )
    conflicts = [r for r in assessment.reasons if r.conflict_refs]
    assert [r.conflict_refs for r in conflicts] == [(third,)]


def test_dependency_dismissal_can_only_block():
    csv = PAUSED + PRODUCT + order()
    summary = classify(csv)
    customer = raw_id(summary, "CUST-1001")
    _, _, assessment = run(
        csv, "ORD-3001",
        dismissals=[Dismissal(DismissalKind.DEPENDENCY_MATCH, customer, "customer")],
    )
    states = {d.kind.value: d.state for d in assessment.dependencies}
    assert states["customer"] is DependencyState.UNRESOLVED
    assert assessment.classification.readiness is Readiness.BLOCKED_BY_DEPENDENCY
    assert "Customer reference 'Sofia Rossi' is unresolved." in [r.summary for r in assessment.reasons]


def test_repointing_by_reference_edit():
    csv = CUSTOMER + PRODUCT + order(name="S. Rossi")
    _, _, assessment = run(csv, "ORD-3001", (("order.customer_name_raw", "Sofia Rossi"),))
    assert assessment.terminal.payload.customer_match_key.value == "sofia rossi"
    assert {d.kind.value: d.state for d in assessment.dependencies}["customer"] is DependencyState.RESOLVED
    assert assessment.classification.verdict is Verdict.CLEAN
    assert assessment.classification.readiness is Readiness.ELIGIBLE


def test_context_readiness_comes_from_stored_assessments():
    csv = PAUSED + PRODUCT + order()
    _, _, assessment = run(csv, "ORD-3001", (("order.quantity", "2"),))
    assert assessment.classification.verdict is Verdict.CLEAN
    assert assessment.classification.readiness is Readiness.BLOCKED_BY_DEPENDENCY


def test_status_vocabulary_citation_accepts_new_status():
    cite = Citation("vocab.product_status", "product.status", "on_hold")
    _, _, plain = run(CUSTOMER + GARDEN, "SKU-2004", (("product.status", "on_hold"),))
    assert plain.terminal.payload.status.state is FieldState.UNRESOLVED
    assert "INVALID_STATUS" in codes(plain)
    _, _, held = run(
        CUSTOMER + GARDEN, "SKU-2004", (("product.status", "on_hold"),),
        citations=[cite], accepted_statuses=frozenset({"on_hold"}),
    )
    assert held.terminal.payload.status.value == "on_hold"
    assert codes(held) == ["INVALID_CATEGORY"]


def test_edit_detects_conflict_with_later_record():
    csv = PAUSED + SECOND_CUSTOMER
    summary = classify(csv)
    later = raw_id(summary, "CUST-1002")
    _, _, different = run(csv, "CUST-1001", (("customer.customer_id", "CUST-1002"),))
    assert [r.conflict_refs for r in different.reasons if r.conflict_refs] == [(later,)]
    same = (
        ("customer.customer_id", "CUST-1002"), ("customer.name", "Wei Zhang"),
        ("customer.email", "w@example.org"), ("customer.lifetime_spend", "50"),
        ("customer.signup_date", "2024-02-01"), ("customer.status", "active"),
        ("customer.tags", ""),
    )
    _, _, identical = run(csv, "CUST-1001", same)
    assert [r for r in identical.reasons if r.conflict_refs] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest -q tests/unit/edits/test_recheck.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.pipeline.recheck'`.

- [ ] **Step 3: Implement `services/pipeline/recheck.py`**

```python
"""Assess one edited record against its whole run, writing nothing."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from typing import cast
from uuid import UUID

from services.domain.candidates import (
    CandidatePayload,
    CandidateRevision,
    CustomerCandidate,
    OrderCandidate,
    RejectedCandidateShell,
)
from services.domain.edits import Citation, Dismissal, DismissalKind
from services.domain.fields import CandidateField, SourceRef
from services.domain.ids import deterministic_id
from services.domain.issues import (
    ClassificationResult,
    ComparisonScope,
    DataQualityIssue,
    DependencyKind,
    DependencyRecord,
    DependencyState,
    IssueCode,
    Readiness,
    ReviewReason,
    Severity,
    TransformationEvent,
    Verdict,
)
from services.domain.observations import Reobservation
from services.pipeline.rules.base import CandidateDependency, RunCandidateGraph
from services.pipeline.rules.catalogue import BY_ID, CheckKind
from services.pipeline.rules.domains import TerminalDomainConfig
from services.pipeline.rules.duplicates import business_key, same_typed_values

REPAIR_ORIGINS = frozenset(("SKU_ZERO_PADDING", "LINE_TOTAL_REPAIRED"))
_CONFLICT_SUMMARY = (
    "Business identity has different observed values; existing evidence is retained."
)


@dataclass(frozen=True, slots=True)
class Assessment:
    terminal: CandidateRevision
    new_revisions: tuple[CandidateRevision, ...]
    issues: tuple[DataQualityIssue, ...]
    transformations: tuple[TransformationEvent, ...]
    classification: ClassificationResult
    dependencies: tuple[DependencyRecord, ...]
    reobservations: tuple[Reobservation, ...]
    reasons: tuple[ReviewReason, ...]
    live_issues: tuple[DataQualityIssue, ...]


def _field_issue_refs(payload: CandidatePayload) -> frozenset[UUID]:
    if isinstance(payload, RejectedCandidateShell):
        return frozenset(payload.issue_refs)
    refs: set[UUID] = set()
    for item in fields(payload):
        value: object = getattr(payload, item.name)
        if isinstance(value, CandidateField):
            refs.update(cast(CandidateField[object], value).issue_refs)
    return frozenset(refs)


def waived(
    issue: DataQualityIssue,
    payload: CandidatePayload,
    citations: Sequence[Citation],
    config: TerminalDomainConfig,
) -> bool:
    if issue.check_id is None:
        return False
    matching = [
        c for c in citations if c.check_id == issue.check_id and c.field_path == issue.field_path
    ]
    if not matching:
        return False
    if BY_ID[issue.check_id].kind is not CheckKind.VOCABULARY:
        return True
    accepted = {c.accepted_value for c in matching}
    entity, name = issue.field_path.split(".", 1)
    value: object = cast(CandidateField[object], getattr(payload, name)).value
    if isinstance(value, tuple):
        vocabulary = {
            "customer": config.customer_tags,
            "product": config.product_tags,
            "order": config.order_tags,
        }[entity]
        return all(tag in accepted for tag in cast(tuple[str, ...], value) if tag not in vocabulary)
    return value in accepted


def _dismissed_comparison(
    issue: DataQualityIssue,
    refs: tuple[UUID, ...],
    dismissed: frozenset[tuple[DismissalKind, UUID]],
    raw_ids: frozenset[UUID],
) -> bool:
    if not refs:
        return False
    if issue.code is IssueCode.DUPLICATE:
        return (DismissalKind.DUPLICATE, refs[0]) in dismissed
    if issue.code is IssueCode.BUSINESS_KEY_CONFLICT:
        # Same-run conflicts name one raw record; earlier-run conflicts name
        # (identity, revision) pairs. Every named counterpart must be dismissed.
        counterparts = refs if refs[0] in raw_ids else refs[0::2]
        return all((DismissalKind.CONFLICT, c) in dismissed for c in counterparts)
    return False


def symmetric_conflicts(
    graph: RunCandidateGraph,
    terminal: CandidateRevision,
    existing_refs: Mapping[UUID, tuple[UUID, ...]],
) -> tuple[tuple[DataQualityIssue, tuple[UUID, ...]], ...]:
    """Conflicts with same-key records the comparison rule never reaches (§7.8)."""
    key = business_key(terminal.payload)
    if key is None:
        return ()
    named = {ref for refs in existing_refs.values() for ref in refs}
    snapshot = graph.fx_snapshot.id if graph.fx_snapshot else None
    found: list[tuple[DataQualityIssue, tuple[UUID, ...]]] = []
    for other in graph.terminal:
        if other.raw_record_id == terminal.raw_record_id or other.raw_record_id in named:
            continue
        if business_key(other.payload) != key or same_typed_values(
            other.payload, terminal.payload
        ):
            continue
        issue = DataQualityIssue(
            deterministic_id(
                terminal.id, graph.rules_version, snapshot, "symmetric-conflict", other.raw_record_id
            ),
            terminal.id,
            IssueCode.BUSINESS_KEY_CONFLICT,
            Severity.WARNING,
            f"{terminal.entity_type}.identity",
            _CONFLICT_SUMMARY,
            (SourceRef(terminal.raw_record_id),),
        )
        found.append((issue, (other.raw_record_id,)))
    return tuple(found)


def _reference_refs(terminal: CandidateRevision, kind: DependencyKind) -> tuple[SourceRef, ...]:
    payload = terminal.payload
    if isinstance(payload, OrderCandidate):
        return (
            payload.sku.source_refs
            if kind is DependencyKind.PRODUCT
            else payload.customer_name_raw.source_refs
        )
    if isinstance(payload, CustomerCandidate):
        return payload.notes.source_refs
    return (SourceRef(terminal.raw_record_id),)


def assess(
    graph: RunCandidateGraph,
    raw_record_id: UUID,
    first_new_revision: int,
    *,
    citations: Sequence[Citation],
    dismissals: Sequence[Dismissal],
    target_ready: Callable[[UUID], bool],
    domain_config: TerminalDomainConfig = TerminalDomainConfig(),
) -> Assessment:
    chain = tuple(
        sorted(
            (r for r in graph.revisions if r.raw_record_id == raw_record_id),
            key=lambda r: r.revision_number,
        )
    )
    terminal = chain[-1]
    new_revisions = tuple(r for r in chain if r.revision_number >= first_new_revision)
    new_ids = {r.id for r in new_revisions}
    snapshot_id = graph.fx_snapshot.id if graph.fx_snapshot else None
    raw_ids = frozenset(raw.id for raw in graph.raw_records)
    raw_of = {r.id: r.raw_record_id for r in graph.revisions}
    dismissed = frozenset((d.kind, d.counterpart_id) for d in dismissals)

    refs: dict[UUID, tuple[UUID, ...]] = {
        c.issue_id: c.conflict_refs
        for c in graph.comparisons
        if c.candidate_revision_id == terminal.id
    }
    symmetric = symmetric_conflicts(graph, terminal, refs)
    refs.update({issue.id: counterparts for issue, counterparts in symmetric})
    issues = (*graph.issues, *(issue for issue, _ in symmetric))
    referenced = _field_issue_refs(terminal.payload)
    live = tuple(
        issue
        for issue in issues
        if (issue.id in referenced or issue.candidate_revision_id == terminal.id)
        and not waived(issue, terminal.payload, citations, domain_config)
        and not _dismissed_comparison(issue, refs.get(issue.id, ()), dismissed, raw_ids)
    )
    reviewable = tuple(issue for issue in live if issue.severity is not Severity.INFO)
    duplicate = any(
        relation.later_raw_id == raw_record_id
        and relation.comparison_scope is ComparisonScope.SAME_RUN
        and (DismissalKind.DUPLICATE, relation.earlier_raw_id) not in dismissed
        for relation in graph.duplicates
    )
    if duplicate:
        verdict = Verdict.DUPLICATE
    elif isinstance(terminal.payload, RejectedCandidateShell):
        verdict = Verdict.REJECTED
    elif reviewable:
        verdict = Verdict.NEEDS_REVIEW
    elif any(r.origin in REPAIR_ORIGINS for r in chain):
        verdict = Verdict.AUTO_REPAIRED
    else:
        verdict = Verdict.CLEAN

    def dismiss(dependency: CandidateDependency) -> CandidateDependency:
        target = dependency.target_revision_id
        if target is not None and (
            DismissalKind.DEPENDENCY_MATCH, raw_of[target]
        ) in dismissed and any(
            d.kind is DismissalKind.DEPENDENCY_MATCH
            and d.counterpart_id == raw_of[target]
            and d.detail == dependency.kind.value
            for d in dismissals
        ):
            return replace(dependency, target_revision_id=None, state=DependencyState.UNRESOLVED)
        return dependency

    dependencies = tuple(
        dismiss(d) for d in graph.dependencies if d.source_revision_id == terminal.id
    )
    clean = verdict in (Verdict.CLEAN, Verdict.AUTO_REPAIRED)
    ready = clean and all(
        d.state is DependencyState.RESOLVED
        and d.target_revision_id is not None
        and target_ready(d.target_revision_id)
        for d in dependencies
    )
    reobservations = tuple(
        r for r in graph.reobservations if r.candidate_revision_id == terminal.id
    )
    if reobservations:
        readiness = Readiness.INELIGIBLE
    elif ready:
        readiness = Readiness.ELIGIBLE
    else:
        readiness = Readiness.BLOCKED_BY_DEPENDENCY if clean else Readiness.INELIGIBLE
    result = ClassificationResult(
        deterministic_id(terminal.id, "classification", graph.rules_version, snapshot_id),
        terminal.id,
        graph.rules_version,
        snapshot_id,
        verdict,
        readiness,
        graph.evaluated_at,
    )
    reasons = [
        ReviewReason(
            issue.id, issue.field_path, issue.summary, issue.source_refs,
            conflict_refs=refs.get(issue.id, ()),
        )
        for issue in reviewable
    ]
    records: list[DependencyRecord] = []
    dependency_issues: list[DataQualityIssue] = []
    for target in dependencies:
        state = target.state
        if state is DependencyState.RESOLVED and (
            target.target_revision_id is None or not target_ready(target.target_revision_id)
        ):
            state = DependencyState.BLOCKED
        record = DependencyRecord(
            deterministic_id(result.id, "dependency", target.kind, target.referenced_business_value),
            result.id,
            target.kind,
            target.referenced_business_value,
            None,
            state,
            target.target_revision_id,
        )
        records.append(record)
        if state is not DependencyState.RESOLVED:
            path = f"{terminal.entity_type}.{target.kind}"
            source_refs = _reference_refs(terminal, target.kind)
            issue = DataQualityIssue(
                deterministic_id(terminal.id, graph.rules_version, "dependency-issue", record.id),
                terminal.id,
                IssueCode.UNRESOLVED_REFERENCE,
                Severity.WARNING,
                path,
                f"{target.kind.value.title()} reference "
                f"{target.referenced_business_value!r} is {state.value}.",
                source_refs,
            )
            dependency_issues.append(issue)
            reasons.append(ReviewReason(issue.id, path, issue.summary, source_refs, (record.id,)))
    return Assessment(
        terminal,
        new_revisions,
        (*(i for i in issues if i.candidate_revision_id in new_ids), *dependency_issues),
        tuple(t for t in graph.transformations if t.candidate_revision_id in new_ids),
        result,
        tuple(records),
        reobservations,
        tuple(reasons),
        live,
    )
```

- [ ] **Step 4: Run tests and checks**

Run: `.venv/bin/pytest -q tests/unit/edits && .venv/bin/ruff check services/pipeline tests/unit/edits && .venv/bin/pyright services/pipeline`
Expected: PASS; clean. If an assertion fails, compare against §7 of the spec before changing code; change the test only if it contradicts the spec.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/recheck.py tests/unit/edits/test_recheck.py
git commit -m "feat: assess edited records with waivers, dismissals and run context"
```

---

### Task 8: Edit repository, ports and unit-of-work wiring

**Files:**
- Create: `services/application/edit_ports.py`, `services/infrastructure/db/edit_repository.py`
- Modify: `services/application/ports.py` (`Repositories.edits`, `UnitOfWork.edits`), `services/application/derived_ports.py` and `services/infrastructure/db/derived_repositories.py` (`CandidateRepository.chain`), `services/infrastructure/db/uow.py`, `services/infrastructure/runtime.py` (`_repositories`), `services/pipeline/classify.py` (write assessment 1), and the four test `Repositories(...)` constructors: `tests/integration/foundation/test_migrations.py:239`, `tests/integration/foundation/test_concurrent_ingest.py:99` and `:156`, `tests/integration/foundation/test_parse_recovery.py:50`
- Test: `tests/integration/edits/test_edit_repository.py`

**Interfaces:**
- Consumes: domain types (Task 1), models (Task 2), `build_edited_revision` (Task 5).
- Produces `EditRepository` protocol and `SqlAlchemyEditRepository` with: `lock_idempotency(key)`, `set_lock_timeout(milliseconds)`, `by_idempotency_key(key) -> HumanEdit | None`, `for_record(raw_record_id) -> tuple[HumanEdit, ...]` (by `edit_number`), `has_edits(raw_record_id) -> bool`, `add(edit)`, `add_assessment(assessment)`, `current_assessment(review_item_id) -> ReviewAssessment`, `assessment_for_edit(edit_id) -> ReviewAssessment`, `add_failure(failure)`, `latest_failure(review_item_id) -> EditAttemptFailure | None`, `run_for_review(review_item_id) -> UUID | None`. `CandidateRepository.chain(raw_record_id) -> tuple[CandidateRevision, ...]`. `uow.edits`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/edits/test_edit_repository.py
"""Edit persistence round-trips, append-only rules and first assessments."""

from dataclasses import replace
from uuid import uuid4

import pytest

from services.domain.edits import (
    Citation,
    EditReason,
    HumanEdit,
    ReviewAssessment,
    seal,
)
from services.pipeline.edit_input import build_edited_revision, parse_input
from tests.integration.edits.support import (
    CUSTOMER,
    EDIT_NOW,
    GARDEN_PRODUCT,
    processed,
    review_for,
    uow_for,
)
from tests.integration.foundation.test_concurrent_ingest import engine as engine


def test_classification_writes_first_assessment_for_every_review(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    with uow_for(engine) as uow:
        items = uow.reviews.for_run(run)
        assert items
        for item in items:
            assessment = uow.edits.current_assessment(item.id)
            assert (assessment.sequence, assessment.classification_id, assessment.human_edit_id) == (
                1, item.classification_id, None,
            )
            assert assessment.reasons == item.reasons


def test_edit_round_trips_and_is_append_only(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    edited = build_edited_revision(
        terminal, [parse_input("product.stock_qty", "7")], first_sequence=50, at=EDIT_NOW
    )
    edit = seal(
        HumanEdit(
            uuid4(), run, item.raw_record_id, item.id, 1, terminal.id, edited.revision.id,
            EditReason.BUSINESS_RULE_UPDATED, "Garden range approved", edited.changes,
            (Citation("vocab.product_category", "product.category", "Garden"),), (),
            "r" * 64, "Alexis", EDIT_NOW, "key-1", "f" * 64, None,
        ),
        edited.revision.payload,
    )
    with uow_for(engine) as uow:
        uow.candidates.add(edited.revision)
        uow.edits.add(edit)
        uow.commit()
    with uow_for(engine) as uow:
        assert uow.edits.for_record(item.raw_record_id) == (edit,)
        assert uow.edits.by_idempotency_key("key-1") == edit
        assert uow.edits.has_edits(item.raw_record_id)
        assert uow.edits.run_for_review(item.id) == run
        assert [r.revision_number for r in uow.candidates.chain(item.raw_record_id)][-1] == (
            edited.revision.revision_number
        )
        with pytest.raises(ValueError, match="append-only"):
            uow.edits.add(replace(edit, note="Changed"))


def test_assessments_append_and_the_latest_is_current(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, _ = review_for(engine, run, "SKU-2004")
    with uow_for(engine) as uow:
        first = uow.edits.current_assessment(item.id)
        with pytest.raises(ValueError, match="append-only"):
            uow.edits.add_assessment(replace(first, reasons=()))
        uow.edits.add_assessment(first)  # identical replay is accepted
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest -q tests/integration/edits/test_edit_repository.py`
Expected: FAIL with `AttributeError: ... has no attribute 'edits'`.

- [ ] **Step 3: Add the port**

```python
# services/application/edit_ports.py
"""Operator edits and review assessments belong to the caller's transaction."""

from typing import Protocol
from uuid import UUID

from services.domain.edits import EditAttemptFailure, HumanEdit, ReviewAssessment


class EditRepository(Protocol):
    def lock_idempotency(self, key: str) -> None: ...
    def set_lock_timeout(self, milliseconds: int) -> None: ...
    def by_idempotency_key(self, key: str) -> HumanEdit | None: ...
    def for_record(self, raw_record_id: UUID) -> tuple[HumanEdit, ...]: ...
    def has_edits(self, raw_record_id: UUID) -> bool: ...
    def add(self, edit: HumanEdit) -> None: ...
    def add_assessment(self, assessment: ReviewAssessment) -> None: ...
    def current_assessment(self, review_item_id: UUID) -> ReviewAssessment: ...
    def assessment_for_edit(self, edit_id: UUID) -> ReviewAssessment: ...
    def add_failure(self, failure: EditAttemptFailure) -> None: ...
    def latest_failure(self, review_item_id: UUID) -> EditAttemptFailure | None: ...
    def run_for_review(self, review_item_id: UUID) -> UUID | None: ...
```

In `services/application/ports.py`: import `EditRepository`; add `edits: EditRepository` as the **last** field of `Repositories`; add to `UnitOfWork`:

```python
    @property
    def edits(self) -> EditRepository: ...
```

In `services/application/derived_ports.py`, add to `CandidateRepository`:

```python
    def chain(self, raw_record_id: UUID) -> tuple[CandidateRevision, ...]: ...
```

- [ ] **Step 4: Implement the repository**

```python
# services/infrastructure/db/edit_repository.py
"""Append-only operator edits, review assessments and edit failure records."""

from datetime import UTC
from typing import cast
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from services.application.errors import ResourceNotFoundError
from services.domain.edits import (
    Citation,
    Dismissal,
    DismissalKind,
    EditAttemptFailure,
    EditReason,
    EditStage,
    FieldChange,
    HumanEdit,
    ReviewAssessment,
)
from services.domain.fields import CandidateField
from services.domain.issues import ReviewReason
from services.infrastructure.db.derived_codec import (
    array_json,
    encode,
    evidence_equal,
    read_as,
    read_tuple,
)
from services.infrastructure.db.models import (
    EditAttemptFailureModel,
    HumanEditModel,
    ReviewAssessmentModel,
    ReviewItemModel,
)


def _changes(value: list[object]) -> tuple[FieldChange, ...]:
    result: list[FieldChange] = []
    for item in value:
        data = cast(dict[str, object], item)
        result.append(
            FieldChange(
                str(data["field_path"]),
                cast(str | None, data["input_text"]),
                cast(CandidateField[object], read_as(data["before"], CandidateField)),
                cast(CandidateField[object], read_as(data["after"], CandidateField)),
            )
        )
    return tuple(result)


def _edit(row: HumanEditModel) -> HumanEdit:
    return HumanEdit(
        row.id,
        row.run_id,
        row.raw_record_id,
        row.review_item_id,
        row.edit_number,
        row.parent_revision_id,
        row.candidate_revision_id,
        EditReason(row.reason),
        row.note,
        _changes(row.field_changes),
        tuple(
            Citation(str(d["check_id"]), str(d["field_path"]), cast(str | None, d["accepted_value"]))
            for d in cast(list[dict[str, object]], row.citations)
        ),
        tuple(
            Dismissal(DismissalKind(str(d["kind"])), UUID(str(d["counterpart_id"])), cast(str | None, d["detail"]))
            for d in cast(list[dict[str, object]], row.dismissals)
        ),
        row.rules_version,
        row.operator_name,
        row.edited_at.astimezone(UTC),
        row.idempotency_key,
        row.request_fingerprint,
        row.parent_hash,
        row.content_hash,
    )


def _assessment(row: ReviewAssessmentModel) -> ReviewAssessment:
    return ReviewAssessment(
        row.review_item_id,
        row.sequence,
        row.classification_id,
        row.human_edit_id,
        read_tuple(row.reasons, ReviewReason),
        row.created_at.astimezone(UTC),
    )


def _failure(row: EditAttemptFailureModel) -> EditAttemptFailure:
    return EditAttemptFailure(
        row.id,
        row.review_item_id,
        row.run_id,
        cast(EditStage, row.stage),
        row.code,
        row.reference,
        row.error_type,
        row.operator_name,
        row.occurred_at.astimezone(UTC),
    )


class SqlAlchemyEditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_idempotency(self, key: str) -> None:
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "human-edit:" + key},
        )

    def set_lock_timeout(self, milliseconds: int) -> None:
        self._session.execute(text(f"SET LOCAL lock_timeout = {int(milliseconds)}"))

    def by_idempotency_key(self, key: str) -> HumanEdit | None:
        row = self._session.scalar(
            select(HumanEditModel).where(HumanEditModel.idempotency_key == key)
        )
        return _edit(row) if row else None

    def for_record(self, raw_record_id: UUID) -> tuple[HumanEdit, ...]:
        return tuple(
            _edit(row)
            for row in self._session.scalars(
                select(HumanEditModel)
                .where(HumanEditModel.raw_record_id == raw_record_id)
                .order_by(HumanEditModel.edit_number)
            )
        )

    def has_edits(self, raw_record_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(HumanEditModel.id)
                .where(HumanEditModel.raw_record_id == raw_record_id)
                .limit(1)
            )
            is not None
        )

    def add(self, edit: HumanEdit) -> None:
        if self._session.get(HumanEditModel, edit.id) is not None:
            raise ValueError("human edits are append-only")
        self._session.add(
            HumanEditModel(
                id=edit.id,
                run_id=edit.run_id,
                raw_record_id=edit.raw_record_id,
                review_item_id=edit.review_item_id,
                edit_number=edit.edit_number,
                parent_revision_id=edit.parent_revision_id,
                candidate_revision_id=edit.candidate_revision_id,
                reason=edit.reason.value,
                note=edit.note,
                field_changes=[
                    {
                        "field_path": c.field_path,
                        "input_text": c.input_text,
                        "before": encode(c.before),
                        "after": encode(c.after),
                    }
                    for c in edit.field_changes
                ],
                citations=[
                    {"check_id": c.check_id, "field_path": c.field_path, "accepted_value": c.accepted_value}
                    for c in edit.citations
                ],
                dismissals=[
                    {"kind": d.kind.value, "counterpart_id": str(d.counterpart_id), "detail": d.detail}
                    for d in edit.dismissals
                ],
                rules_version=edit.rules_version,
                operator_name=edit.operator_name,
                edited_at=edit.edited_at,
                idempotency_key=edit.idempotency_key,
                request_fingerprint=edit.request_fingerprint,
                content_hash=edit.content_hash,
                parent_hash=edit.parent_hash,
            )
        )
        self._session.flush()

    def add_assessment(self, assessment: ReviewAssessment) -> None:
        existing = self._session.get(
            ReviewAssessmentModel, (assessment.review_item_id, assessment.sequence)
        )
        if existing is not None:
            if not evidence_equal(_assessment(existing), assessment):
                raise ValueError("review assessments are append-only")
            return
        self._session.add(
            ReviewAssessmentModel(
                review_item_id=assessment.review_item_id,
                sequence=assessment.sequence,
                classification_id=assessment.classification_id,
                human_edit_id=assessment.human_edit_id,
                reasons=array_json(assessment.reasons),
                created_at=assessment.created_at,
            )
        )
        self._session.flush()

    def current_assessment(self, review_item_id: UUID) -> ReviewAssessment:
        row = self._session.scalar(
            select(ReviewAssessmentModel)
            .where(ReviewAssessmentModel.review_item_id == review_item_id)
            .order_by(ReviewAssessmentModel.sequence.desc())
            .limit(1)
        )
        if row is None:
            raise ResourceNotFoundError(f"Review has no assessment: {review_item_id}")
        return _assessment(row)

    def assessment_for_edit(self, edit_id: UUID) -> ReviewAssessment:
        row = self._session.scalar(
            select(ReviewAssessmentModel).where(ReviewAssessmentModel.human_edit_id == edit_id)
        )
        if row is None:
            raise ResourceNotFoundError(f"Edit has no assessment: {edit_id}")
        return _assessment(row)

    def add_failure(self, failure: EditAttemptFailure) -> None:
        self._session.add(
            EditAttemptFailureModel(
                id=failure.id,
                review_item_id=failure.review_item_id,
                run_id=failure.run_id,
                stage=failure.stage,
                code=failure.code,
                reference=failure.reference,
                error_type=failure.error_type,
                operator_name=failure.operator_name,
                occurred_at=failure.occurred_at,
            )
        )
        self._session.flush()

    def latest_failure(self, review_item_id: UUID) -> EditAttemptFailure | None:
        row = self._session.scalar(
            select(EditAttemptFailureModel)
            .where(EditAttemptFailureModel.review_item_id == review_item_id)
            .order_by(EditAttemptFailureModel.occurred_at.desc(), EditAttemptFailureModel.id)
            .limit(1)
        )
        return _failure(row) if row else None

    def run_for_review(self, review_item_id: UUID) -> UUID | None:
        return self._session.scalar(
            select(ReviewItemModel.run_id).where(ReviewItemModel.id == review_item_id)
        )
```

In `SqlAlchemyCandidateRepository` add:

```python
    def chain(self, raw_record_id: UUID) -> tuple[CandidateRevision, ...]:
        return tuple(
            _candidate(row)
            for row in self._session.scalars(
                select(CandidateRevisionModel)
                .where(CandidateRevisionModel.raw_record_id == raw_record_id)
                .order_by(CandidateRevisionModel.revision_number)
            )
        )
```

- [ ] **Step 5: Wire the unit of work, runtime and test constructors**

In `services/infrastructure/db/uow.py`, import `EditRepository` and add:

```python
    @property
    def edits(self) -> EditRepository:
        return self.repositories.edits
```

In `services/infrastructure/runtime.py`, import `SqlAlchemyEditRepository` and append `SqlAlchemyEditRepository(session),` as the last argument of `Repositories(...)` in `_repositories`. Do the same in each test `Repositories(...)` constructor listed under **Files** (use `SqlAlchemyEditRepository(session)`, or `cast(EditRepository, Mock(spec=EditRepository))` where that constructor already mocks other repositories).

- [ ] **Step 6: Classification writes the first assessment**

In `services/pipeline/classify.py`, import `ReviewAssessment` from `services.domain.edits` and replace the review loop in `classify_run`:

```python
            for review in summary.reviews:
                uow.reviews.add(review)
                uow.edits.add_assessment(
                    ReviewAssessment(
                        review.id, 1, review.classification_id, None, review.reasons,
                        review.created_at,
                    )
                )
```

`add_assessment` is idempotent for identical rows, so classification retries remain safe.

- [ ] **Step 7: Run tests**

Run: `.venv/bin/pytest -q tests/integration/edits tests/integration/pipeline tests/integration/foundation tests/unit && .venv/bin/pyright services`
Expected: PASS; 0 errors.

- [ ] **Step 8: Commit**

```bash
git add services tests
git commit -m "feat: persist edits and review assessments through the unit of work"
```

---

### Task 9: Append-edit command

**Files:**
- Modify: `services/application/errors.py` (add `CodedError`, `DatabaseUnavailable`)
- Create: `services/application/edit_errors.py`, `services/application/edit_rules.py`, `services/application/edits.py`
- Modify: `tests/integration/edits/support.py` (add `edit(...)` helper)
- Test: `tests/integration/edits/test_append_edit.py`

**Interfaces:**
- Consumes: everything from Tasks 1–8.
- Produces:
  - `CodedError(message, **details)` with `.code`, `.status`, `.details`; `DatabaseUnavailable(reference)`.
  - Edit errors: `EditNotEditable(reason)`, `EditStale(current_candidate_revision_id, current_edit_number)`, `EditIdempotencyConflict()`, `EditNoChange()`, `EditReasonMismatchError(message, reason)`, `EditFieldNotEditable(field_path)`, `EditCitationInvalid(message, check_id, field_path)`, `EditDismissalInvalid(dismissal)`, `EditWorkerFailure(stage, reference)`.
  - `not_editable_reason(terminal, chain, decisions, canonicals) -> str | None`; `dismissible_findings(item, assessment, classification, current_edit, *, classifications, candidates, raw_ids) -> tuple[Dismissal, ...]`.
  - `AppendEdit(review_item_id, expected_candidate_revision_id, reason, note, fields, citations, dismissals, operator_name, idempotency_key)`, `EditOutcome(edit, terminal_revision_id, verdict, readiness, reasons, legal_outcomes, replayed)`, `request_fingerprint(command) -> str`, `append_edit(command, uow_factory, clock, *, error_kind=no_error_kind, failure_injector=None, lock_timeout_ms=None) -> EditOutcome`.

- [ ] **Step 1: Add the `edit` helper to `tests/integration/edits/support.py`**

Put the imports with the module's other imports (Ruff rejects mid-file imports) and the function at the end.

```python
from uuid import uuid4

from services.application.edits import AppendEdit, EditOutcome, append_edit
from services.domain.edits import Citation, Dismissal, EditReason, FieldInput


def edit(
    engine,
    item,
    expected,
    reason: EditReason = EditReason.DATA_WRONG,
    fields: tuple[tuple[str, str | None], ...] = (),
    *,
    citations: tuple[Citation, ...] = (),
    dismissals: tuple[Dismissal, ...] = (),
    key: str | None = None,
    note: str | None = None,
    **options,
) -> EditOutcome:
    expected_id = getattr(expected, "id", expected)
    return append_edit(
        AppendEdit(
            item.id,
            expected_id,
            reason,
            note,
            tuple(FieldInput(path, text) for path, text in fields),
            citations,
            dismissals,
            "Alexis",
            key or str(uuid4()),
        ),
        lambda: uow_for(engine),
        EditClock(),
        **options,
    )
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/integration/edits/test_append_edit.py
"""EDT-01, 03..05, 08, 09, 17, 31, 32, 35..37: appending edits against PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
from sqlalchemy import text

from services.application.decisions import DecideReview, decide_review
from services.application.edit_errors import (
    EditFieldNotEditable,
    EditIdempotencyConflict,
    EditNoChange,
    EditNotEditable,
    EditReasonMismatchError,
    EditStale,
)
from services.application.ingest import (
    IngestFile,
    ReprocessSource,
    ingest_file,
    reprocess_source,
)
from services.application.process import ProcessRun, process_run
from services.domain.decisions import DecisionOutcome
from services.domain.edits import Citation, EditReason
from services.domain.issues import TransformationCode, Verdict
from services.infrastructure.source_store import FilesystemSourceStore
from tests.integration.edits.support import (
    CUSTOMER,
    EDIT_NOW,
    GARDEN_PRODUCT,
    PADDED_ORDER,
    PRODUCT,
    UNLINKED_ORDER,
    count,
    edit,
    processed,
    review_for,
    uow_for,
)
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.unit.classify.test_rules import SNAPSHOT, FixedClock

EDIT_TABLES = (
    "human_edit", "review_assessment", "candidate_revision", "data_quality_issue",
    "transformation_event", "classification_result", "dependency_record",
)


def rows_outside(engine, raw_id):
    """Every derived row that does not belong to the given raw record."""
    queries = {
        "candidate_revision": "SELECT * FROM candidate_revision WHERE raw_record_id <> :raw",
        "data_quality_issue": """SELECT i.* FROM data_quality_issue i JOIN candidate_revision c
            ON c.id = i.candidate_revision_id WHERE c.raw_record_id <> :raw""",
        "transformation_event": """SELECT t.* FROM transformation_event t JOIN candidate_revision c
            ON c.id = t.candidate_revision_id WHERE c.raw_record_id <> :raw""",
        "classification_result": """SELECT r.* FROM classification_result r JOIN candidate_revision c
            ON c.id = r.candidate_revision_id WHERE c.raw_record_id <> :raw""",
        "review_assessment": """SELECT a.* FROM review_assessment a JOIN review_item i
            ON i.id = a.review_item_id WHERE i.raw_record_id <> :raw""",
        "review_item": "SELECT * FROM review_item WHERE raw_record_id <> :raw",
        "duplicate_relation": "SELECT * FROM duplicate_relation",
    }
    with engine.connect() as connection:
        return {
            name: sorted(map(tuple, connection.execute(text(sql), {"raw": raw_id}).all()), key=str)
            for name, sql in queries.items()
        }


def counts(engine):
    return {table: count(engine, table) for table in EDIT_TABLES}


def test_edit_appends_revisions_without_touching_history(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + PRODUCT + PADDED_ORDER)
    item, terminal = review_for(engine, run, "ORD-3001")
    with uow_for(engine) as uow:
        before = uow.candidates.chain(item.raw_record_id)
    assert [r.origin for r in before] == ["normalise", "SKU_ZERO_PADDING", "FX_BINDING"]
    outside = rows_outside(engine, item.raw_record_id)
    outcome = edit(engine, item, terminal, fields=(("order.quantity", "3"),), note="Two boxes")
    with uow_for(engine) as uow:
        after = uow.candidates.chain(item.raw_record_id)
        (stored,) = uow.edits.for_record(item.raw_record_id)
        edited_events = uow.candidates.transformations(after[3].id)
    assert after[:3] == before
    assert [(r.revision_number, r.origin, r.parent_revision_id) for r in after[3:]] == [
        (4, "HUMAN_EDIT", before[2].id), (5, "FX_BINDING", after[3].id),
    ]
    assert after[3].payload.quantity.value == 3
    assert [(e.operation, e.field_path, e.before.value, e.after.value) for e in edited_events] == [
        (TransformationCode.HUMAN_EDIT, "order.quantity", 2, 3)
    ]
    assert stored == outcome.edit
    assert (stored.edit_number, stored.parent_revision_id, stored.candidate_revision_id) == (
        1, before[2].id, after[3].id,
    )
    assert (stored.run_id, stored.edited_at, stored.parent_hash, len(stored.content_hash)) == (
        run, EDIT_NOW, None, 64,
    )
    assert outcome.terminal_revision_id == after[4].id
    assert outcome.verdict is Verdict.NEEDS_REVIEW  # "Shipped" is still unresolved
    assert rows_outside(engine, item.raw_record_id) == outside
    with uow_for(engine) as uow:
        assessment = uow.edits.current_assessment(item.id)
    assert (assessment.sequence, assessment.human_edit_id) == (2, stored.id)


@pytest.mark.parametrize(
    ("reason", "fields", "citations", "error"),
    [
        (EditReason.DATA_WRONG, (("order.customer_match_key", "x"),), (), EditFieldNotEditable),
        (EditReason.DATA_WRONG, (), (), EditReasonMismatchError),
        (EditReason.BUSINESS_RULE_UPDATED, (("order.quantity", "3"),), (), EditReasonMismatchError),
        (EditReason.DATA_WRONG, (("order.quantity", "2"),), (), EditNoChange),
    ],
)
def test_invalid_edits_write_nothing(engine, tmp_path, reason, fields, citations, error):
    run = processed(engine, tmp_path, CUSTOMER + PRODUCT + PADDED_ORDER)
    item, terminal = review_for(engine, run, "ORD-3001")
    before = counts(engine)
    with pytest.raises(error):
        edit(engine, item, terminal, reason, fields, citations=citations)
    assert counts(engine) == before


def test_citation_only_edit_is_accepted(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    outcome = edit(
        engine, item, terminal, EditReason.BUSINESS_RULE_UPDATED,
        citations=(Citation("vocab.product_category", "product.category", "Garden"),),
    )
    assert outcome.edit.field_changes == ()
    assert outcome.verdict is Verdict.CLEAN


def test_reappend_overrides_without_overwriting(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + PRODUCT + UNLINKED_ORDER)
    item, terminal = review_for(engine, run, "ORD-3001")
    first = edit(engine, item, terminal, fields=(("order.customer_name_raw", "Sofia Rosi"),))
    with pytest.raises(EditStale) as caught:
        edit(engine, item, terminal, fields=(("order.customer_name_raw", "Sofia Rossi"),))
    assert caught.value.details["current_edit_number"] == 1
    second = edit(
        engine, item, first.terminal_revision_id, fields=(("order.customer_name_raw", "Sofia Rossi"),)
    )
    with uow_for(engine) as uow:
        chain = uow.candidates.chain(item.raw_record_id)
        edits = uow.edits.for_record(item.raw_record_id)
    assert [r.origin for r in chain] == [
        "normalise", "FX_BINDING", "HUMAN_EDIT", "FX_BINDING", "HUMAN_EDIT", "FX_BINDING",
    ]
    assert edits == (first.edit, second.edit)
    assert second.edit.parent_hash == first.edit.content_hash
    assert chain[-1].payload.customer_name_raw.value == "Sofia Rossi"
    assert second.verdict is Verdict.CLEAN


def test_override_replaces_waivers(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    waived = edit(
        engine, item, terminal, EditReason.BUSINESS_RULE_UPDATED,
        citations=(Citation("vocab.product_category", "product.category", "Garden"),),
    )
    assert waived.verdict is Verdict.CLEAN
    corrected = edit(engine, item, waived.terminal_revision_id, fields=(("product.stock_qty", "7"),))
    assert corrected.verdict is Verdict.NEEDS_REVIEW
    assert any("Garden" in r.summary for r in corrected.reasons)


def test_approved_record_is_not_editable_until_reversed(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    approved = decide_review(
        DecideReview(item.id, terminal.id, 0, DecisionOutcome.APPROVE, "Alexis", None, "a1"),
        uow_for(engine), FixedClock(),
    )
    with pytest.raises(EditNotEditable) as caught:
        edit(engine, item, terminal, fields=(("product.stock_qty", "7"),))
    assert caught.value.details["reason"] == "approved"
    decide_review(
        DecideReview(
            item.id, terminal.id, 1, DecisionOutcome.REJECT, "Alexis", "Wrong stock", "r1",
            approved.decision.id,
        ),
        uow_for(engine), FixedClock(),
    )
    outcome = edit(engine, item, terminal, fields=(("product.stock_qty", "7"),))
    assert outcome.edit.edit_number == 1


def test_idempotent_replay(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    first = edit(engine, item, terminal, fields=(("product.stock_qty", "7"),), key="same")
    before = counts(engine)
    replay = edit(engine, item, terminal, fields=(("product.stock_qty", "7"),), key="same")
    assert replay.replayed and not first.replayed
    assert (replay.edit.id, replay.edit.content_hash) == (first.edit.id, first.edit.content_hash)
    assert counts(engine) == before
    with pytest.raises(EditIdempotencyConflict):
        edit(engine, item, terminal, fields=(("product.stock_qty", "7"),), key="same", note="Other")
    assert counts(engine) == before


def test_concurrent_edits_serialise(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")

    def attempt(value):
        try:
            return edit(engine, item, terminal, fields=(("product.stock_qty", value),))
        except EditStale as error:
            return error

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, ["7", "8"]))
    assert sorted(type(r).__name__ for r in results) == ["EditOutcome", "EditStale"]
    assert count(engine, "human_edit") == 1


def test_edit_racing_approval_has_one_winner(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")

    def approve():
        try:
            return decide_review(
                DecideReview(item.id, terminal.id, 0, DecisionOutcome.APPROVE, "Sam", None, "race"),
                uow_for(engine), FixedClock(),
            )
        except Exception as error:  # the loser raises a stale/illegal decision
            return error

    def change():
        try:
            return edit(engine, item, terminal, fields=(("product.stock_qty", "7"),))
        except EditNotEditable as error:
            return error

    with ThreadPoolExecutor(2) as pool:
        results = [f.result() for f in (pool.submit(approve), pool.submit(change))]
    assert sum(not isinstance(r, Exception) for r in results) == 1


def test_reprocess_does_not_inherit_edits_but_duplicate_upload_sees_them(engine, tmp_path):
    csv = (CUSTOMER + GARDEN_PRODUCT).encode()
    store = FilesystemSourceStore(tmp_path)
    first = ingest_file(IngestFile(BytesIO(csv), "a.csv", "a", "a"), uow_for(engine), store, FixedClock())
    with uow_for(engine) as uow:
        uow.fx.add(SNAPSHOT)
        uow.commit()
    process_run(ProcessRun(first.run_id), lambda: uow_for(engine), store, FixedClock())
    item, terminal = review_for(engine, first.run_id, "SKU-2004")
    edit(engine, item, terminal, fields=(("product.stock_qty", "7"),))
    again = ingest_file(IngestFile(BytesIO(csv), "b.csv", "b", "b"), uow_for(engine), store, FixedClock())
    assert again.run_id == first.run_id and again.duplicate_upload
    with uow_for(engine) as uow:
        assert len(uow.edits.for_record(item.raw_record_id)) == 1
    successor = reprocess_source(
        ReprocessSource(first.source_file_id, first.source_occurrence_id), uow_for(engine), FixedClock()
    )
    process_run(ProcessRun(successor.run_id), lambda: uow_for(engine), store, FixedClock())
    with uow_for(engine) as uow:
        for review in uow.reviews.for_run(successor.run_id):
            assert not uow.edits.has_edits(review.raw_record_id)
            assert uow.edits.current_assessment(review.id).human_edit_id is None
            assert all(r.origin != "HUMAN_EDIT" for r in uow.candidates.chain(review.raw_record_id))
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/pytest -q tests/integration/edits/test_append_edit.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.application.edits'`.

- [ ] **Step 4: Add coded errors**

Add `from uuid import UUID` to the top of `services/application/errors.py`, then append:

```python
class CodedError(Exception):
    """A failure with a stable machine code, HTTP status and safe details."""

    code: str = "internal_error"
    status: int = 500

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.details: dict[str, object] = details


class DatabaseUnavailable(CodedError):
    code = "database_unavailable"
    status = 503

    def __init__(self, reference: UUID) -> None:
        super().__init__(
            "The database is unavailable. Nothing was changed; try again shortly.",
            retryable=True,
            reference=reference,
        )
```

```python
# services/application/edit_errors.py
"""Edit failures with stable codes; messages are safe to show operators."""

from uuid import UUID

from services.application.errors import CodedError
from services.domain.edits import Dismissal, EditStage

NOT_EDITABLE = {
    "approved": "Approved records must be reversed before editing.",
    "acknowledged": "Acknowledged records must be rejected before editing.",
    "typeless": "Records without a recognised type cannot be edited.",
    "promoted": "This record is already current canonical data and cannot be edited here.",
}
WORKER_CODES: dict[EditStage, str] = {
    "lock": "edit_lock_timeout",
    "parse": "edit_parse_failed",
    "recheck": "edit_recheck_failed",
    "persist": "edit_persist_failed",
}
WORKER_TEXT: dict[EditStage, str] = {
    "lock": "The record is busy. Nothing was changed; try again.",
    "parse": "Your values could not be read on the server. Nothing was changed.",
    "recheck": "The re-check failed on the server. Nothing was changed.",
    "persist": "The edit could not be saved. Nothing was changed.",
}


class EditError(CodedError):
    status = 422


class EditNotEditable(EditError):
    code, status = "edit_not_editable", 409

    def __init__(self, reason: str) -> None:
        super().__init__(NOT_EDITABLE[reason], reason=reason)


class EditStale(EditError):
    code, status = "edit_stale", 409

    def __init__(self, current_candidate_revision_id: UUID, current_edit_number: int) -> None:
        super().__init__(
            "This record changed since you opened it. Review the current values and append again.",
            current_candidate_revision_id=current_candidate_revision_id,
            current_edit_number=current_edit_number,
        )


class EditIdempotencyConflict(EditError):
    code, status = "edit_idempotency_conflict", 409

    def __init__(self) -> None:
        super().__init__("This idempotency key was already used for a different edit.")


class EditNoChange(EditError):
    code = "edit_no_change"

    def __init__(self) -> None:
        super().__init__("Nothing changed: the values, rules and findings match the current version.")


class EditReasonMismatchError(EditError):
    code = "edit_reason_mismatch"

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message, reason=reason)


class EditFieldNotEditable(EditError):
    code = "edit_field_not_editable"

    def __init__(self, field_path: str) -> None:
        super().__init__(f"{field_path} cannot be edited.", field_path=field_path)


class EditCitationInvalid(EditError):
    code = "edit_citation_invalid"

    def __init__(self, message: str, check_id: str, field_path: str) -> None:
        super().__init__(message, check_id=check_id, field_path=field_path)


class EditDismissalInvalid(EditError):
    code = "edit_dismissal_invalid"

    def __init__(self, dismissal: Dismissal) -> None:
        super().__init__(
            "That cross-record finding is not on the current version.",
            descriptor={
                "kind": dismissal.kind.value,
                "counterpart_id": dismissal.counterpart_id,
                "detail": dismissal.detail,
            },
        )


class EditWorkerFailure(EditError):
    def __init__(self, stage: EditStage, reference: UUID) -> None:
        super().__init__(WORKER_TEXT[stage], stage=stage, retryable=True, reference=reference)
        self.code = WORKER_CODES[stage]
        self.status = 503 if stage == "lock" else 500
```

- [ ] **Step 5: Add shared edit rules**

```python
# services/application/edit_rules.py
"""Editability and dismissible findings, shared by commands and read projections."""

from collections.abc import Sequence
from uuid import UUID

from services.application.canonical_ports import CanonicalRepository
from services.application.derived_ports import CandidateRepository, ClassificationRepository
from services.domain.candidates import CandidateRevision, RejectedCandidateShell
from services.domain.decisions import EffectiveReviewState, ReviewDecision, current_effective_state
from services.domain.edits import Dismissal, DismissalKind, HumanEdit, ReviewAssessment
from services.domain.issues import (
    ClassificationResult,
    ComparisonScope,
    DependencyState,
    ReviewItem,
)


def not_editable_reason(
    terminal: CandidateRevision,
    chain: Sequence[CandidateRevision],
    decisions: Sequence[ReviewDecision],
    canonicals: CanonicalRepository,
) -> str | None:
    if isinstance(terminal.payload, RejectedCandidateShell):
        return "typeless"
    state = current_effective_state(decisions, terminal.id)
    if state is EffectiveReviewState.APPROVED:
        return "approved"
    if state is EffectiveReviewState.ACKNOWLEDGED:
        return "acknowledged"
    for revision in chain:
        canonical = canonicals.for_candidate(revision.id)
        if canonical is not None and canonicals.current(canonical.identity_id) == canonical:
            return "promoted"
    return None


def dismissible_findings(
    item: ReviewItem,
    assessment: ReviewAssessment,
    classification: ClassificationResult,
    current_edit: HumanEdit | None,
    *,
    classifications: ClassificationRepository,
    candidates: CandidateRepository,
    raw_ids: frozenset[UUID],
) -> tuple[Dismissal, ...]:
    """Findings on the current version, plus those the current edit already dismissed."""
    found: dict[tuple[DismissalKind, UUID], Dismissal] = {}

    def add(dismissal: Dismissal) -> None:
        found.setdefault((dismissal.kind, dismissal.counterpart_id), dismissal)

    duplicates: set[UUID] = set()
    for relation in classifications.duplicates(item.raw_record_id):
        if relation.comparison_scope is ComparisonScope.SAME_RUN:
            duplicates.add(relation.earlier_raw_id)
            add(Dismissal(DismissalKind.DUPLICATE, relation.earlier_raw_id))
    for reason in assessment.reasons:
        refs = reason.conflict_refs
        if not refs or refs[0] in duplicates:
            continue
        if refs[0] in raw_ids:
            add(Dismissal(DismissalKind.CONFLICT, refs[0], "same_run"))
        else:
            for identity in refs[0::2]:
                add(Dismissal(DismissalKind.CONFLICT, identity, "earlier_run"))
    for dependency in classifications.dependencies(classification.id):
        target = dependency.target_candidate_revision_id
        if target is not None and dependency.state in (
            DependencyState.RESOLVED,
            DependencyState.BLOCKED,
        ):
            add(
                Dismissal(
                    DismissalKind.DEPENDENCY_MATCH,
                    candidates.get(target).raw_record_id,
                    dependency.kind.value,
                )
            )
    if current_edit is not None:
        for dismissal in current_edit.dismissals:
            add(dismissal)
    return tuple(found.values())
```

- [ ] **Step 6: Implement `services/application/edits.py`**

```python
"""Append-only operator edits: validate, parse, re-check and persist atomically."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from services.application.edit_errors import (
    EditCitationInvalid,
    EditDismissalInvalid,
    EditError,
    EditFieldNotEditable,
    EditIdempotencyConflict,
    EditNoChange,
    EditNotEditable,
    EditReasonMismatchError,
    EditStale,
    EditWorkerFailure,
    WORKER_CODES,
)
from services.application.edit_rules import dismissible_findings, not_editable_reason
from services.application.errors import (
    ApplicationValidationError,
    DatabaseUnavailable,
    ResourceNotFoundError,
)
from services.application.ports import Clock, UnitOfWork
from services.domain.decisions import DecisionOutcome, legal_outcomes
from services.domain.edits import (
    Citation,
    Dismissal,
    EditAttemptFailure,
    EditReason,
    EditReasonMismatch,
    EditStage,
    FieldChange,
    FieldInput,
    HumanEdit,
    ReviewAssessment,
    document_hash,
    seal,
    validate_reason,
)
from services.domain.ids import new_id
from services.domain.issues import Readiness, ReviewReason, Verdict
from services.pipeline.edit_input import (
    EDITABLE_FIELDS,
    EditedRevision,
    FieldNotEditable,
    build_edited_revision,
    parse_input,
)
from services.pipeline.recheck import Assessment, assess
from services.pipeline.rules.base import RunCandidateGraph
from services.pipeline.rules.catalogue import CitationError, validate_citation
from services.pipeline.rules.registry import default_registry

LOCK_TIMEOUT_MS = 5000
logger = logging.getLogger("services.edits")

type UnitOfWorkFactory = Callable[[], UnitOfWork]
type ErrorKind = Callable[[BaseException], Literal["lock", "unavailable"] | None]
type FailureInjector = Callable[[EditStage], None]


def no_error_kind(error: BaseException) -> None:
    return None


@dataclass(frozen=True, slots=True)
class AppendEdit:
    review_item_id: UUID
    expected_candidate_revision_id: UUID
    reason: EditReason
    note: str | None
    fields: tuple[FieldInput, ...]
    citations: tuple[Citation, ...]
    dismissals: tuple[Dismissal, ...]
    operator_name: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.operator_name.strip() or not self.idempotency_key.strip():
            raise ApplicationValidationError("operator and idempotency key must be nonempty")
        paths = [f.field_path for f in self.fields]
        if len(paths) != len(set(paths)):
            raise ApplicationValidationError("each field may be edited once per edit")


@dataclass(frozen=True, slots=True)
class EditOutcome:
    edit: HumanEdit
    terminal_revision_id: UUID
    verdict: Verdict
    readiness: Readiness
    reasons: tuple[ReviewReason, ...]
    legal_outcomes: tuple[DecisionOutcome, ...]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _Prepared:
    edit: HumanEdit
    edited: EditedRevision
    assessment: Assessment
    current: ReviewAssessment
    changes: tuple[FieldChange, ...]


def request_fingerprint(command: AppendEdit) -> str:
    return document_hash(
        {
            "review_item_id": command.review_item_id,
            "expected_candidate_revision_id": command.expected_candidate_revision_id,
            "reason": command.reason,
            "note": command.note,
            "fields": sorted(command.fields, key=lambda f: f.field_path),
            "citations": sorted(command.citations, key=lambda c: (c.check_id, c.field_path, c.accepted_value or "")),
            "dismissals": sorted(command.dismissals, key=lambda d: (d.kind, str(d.counterpart_id))),
            "operator_name": command.operator_name,
        }
    )


def _target_ready(uow: UnitOfWork, target: UUID) -> bool:
    canonical = uow.canonicals.for_candidate(target)
    if canonical is not None and uow.canonicals.current(canonical.identity_id) == canonical:
        return True
    return any(
        result.verdict in (Verdict.CLEAN, Verdict.AUTO_REPAIRED)
        and result.readiness is Readiness.ELIGIBLE
        for result in uow.classifications.for_revision(target)
    )


def _prepare(
    command: AppendEdit,
    uow: UnitOfWork,
    clock: Clock,
    enter: Callable[[EditStage], None],
    lock_timeout_ms: int,
) -> _Prepared:
    uow.edits.set_lock_timeout(lock_timeout_ms)
    uow.canonicals.lock_promotions()
    item = uow.reviews.get_locked(command.review_item_id)
    enter("parse")
    current = uow.edits.current_assessment(item.id)
    classification = uow.classifications.get(current.classification_id)
    chain = uow.candidates.chain(item.raw_record_id)
    terminal = chain[-1]
    edits = uow.edits.for_record(item.raw_record_id)
    previous = edits[-1] if edits else None
    reason = not_editable_reason(
        terminal, chain, uow.decisions.for_review(item.id), uow.canonicals
    )
    if reason is not None:
        raise EditNotEditable(reason)
    if command.expected_candidate_revision_id != terminal.id:
        raise EditStale(terminal.id, len(edits))
    try:
        validate_reason(command.reason, len(command.fields), command.citations, command.dismissals)
    except EditReasonMismatch as error:
        raise EditReasonMismatchError(str(error), command.reason.value) from error
    entity = terminal.entity_type
    assert entity is not None  # typeless records were refused above
    for field in command.fields:
        if field.field_path not in EDITABLE_FIELDS[entity.value]:
            raise EditFieldNotEditable(field.field_path)
    for citation in command.citations:
        try:
            validate_citation(entity.value, citation)
        except CitationError as error:
            raise EditCitationInvalid(str(error), citation.check_id, citation.field_path) from error
    raw_ids = frozenset(raw.id for raw in uow.raw_records.for_run(item.run_id))
    available = set(
        dismissible_findings(
            item, current, classification, previous,
            classifications=uow.classifications, candidates=uow.candidates, raw_ids=raw_ids,
        )
    )
    for dismissal in command.dismissals:
        if dismissal not in available:
            raise EditDismissalInvalid(dismissal)
    accepted = frozenset(
        c.accepted_value
        for c in command.citations
        if c.check_id == f"vocab.{entity.value}_status" and c.accepted_value
    )
    try:
        parsed = [
            parse_input(f.field_path, f.input_text, accepted_statuses=accepted)
            for f in command.fields
        ]
    except FieldNotEditable as error:
        raise EditFieldNotEditable(error.field_path) from error
    sequence = 1 + max(
        (e.sequence for r in chain for e in uow.candidates.transformations(r.id)), default=0
    )
    edited = build_edited_revision(terminal, parsed, first_sequence=sequence, at=clock.now())
    if not edited.changes and (
        command.citations == (previous.citations if previous else ())
        and command.dismissals == (previous.dismissals if previous else ())
    ):
        raise EditNoChange()
    registry = default_registry()
    draft = HumanEdit(
        new_id(), item.run_id, item.raw_record_id, item.id, len(edits) + 1, terminal.id,
        edited.revision.id, command.reason, command.note, edited.changes, command.citations,
        command.dismissals, registry.rules_version, command.operator_name, clock.now(),
        command.idempotency_key, request_fingerprint(command),
        previous.content_hash if previous else None,
    )
    edit = seal(draft, edited.revision.payload)
    enter("recheck")
    run = uow.runs.get(item.run_id)
    revisions = uow.candidates.for_run(run.id)
    graph = RunCandidateGraph(
        run.id,
        uow.raw_records.for_run(run.id),
        (*revisions, edited.revision),
        uow.fx.get(run.fx_snapshot_id) if run.fx_snapshot_id else None,
        clock.now(),
        issues=(*(i for r in revisions for i in uow.candidates.issues(r.id)), *edited.issues),
        transformations=(
            *(t for r in revisions for t in uow.candidates.transformations(r.id)),
            *edited.transformations,
        ),
        prior_observations=uow.canonicals.prior_observations(),
    )
    graph = registry.apply(graph, only_raw_record_id=item.raw_record_id)
    assessment = assess(
        graph,
        item.raw_record_id,
        edited.revision.revision_number,
        citations=command.citations,
        dismissals=command.dismissals,
        target_ready=lambda target: _target_ready(uow, target),
    )
    return _Prepared(edit, edited, assessment, current, edited.changes)


def _persist(prepared: _Prepared, uow: UnitOfWork, clock: Clock) -> None:
    assessment = prepared.assessment
    for revision in assessment.new_revisions:
        uow.candidates.add(revision)
    for event in assessment.transformations:
        uow.candidates.add_transformation(event)
    for issue in assessment.issues:
        uow.candidates.add_issue(issue)
    uow.edits.add(prepared.edit)
    uow.classifications.add(assessment.classification)
    for dependency in assessment.dependencies:
        uow.classifications.add_dependency(dependency)
    for link in assessment.reobservations:
        uow.canonicals.add_reobservation(link)
    uow.edits.add_assessment(
        ReviewAssessment(
            prepared.edit.review_item_id,
            prepared.current.sequence + 1,
            assessment.classification.id,
            prepared.edit.id,
            assessment.reasons,
            clock.now(),
        )
    )


def _outcome(edit: HumanEdit, assessment: Assessment, replayed: bool = False) -> EditOutcome:
    result = assessment.classification
    return EditOutcome(
        edit, assessment.terminal.id, result.verdict, result.readiness, assessment.reasons,
        legal_outcomes(result.verdict, edited=True), replayed,
    )


def _replay(command: AppendEdit, uow: UnitOfWork) -> EditOutcome | None:
    stored = uow.edits.by_idempotency_key(command.idempotency_key)
    if stored is None:
        return None
    if stored.request_fingerprint != request_fingerprint(command):
        raise EditIdempotencyConflict()
    current = uow.edits.assessment_for_edit(stored.id)
    result = uow.classifications.get(current.classification_id)
    return EditOutcome(
        stored, result.candidate_revision_id, result.verdict, result.readiness,
        current.reasons, legal_outcomes(result.verdict, edited=True), True,
    )


def append_edit(
    command: AppendEdit,
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    *,
    error_kind: ErrorKind = no_error_kind,
    failure_injector: FailureInjector | None = None,
    lock_timeout_ms: int | None = None,
) -> EditOutcome:
    reference = new_id()
    stage: EditStage = "lock"

    def enter(next_stage: EditStage) -> None:
        nonlocal stage
        stage = next_stage
        if failure_injector is not None:
            failure_injector(next_stage)

    try:
        with uow_factory() as uow:
            uow.edits.set_lock_timeout(lock_timeout_ms or LOCK_TIMEOUT_MS)
            uow.edits.lock_idempotency(command.idempotency_key)
            replayed = _replay(command, uow)
            if replayed is not None:
                return replayed
            prepared = _prepare(command, uow, clock, enter, lock_timeout_ms or LOCK_TIMEOUT_MS)
            enter("persist")
            _persist(prepared, uow, clock)
            uow.commit()
            return _outcome(prepared.edit, prepared.assessment)
    except (EditError, ResourceNotFoundError, ApplicationValidationError):
        raise
    except Exception as error:
        kind = error_kind(error)
        if kind == "unavailable":
            logger.error("database_unavailable reference=%s", reference, exc_info=error)
            raise DatabaseUnavailable(reference) from error
        failed: EditStage = "lock" if kind == "lock" else stage
        logger.error(
            "%s reference=%s stage=%s", WORKER_CODES[failed], reference, failed, exc_info=error
        )
        _record_failure(uow_factory, command, failed, reference, error, clock)
        raise EditWorkerFailure(failed, reference) from error


def _record_failure(
    uow_factory: UnitOfWorkFactory,
    command: AppendEdit,
    stage: EditStage,
    reference: UUID,
    error: BaseException,
    clock: Clock,
) -> None:
    try:
        with uow_factory() as uow:
            run_id = uow.edits.run_for_review(command.review_item_id)
            if run_id is None:
                return
            uow.edits.add_failure(
                EditAttemptFailure(
                    new_id(), command.review_item_id, run_id, stage, WORKER_CODES[stage],
                    reference, type(error).__name__, command.operator_name, clock.now(),
                )
            )
            uow.commit()
    except Exception:
        logger.exception("could not record edit failure reference=%s", reference)
```

`_prepare` calls `set_lock_timeout` again; the second call is harmless and keeps `_prepare` usable on its own by `preview_edit` (Task 10).

- [ ] **Step 7: Run tests and checks**

Run: `.venv/bin/pytest -q tests/integration/edits && .venv/bin/ruff check services tests && .venv/bin/pyright services`
Expected: PASS; clean.

- [ ] **Step 8: Commit**

```bash
git add services/application tests/integration/edits
git commit -m "feat: append run-scoped edits with re-check, hash chain and idempotency"
```

---

### Task 10: Worker failures, lock timeout and preview

**Files:**
- Create: `services/infrastructure/db/errors.py`
- Modify: `services/application/edits.py` (add `EditPreview`, `preview_edit`), `services/infrastructure/runtime.py` (`Runtime.database_error_kind`)
- Test: `tests/unit/edits/test_database_errors.py`, `tests/integration/edits/test_edit_failures.py`

**Interfaces:**
- Produces: `database_error_kind(error: BaseException) -> Literal["lock", "unavailable"] | None`; `Runtime.database_error_kind: ErrorKind` (default `no_error_kind`; `build_runtime` sets the PostgreSQL one); `EditPreview(changes, verdict, readiness, reasons)`; `preview_edit(command, uow_factory, clock, *, error_kind=no_error_kind) -> EditPreview`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/edits/test_database_errors.py
"""Lock timeouts and lost connections are told apart from other SQL errors."""

from sqlalchemy.exc import OperationalError

from services.infrastructure.db.errors import database_error_kind
from services.infrastructure.runtime import RuntimeConfigurationError


class Driver(Exception):
    def __init__(self, sqlstate):
        super().__init__("driver text")
        self.sqlstate = sqlstate


def wrapped(sqlstate):
    return OperationalError("SELECT 1", {}, Driver(sqlstate))


def test_classifies_database_errors():
    assert database_error_kind(wrapped("55P03")) == "lock"
    assert database_error_kind(wrapped(None)) == "unavailable"
    assert database_error_kind(wrapped("08006")) == "unavailable"
    assert database_error_kind(wrapped("40P01")) is None
    assert database_error_kind(ValueError("x")) is None


def test_follows_the_cause_chain():
    error = RuntimeConfigurationError("Database operation failed")
    error.__cause__ = wrapped(None)
    assert database_error_kind(error) == "unavailable"
```

```python
# tests/integration/edits/test_edit_failures.py
"""EDT-38, EDT-39 and the lock timeout: failed edits change nothing but the failure log."""

from uuid import UUID

import pytest
from sqlalchemy import text

from services.application.edit_errors import EditFieldNotEditable, EditWorkerFailure
from services.application.edits import AppendEdit, preview_edit
from services.application.errors import DatabaseUnavailable
from services.domain.edits import EditReason, FieldInput
from services.infrastructure.db.errors import database_error_kind
from tests.integration.edits.support import (
    CUSTOMER,
    GARDEN_PRODUCT,
    EditClock,
    count,
    edit,
    processed,
    review_for,
    uow_for,
)
from tests.integration.foundation.test_concurrent_ingest import engine as engine

TABLES = (
    "human_edit", "review_assessment", "candidate_revision", "data_quality_issue",
    "transformation_event", "classification_result", "dependency_record",
)


def counts(engine):
    return {table: count(engine, table) for table in TABLES}


def raise_at(target):
    def injector(stage):
        if stage == target:
            raise RuntimeError("injected")

    return injector


@pytest.mark.parametrize(
    ("stage", "code"),
    [("parse", "edit_parse_failed"), ("recheck", "edit_recheck_failed"), ("persist", "edit_persist_failed")],
)
def test_each_worker_failure_rolls_back_and_records(engine, tmp_path, stage, code):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    before = counts(engine)
    with pytest.raises(EditWorkerFailure) as caught:
        edit(engine, item, terminal, fields=(("product.stock_qty", "7"),), failure_injector=raise_at(stage))
    failure = caught.value
    assert (failure.code, failure.status, failure.details["stage"]) == (code, 500, stage)
    assert isinstance(failure.details["reference"], UUID)
    assert counts(engine) == before
    with uow_for(engine) as uow:
        recorded = uow.edits.latest_failure(item.id)
    assert recorded is not None
    assert (recorded.reference, recorded.stage, recorded.code, recorded.run_id) == (
        failure.details["reference"], stage, code, run,
    )
    assert count(engine, "edit_attempt_failure") == 1


def test_lock_timeout_is_reported_as_busy(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    with engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('canonical-promotions', 0))")
        )
        with pytest.raises(EditWorkerFailure) as caught:
            edit(
                engine, item, terminal, fields=(("product.stock_qty", "7"),),
                error_kind=database_error_kind, lock_timeout_ms=200,
            )
        transaction.rollback()
    assert (caught.value.code, caught.value.status) == ("edit_lock_timeout", 503)
    assert count(engine, "human_edit") == 0
    with uow_for(engine) as uow:
        assert uow.edits.latest_failure(item.id).stage == "lock"


def test_unavailable_database_is_not_recorded_as_an_edit_failure(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    with pytest.raises(DatabaseUnavailable) as caught:
        edit(
            engine, item, terminal, fields=(("product.stock_qty", "7"),),
            failure_injector=raise_at("recheck"), error_kind=lambda error: "unavailable",
        )
    assert caught.value.code == "database_unavailable" and caught.value.status == 503
    assert count(engine, "edit_attempt_failure") == 0


def test_preview_matches_append_and_writes_nothing(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    command = AppendEdit(
        item.id, terminal.id, EditReason.DATA_WRONG, None,
        (FieldInput("product.stock_qty", "0"),), (), (), "Alexis", "preview",
    )
    before = counts(engine) | {"edit_attempt_failure": count(engine, "edit_attempt_failure")}
    preview = preview_edit(command, lambda: uow_for(engine), EditClock())
    assert counts(engine) | {"edit_attempt_failure": count(engine, "edit_attempt_failure")} == before
    assert [c.after.value for c in preview.changes] == [0]
    appended = edit(engine, item, terminal, fields=(("product.stock_qty", "0"),))
    assert (preview.verdict, preview.readiness) == (appended.verdict, appended.readiness)
    assert [r.summary for r in preview.reasons] == [r.summary for r in appended.reasons]
    with pytest.raises(EditFieldNotEditable):
        preview_edit(
            AppendEdit(
                item.id, appended.terminal_revision_id, EditReason.DATA_WRONG, None,
                (FieldInput("product.unit_price_gbp", "1"),), (), (), "Alexis", "preview",
            ),
            lambda: uow_for(engine),
            EditClock(),
        )
    assert count(engine, "edit_attempt_failure") == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest -q tests/unit/edits/test_database_errors.py tests/integration/edits/test_edit_failures.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.infrastructure.db.errors'`.

- [ ] **Step 3: Classify database errors**

```python
# services/infrastructure/db/errors.py
"""Tell lock timeouts and lost connections apart from other database errors."""

from typing import Literal

from sqlalchemy.exc import OperationalError

LOCK_NOT_AVAILABLE = "55P03"


def database_error_kind(error: BaseException) -> Literal["lock", "unavailable"] | None:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, OperationalError):
            sqlstate = getattr(current.orig, "sqlstate", None)
            if sqlstate == LOCK_NOT_AVAILABLE:
                return "lock"
            # Connection failures carry no SQLSTATE or the 08 (connection) class.
            if sqlstate is None or str(sqlstate).startswith("08"):
                return "unavailable"
            return None
        current = current.__cause__
    return None
```

In `services/infrastructure/runtime.py`, import `ErrorKind` and `no_error_kind` from `services.application.edits` and `database_error_kind` from `services.infrastructure.db.errors`. Add `database_error_kind: ErrorKind = no_error_kind` as the last field of `Runtime`, and pass `database_error_kind=database_error_kind` in `build_runtime`'s `Runtime(...)`.

- [ ] **Step 4: Add preview to `services/application/edits.py`**

```python
@dataclass(frozen=True, slots=True)
class EditPreview:
    changes: tuple[FieldChange, ...]
    verdict: Verdict
    readiness: Readiness
    reasons: tuple[ReviewReason, ...]


def preview_edit(
    command: AppendEdit,
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    *,
    error_kind: ErrorKind = no_error_kind,
) -> EditPreview:
    """Run every append step except persistence, then roll back."""
    reference = new_id()
    try:
        with uow_factory() as uow:
            prepared = _prepare(command, uow, clock, lambda stage: None, LOCK_TIMEOUT_MS)
            uow.rollback()
    except (EditError, ResourceNotFoundError, ApplicationValidationError):
        raise
    except Exception as error:
        kind = error_kind(error)
        if kind == "unavailable":
            raise DatabaseUnavailable(reference) from error
        logger.error("edit preview failed reference=%s", reference, exc_info=error)
        raise EditWorkerFailure("lock" if kind == "lock" else "recheck", reference) from error
    result = prepared.assessment.classification
    return EditPreview(
        prepared.changes, result.verdict, result.readiness, prepared.assessment.reasons
    )
```

Preview never records an `edit_attempt_failure`: nothing was attempted from the operator's point of view.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest -q tests/unit/edits tests/integration/edits && .venv/bin/pyright services`
Expected: PASS; 0 errors. The lock test takes about 200 ms.

- [ ] **Step 6: Commit**

```bash
git add services tests
git commit -m "feat: report edit worker failures with codes and add rolled-back preview"
```

---

### Task 11: Decisions and cascade with assessments

**Files:**
- Modify: `services/application/decisions.py`, `services/application/dependency_readiness.py`
- Test: `tests/integration/edits/test_edit_decisions.py`

**Interfaces:**
- Consumes: `uow.edits.current_assessment`, `uow.edits.has_edits` (Task 8); `legal_outcomes(..., edited=)`, `current_effective_state` (Task 4).
- Produces: no new names. `decide_review` and `dependencies_ready` change behaviour as below.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/edits/test_edit_decisions.py
"""EDT-23, 24, 33, 34, 41: decisions govern one version; edits never auto-promote."""

import pytest

from services.application.decisions import (
    DecideReview,
    IllegalDecisionError,
    StaleDecisionError,
    decide_review,
)
from services.domain.decisions import (
    DecisionOutcome,
    EffectiveReviewState,
    current_effective_state,
)
from services.domain.issues import Readiness, Verdict
from tests.integration.edits.support import (
    CUSTOMER,
    GARDEN_PRODUCT,
    ORDER,
    PAUSED_CUSTOMER,
    PRODUCT,
    count,
    edit,
    processed,
    review_for,
    uow_for,
)
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.unit.classify.test_rules import FixedClock

CANONICAL = ("review_decision", "canonical_identity", "canonical_revision", "canonical_current")


def decide(engine, item, revision_id, outcome, sequence=0, supersedes=None, key=None, reason="Checked"):
    return decide_review(
        DecideReview(
            item.id, revision_id, sequence, outcome, "Alexis",
            reason, key or f"{item.id}-{outcome}-{sequence}", supersedes,
        ),
        uow_for(engine),
        FixedClock(),
    )


def canonical_for(engine, revision_id):
    with uow_for(engine) as uow:
        return uow.canonicals.for_candidate(revision_id)


def test_rejected_record_is_pending_after_edit_and_can_be_rejected_again(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    rejected = decide(engine, item, terminal.id, DecisionOutcome.REJECT)
    outcome = edit(engine, item, terminal, fields=(("product.stock_qty", "7"),))
    with uow_for(engine) as uow:
        history = uow.decisions.for_review(item.id)
    assert current_effective_state(history, outcome.terminal_revision_id) is EffectiveReviewState.PENDING
    again = decide(
        engine, item, outcome.terminal_revision_id, DecisionOutcome.REJECT, 1, rejected.decision.id,
    )
    assert again.effective_state is EffectiveReviewState.REJECTED
    with pytest.raises(IllegalDecisionError):
        decide(engine, item, outcome.terminal_revision_id, DecisionOutcome.REJECT, 2, again.decision.id)


def test_decision_on_superseded_version_is_stale(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    edit(engine, item, terminal, fields=(("product.stock_qty", "7"),))
    before = {table: count(engine, table) for table in CANONICAL}
    with pytest.raises(StaleDecisionError):
        decide(engine, item, terminal.id, DecisionOutcome.APPROVE)
    assert {table: count(engine, table) for table in CANONICAL} == before


def test_approving_edited_customer_promotes_it_and_unblocks_orders(engine, tmp_path):
    run = processed(engine, tmp_path, PAUSED_CUSTOMER + PRODUCT + ORDER)
    customer, customer_terminal = review_for(engine, run, "CUST-1001")
    order, order_terminal = review_for(engine, run, "ORD-3001")
    fixed = edit(engine, customer, customer_terminal, fields=(("customer.status", "active"),))
    assert (fixed.verdict, fixed.readiness) == (Verdict.CLEAN, Readiness.ELIGIBLE)
    result = decide(engine, customer, fixed.terminal_revision_id, DecisionOutcome.APPROVE)
    assert result.canonical_revision.candidate_revision_id == fixed.terminal_revision_id
    promoted = canonical_for(engine, order_terminal.id)
    assert promoted is not None and promoted.revision_number == 1


def test_cascade_never_promotes_an_edited_record(engine, tmp_path):
    run = processed(engine, tmp_path, PAUSED_CUSTOMER + PRODUCT + ORDER)
    customer, customer_terminal = review_for(engine, run, "CUST-1001")
    order, order_terminal = review_for(engine, run, "ORD-3001")
    edited_order = edit(engine, order, order_terminal, fields=(("order.quantity", "2"),))
    assert (edited_order.verdict, edited_order.readiness) == (
        Verdict.CLEAN, Readiness.BLOCKED_BY_DEPENDENCY,
    )
    decide(engine, customer, customer_terminal.id, DecisionOutcome.APPROVE)
    assert canonical_for(engine, edited_order.terminal_revision_id) is None
    with uow_for(engine) as uow:
        history = uow.decisions.for_review(order.id)
    assert current_effective_state(history, edited_order.terminal_revision_id) is EffectiveReviewState.PENDING
    approved = decide(engine, order, edited_order.terminal_revision_id, DecisionOutcome.APPROVE)
    assert approved.canonical_revision.candidate_revision_id == edited_order.terminal_revision_id
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest -q tests/integration/edits/test_edit_decisions.py`
Expected: FAIL. The stale test fails because `decide_review` still reads `item.classification_id`; the cascade test fails because the edited order is auto-promoted; the unblock test fails because the order still points at the pre-edit customer.

- [ ] **Step 3: Follow the target record's terminal version in readiness**

In `services/application/dependency_readiness.py`, replace the first lines of the loop:

```python
    for dependency in dependencies:
        recorded = dependency.target_candidate_revision_id
        if recorded is None:
            return False
        # Edits append versions; the referenced record's current version decides.
        target = candidates.terminal(candidates.get(recorded).raw_record_id).id
        canonical = canonicals.for_candidate(target)
```

Leave the rest of the loop unchanged (it already uses `target`).

- [ ] **Step 4: Use the current assessment in `decide_review`**

In `services/application/decisions.py`:

Replace

```python
        item = uow.reviews.get_locked(command.review_item_id)
        classification = uow.classifications.get(item.classification_id)
```

with

```python
        item = uow.reviews.get_locked(command.review_item_id)
        assessment = uow.edits.current_assessment(item.id)
        classification = uow.classifications.get(assessment.classification_id)
```

Replace

```python
        legal = legal_outcomes(classification.verdict)
```

with

```python
        legal = legal_outcomes(
            classification.verdict, edited=assessment.human_edit_id is not None
        )
```

Replace

```python
        if previous is not None and previous.outcome == command.outcome:
            raise IllegalDecisionError("A superseding decision must change the outcome")
```

with

```python
        if (
            previous is not None
            and previous.outcome == command.outcome
            and previous.candidate_revision_id == candidate.id
        ):
            raise IllegalDecisionError("A superseding decision must change the outcome")
```

Replace

```python
        if previous is not None and previous.outcome is DecisionOutcome.APPROVE:
```

with

```python
        if (
            previous is not None
            and previous.outcome is DecisionOutcome.APPROVE
            and previous.candidate_revision_id == candidate.id
        ):
```

In `_unblock_dependants`, extend the skip condition:

```python
            if (
                result.verdict not in (Verdict.CLEAN, Verdict.AUTO_REPAIRED)
                or uow.candidates.terminal(candidate.raw_record_id).id != candidate.id
                or uow.canonicals.for_candidate(candidate.id) is not None
                # An edited record reaches canonical data only through its own approval.
                or uow.edits.has_edits(candidate.raw_record_id)
            ):
                remaining.remove(result)
                continue
```

- [ ] **Step 5: Run the new and existing decision suites**

Run: `.venv/bin/pytest -q tests/integration/edits tests/integration/review tests/unit/review && .venv/bin/pyright services`
Expected: PASS; 0 errors. Existing `DEC-*` tests pass unchanged because unedited records keep assessment 1 and their terminal version is the recorded one.

- [ ] **Step 6: Commit**

```bash
git add services/application tests/integration/edits/test_edit_decisions.py
git commit -m "feat: decide on the current assessment and never cascade-promote edits"
```

---

### Task 12: Read projections: queue, detail and edit context

**Files:**
- Modify: `services/application/views.py`, `services/application/queries.py`, `services/application/read_ports.py`, `services/application/query_services.py`, `services/infrastructure/db/read_repository.py`, `services/infrastructure/db/derived_repositories.py` (public `to_review_item`)
- Test: `tests/integration/edits/test_edit_reads.py`

**Interfaces:**
- Consumes: `not_editable_reason`, `dismissible_findings` (Task 9); `EDITABLE_FIELDS` (Task 5); `entity_checks` (Task 3); `SqlAlchemyEditRepository` (Task 8).
- Produces:
  - `ReviewRowView` gains `edit_count: int = 0`, `assessment_sequence: int = 1` (appended fields). Its `classification_id`, `candidate_revision_id`, `verdict`, `readiness`, `effective_state`, and `reason_summaries` now come from the current assessment.
  - `ReviewDetailView` gains `edits: tuple[EditHistoryView, ...] = ()`, `editable: bool = False`, `not_editable_reason: str | None = None`, `latest_edit_failure: EditAttemptFailure | None = None`.
  - `EditHistoryView(edit, overridden_by_edit_number, reason_label)`, `EditableFieldView(field_path, state, value, source_text, set_by_edit_number)`, `CheckView(id, kind, label, fields, fired_fields)`, `FindingView(kind, counterpart_id, detail, summary)`, `EditContextView(...)` (fields below), `EditContextQuery(review_item_id)`, `ReadRepository.edit_context`, `get_edit_context(repo, query)`.
  - Run detail records gain `edit_count`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/edits/test_edit_reads.py
"""API-12 (data), EDT-08 (display): reads follow the current assessment."""

from services.application.decisions import DecideReview, decide_review
from services.application.queries import (
    AllFilesScope,
    EditContextQuery,
    ReviewDetailQuery,
    ReviewQueueQuery,
    RunDetailQuery,
)
from services.domain.decisions import DecisionOutcome, EffectiveReviewState
from services.domain.edits import Citation, EditReason
from services.infrastructure.db.read_repository import SqlAlchemyReadRepository
from tests.integration.edits.support import (
    CUSTOMER,
    GARDEN_PRODUCT,
    PRODUCT,
    edit,
    processed,
    review_for,
    uow_for,
)
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.unit.classify.test_rules import FixedClock


def test_detail_lists_edits_newest_first_with_overrides(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    first = edit(engine, item, terminal, fields=(("product.stock_qty", "7"),))
    second = edit(
        engine, item, first.terminal_revision_id, EditReason.BUSINESS_RULE_UPDATED,
        citations=(Citation("vocab.product_category", "product.category", "Garden"),),
    )
    detail = SqlAlchemyReadRepository(engine).review_detail(ReviewDetailQuery(item.id))
    assert [(e.edit.edit_number, e.overridden_by_edit_number) for e in detail.edits] == [(2, None), (1, 2)]
    assert detail.edits[0].edit == second.edit
    assert (detail.item.edit_count, detail.item.assessment_sequence) == (2, 3)
    assert detail.item.candidate_revision_id == second.terminal_revision_id
    assert detail.item.verdict == "CLEAN" and detail.item.reason_summaries == ()
    assert detail.editable and detail.not_editable_reason is None
    assert [o.value for o in detail.allowed_outcomes] == ["approve", "reject"]
    assert any(n.kind == "human_edit" and n.id == second.edit.id for n in detail.evidence.nodes)


def test_rejected_then_edited_record_is_pending_in_the_queue(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    decide_review(
        DecideReview(item.id, terminal.id, 0, DecisionOutcome.REJECT, "Alexis", "No", "rej"),
        uow_for(engine), FixedClock(),
    )
    edit(engine, item, terminal, fields=(("product.stock_qty", "0"),))
    queue = SqlAlchemyReadRepository(engine).review_queue(
        ReviewQueueQuery(AllFilesScope(), EffectiveReviewState.PENDING)
    )
    (row,) = [r for r in queue.items if r.id == item.id]
    assert row.effective_state == "pending" and row.edit_count == 1
    assert any("Stock quantity 0" in summary for summary in row.reason_summaries)


def test_edit_context_describes_fields_checks_and_findings(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    context = SqlAlchemyReadRepository(engine).edit_context(EditContextQuery(item.id))
    assert context.editable and context.edit_number == 0
    assert context.candidate_revision_id == terminal.id
    paths = [f.field_path for f in context.fields]
    assert "product.category" in paths and "product.unit_price_gbp" not in paths
    category = next(f for f in context.fields if f.field_path == "product.category")
    assert (category.state, category.value, category.source_text, category.set_by_edit_number) == (
        "known", "Garden", "Garden", None,
    )
    assert context.checks[0].id == "vocab.product_category"
    assert context.checks[0].fired_fields == ("product.category",)
    assert context.findings == ()


def test_edit_context_lists_duplicate_finding_and_marks_edited_fields(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + PRODUCT + PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004", occurrence=1)
    reader = SqlAlchemyReadRepository(engine)
    (finding,) = reader.edit_context(EditContextQuery(item.id)).findings
    assert (finding.kind, finding.summary) == ("duplicate", "Duplicate of line 2")
    edit(engine, item, terminal, fields=(("product.name", "Widget 2"),))
    context = reader.edit_context(EditContextQuery(item.id))
    name = next(f for f in context.fields if f.field_path == "product.name")
    assert (name.value, name.set_by_edit_number, context.edit_number) == ("Widget 2", 1, 1)


def test_approved_record_context_is_not_editable(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    decide_review(
        DecideReview(item.id, terminal.id, 0, DecisionOutcome.APPROVE, "Alexis", None, "ok"),
        uow_for(engine), FixedClock(),
    )
    reader = SqlAlchemyReadRepository(engine)
    context = reader.edit_context(EditContextQuery(item.id))
    detail = reader.review_detail(ReviewDetailQuery(item.id))
    assert (context.editable, context.not_editable_reason) == (False, "approved")
    assert (detail.editable, detail.not_editable_reason) == (False, "approved")


def test_run_detail_counts_edits_per_record(engine, tmp_path):
    run = processed(engine, tmp_path, CUSTOMER + GARDEN_PRODUCT)
    item, terminal = review_for(engine, run, "SKU-2004")
    edit(engine, item, terminal, fields=(("product.stock_qty", "7"),))
    records = SqlAlchemyReadRepository(engine).run_detail(RunDetailQuery(run)).records
    counts = {dict(r.fields)["id"]: dict(r.fields)["edit_count"] for r in records}
    assert counts[item.raw_record_id] == 1
    assert sorted(counts.values()) == [0, 1]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest -q tests/integration/edits/test_edit_reads.py`
Expected: FAIL with `ImportError: cannot import name 'EditContextQuery'`.

- [ ] **Step 3: Add views, query, port and service**

In `services/application/queries.py`:

```python
@dataclass(frozen=True, slots=True)
class EditContextQuery:
    review_item_id: UUID
```

In `services/application/views.py`, import `Citation, Dismissal, EditAttemptFailure, HumanEdit` from `services.domain.edits`. Append `edit_count: int = 0` and `assessment_sequence: int = 1` as the last fields of `ReviewRowView`, and append to `ReviewDetailView`:

```python
    edits: tuple["EditHistoryView", ...] = ()
    editable: bool = False
    not_editable_reason: str | None = None
    latest_edit_failure: EditAttemptFailure | None = None
```

Add, before `ReviewDetailView`:

```python
@dataclass(frozen=True, slots=True)
class EditHistoryView:
    edit: HumanEdit
    overridden_by_edit_number: int | None
    reason_label: str


@dataclass(frozen=True, slots=True)
class EditableFieldView:
    field_path: str
    state: str
    value: Value
    source_text: str | None
    set_by_edit_number: int | None


@dataclass(frozen=True, slots=True)
class CheckView:
    id: str
    kind: str
    label: str
    fields: tuple[str, ...]
    fired_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FindingView:
    kind: str
    counterpart_id: UUID
    detail: str | None
    summary: str


@dataclass(frozen=True, slots=True)
class EditContextView:
    review_item_id: UUID
    editable: bool
    not_editable_reason: str | None
    candidate_revision_id: UUID
    edit_number: int
    entity_type: str | None
    business_identifier: str | None
    filename: str | None
    source_line_start: int
    source_line_end: int
    effective_state: str
    fields: tuple[EditableFieldView, ...]
    checks: tuple[CheckView, ...]
    findings: tuple[FindingView, ...]
    current_reason: str | None
    current_citations: tuple[Citation, ...]
    current_dismissals: tuple[Dismissal, ...]
```

Add labels to `code_label`: `"data_wrong": "Data is wrong"`, `"business_rule_updated": "Business rule updated"`, `"graph_wrong": "Record links are wrong"`, `"HUMAN_EDIT": "Edited by operator"`.

In `services/application/read_ports.py` add `def edit_context(self, query: EditContextQuery) -> EditContextView: ...`. In `services/application/query_services.py` add:

```python
def get_edit_context(repo: ReadRepository, query: EditContextQuery) -> EditContextView:
    return repo.edit_context(query)
```

In `services/infrastructure/db/derived_repositories.py` add:

```python
def to_review_item(row: ReviewItemModel) -> ReviewItem:
    return _review(row)
```

- [ ] **Step 4: Read the current assessment in `read_repository.py`**

Add imports: `SqlAlchemyEditRepository`, `to_review_item`, `not_editable_reason`, `dismissible_findings`, `EDITABLE_FIELDS`, `entity_checks`, `current_effective_state`, `CandidateField`, `FieldState` (already imported), `TransformationCode`, the new views and `EditContextQuery`, and `Dismissal`, `DismissalKind`.

Replace `_queue`'s statement and loop:

```python
        statement = (
            select(m.ReviewItemModel, m.RawRecordModel)
            .join(m.RawRecordModel, m.ReviewItemModel.raw_record_id == m.RawRecordModel.id)
            .where(m.ReviewItemModel.run_id.in_([r.id for r in runs]))
            .order_by(
                m.ReviewItemModel.created_at,
                m.RawRecordModel.source_line_start,
                m.ReviewItemModel.id,
            )
        )
        items: list[ReviewRowView] = []
        for review, raw in session.execute(statement):
            item = self._review_row(session, review, raw)
            # ... the existing three filters are unchanged ...
```

Replace the start of `_review_row` (signature and everything down to `reasons = ...`):

```python
    def _review_row(
        self, session: Session, review: m.ReviewItemModel, raw: m.RawRecordModel
    ) -> ReviewRowView:
        edits = SqlAlchemyEditRepository(session)
        assessment = edits.current_assessment(review.id)
        classification = session.get_one(
            m.ClassificationResultModel, assessment.classification_id
        )
        candidate = session.get_one(
            m.CandidateRevisionModel, classification.candidate_revision_id
        )
        history = SqlAlchemyDecisionRepository(session).for_review(review.id)
        latest = history[-1] if history else None
        state = current_effective_state(history, candidate.id).value
```

Delete the old `reasons = cast(...)` line. In the `ReviewRowView(...)` call, replace the reason summaries argument with `tuple(r.summary for r in assessment.reasons)` and append `len(edits.for_record(raw.id)), assessment.sequence` as the last two arguments.

Replace the body of `review_detail`:

```python
    def review_detail(self, query: ReviewDetailQuery) -> ReviewDetailView:
        with self._snapshot() as session:
            review = session.get(m.ReviewItemModel, query.review_item_id)
            if review is None:
                raise ResourceNotFoundError("Requested review item was not found")
            raw = session.get_one(m.RawRecordModel, review.raw_record_id)
            item = self._review_row(session, review, raw)
            edits_repo = SqlAlchemyEditRepository(session)
            assessment = edits_repo.current_assessment(review.id)
            history = SqlAlchemyDecisionRepository(session).for_review(review.id)
            allowed = legal_outcomes(
                Verdict(item.verdict), edited=assessment.human_edit_id is not None
            )
            if item.current_readiness == "blocked_by_dependency":
                allowed = tuple(o for o in allowed if o is not DecisionOutcome.APPROVE)
            if history and history[-1].candidate_revision_id == item.candidate_revision_id:
                allowed = tuple(o for o in allowed if o != history[-1].outcome)
            edits = edits_repo.for_record(raw.id)
            chain = SqlAlchemyCandidateRepository(session).chain(raw.id)
            reason = not_editable_reason(
                chain[-1], chain, history, SqlAlchemyCanonicalRepository(session)
            )
            failure = edits_repo.latest_failure(review.id)
            if failure is not None and edits and failure.occurred_at <= edits[-1].edited_at:
                failure = None
            return ReviewDetailView(
                item,
                self._evidence(session, review),
                tuple(DecisionView(d, code_label(d.outcome)) for d in history),
                allowed,
                tuple(
                    EditHistoryView(
                        e,
                        e.edit_number + 1 if e.edit_number < len(edits) else None,
                        code_label(e.reason),
                    )
                    for e in reversed(edits)
                ),
                reason is None,
                reason,
                failure,
            )
```

In `_evidence`, add `m.HumanEditModel` to `models`, and add `m.HumanEditModel` to the tuple of models expanded for a `CandidateRevisionModel` (it has a `candidate_revision_id` column).

In `run_detail`, create `edits = SqlAlchemyEditRepository(session)` before the records loop and add `"edit_count": len(edits.for_record(raw.id)),` to each record's dictionary.

- [ ] **Step 5: Add `edit_context`**

```python
    def edit_context(self, query: EditContextQuery) -> EditContextView:
        with self._snapshot() as session:
            row = session.get(m.ReviewItemModel, query.review_item_id)
            if row is None:
                raise ResourceNotFoundError("Requested review item was not found")
            raw = session.get_one(m.RawRecordModel, row.raw_record_id)
            edits_repo = SqlAlchemyEditRepository(session)
            candidates = SqlAlchemyCandidateRepository(session)
            classifications = SqlAlchemyClassificationRepository(session)
            assessment = edits_repo.current_assessment(row.id)
            classification = classifications.get(assessment.classification_id)
            chain = candidates.chain(raw.id)
            terminal = chain[-1]
            history = SqlAlchemyDecisionRepository(session).for_review(row.id)
            edits = edits_repo.for_record(raw.id)
            current_edit = edits[-1] if edits else None
            reason = not_editable_reason(
                terminal, chain, history, SqlAlchemyCanonicalRepository(session)
            )
            entity = terminal.entity_type.value if terminal.entity_type else None
            edit_numbers = {e.candidate_revision_id: e.edit_number for e in edits}
            latest: dict[str, tuple[int, UUID, TransformationCode]] = {}
            for revision in chain:
                for event in candidates.transformations(revision.id):
                    if event.field_path not in latest or latest[event.field_path][0] < event.sequence:
                        latest[event.field_path] = (event.sequence, revision.id, event.operation)
            fields: list[EditableFieldView] = []
            for path in EDITABLE_FIELDS.get(entity or "", ()):
                field = cast(CandidateField[object], getattr(terminal.payload, path.split(".", 1)[1]))
                texts = [
                    raw.fields[ref.field_index]
                    for ref in field.source_refs
                    if ref.raw_record_id == raw.id and ref.field_index is not None
                    and ref.field_index < len(raw.fields)
                ]
                last = latest.get(path)
                fields.append(
                    EditableFieldView(
                        path,
                        field.state.value,
                        freeze(field.value),
                        ",".join(texts) if texts else None,
                        edit_numbers.get(last[1])
                        if last and last[2] is TransformationCode.HUMAN_EDIT
                        else None,
                    )
                )
            issues = candidates.issues(terminal.id)
            checks = [
                CheckView(
                    c.id, c.kind.value, c.label, c.fields,
                    tuple(sorted({i.field_path for i in issues if i.check_id == c.id})),
                )
                for c in (entity_checks(entity) if entity else ())
            ]
            checks.sort(key=lambda c: not c.fired_fields)
            lines = dict(
                session.execute(
                    select(m.RawRecordModel.id, m.RawRecordModel.source_line_start).where(
                        m.RawRecordModel.run_id == row.run_id
                    )
                ).tuples()
            )
            findings = tuple(
                FindingView(d.kind.value, d.counterpart_id, d.detail, _finding_summary(d, lines))
                for d in dismissible_findings(
                    to_review_item(row), assessment, classification, current_edit,
                    classifications=classifications, candidates=candidates,
                    raw_ids=frozenset(lines),
                )
            )
            return EditContextView(
                row.id,
                reason is None,
                reason,
                terminal.id,
                len(edits),
                entity,
                _business_identifier(session.get_one(m.CandidateRevisionModel, terminal.id)),
                self._run(session, session.get_one(m.RunModel, row.run_id)).filename,
                raw.source_line_start,
                raw.source_line_end,
                current_effective_state(history, terminal.id).value,
                tuple(fields),
                tuple(checks),
                findings,
                current_edit.reason.value if current_edit else None,
                current_edit.citations if current_edit else (),
                current_edit.dismissals if current_edit else (),
            )
```

Add the module-level helper:

```python
def _finding_summary(finding: Dismissal, lines: Mapping[UUID, int]) -> str:
    line = lines.get(finding.counterpart_id)
    if finding.kind is DismissalKind.DUPLICATE:
        return f"Duplicate of line {line}"
    if finding.kind is DismissalKind.CONFLICT:
        return (
            f"Business ID conflict with line {line}"
            if finding.detail == "same_run"
            else "Business ID conflict with a record from an earlier file"
        )
    return f"{(finding.detail or 'record').title()} link to line {line}"
```

- [ ] **Step 6: Run the read tests and every existing projection test**

Run: `.venv/bin/pytest -q tests/integration/edits tests/integration/api tests/unit/application tests/unit/api && .venv/bin/pyright services`
Expected: PASS; 0 errors. Unit tests that build `ReviewRowView` or `ReviewDetailView` positionally still work, because the new fields are appended with defaults.

- [ ] **Step 7: Commit**

```bash
git add services tests/integration/edits/test_edit_reads.py
git commit -m "feat: project current assessments, edit history and edit context"
```

---

### Task 13: Edit API and error envelope

**Files:**
- Create: `services/api/routes/edits.py`
- Modify: `services/api/models.py`, `services/api/errors.py`, `services/api/app.py`
- Test: `tests/integration/api/test_edit_api.py`

**Interfaces:**
- Consumes: `append_edit`, `preview_edit`, `AppendEdit` (Tasks 9–10); `get_edit_context`, `EditContextQuery` (Task 12); `CodedError` (Task 9); `database_error_kind` (Task 10); `Runtime.database_error_kind`.
- Produces HTTP: `GET /api/reviews/{id}/edit-context`, `POST /api/reviews/{id}/edits/preview`, `POST /api/reviews/{id}/edits` (201 new, 200 replay). Body shape: `{expected_candidate_revision_id, reason, note?, fields: [{field_path, input_text} | {field_path, absent: true}], citations: [{check_id, field_path, accepted_value?}], dismissals: [{kind, counterpart_id, detail?}], operator_name, idempotency_key}`; preview omits `idempotency_key`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/api/test_edit_api.py
"""API-12..14: edit endpoints over real PostgreSQL, with every error code."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from tests.integration.api.test_product_api import app as app
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.unit.classify.test_rules import CUSTOMER, PRODUCT

GARDEN = CUSTOMER + PRODUCT.replace("Electronics", "Garden")
LEAKS = ("sqlalchemy", "psycopg", "SELECT", "Traceback", "injected")


def run(app, body):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            uploaded = await client.post(
                "/api/uploads",
                files={"file": ("garden.csv", GARDEN.encode())},
                data={"operator_name": "Alexis", "idempotency_key": str(uuid4())},
            )
            run_id = uploaded.json()["run_id"]
            assert (await client.post(f"/api/runs/{run_id}/process")).status_code == 200
            queue = (await client.get("/api/reviews", params={"scope": "current", "run_id": run_id})).json()
            (item,) = [i for i in queue["items"] if i["business_identifier"] == "SKU-2004"]
            return await body(client, item)

    return asyncio.run(scenario())


def edit_body(item, **overrides):
    body = {
        "expected_candidate_revision_id": item["candidate_revision_id"],
        "reason": "data_wrong",
        "fields": [{"field_path": "product.stock_qty", "input_text": "7"}],
        "citations": [],
        "dismissals": [],
        "operator_name": "Alexis",
        "idempotency_key": str(uuid4()),
    }
    return body | overrides


def assert_error(response, status, code, detail_keys):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code and error["message"]
    assert set(error["details"]) == set(detail_keys)
    assert not any(leak in response.text for leak in LEAKS)


def test_edit_context_append_replay_and_detail(app):
    async def body(client, item):
        context = await client.get(f"/api/reviews/{item['id']}/edit-context")
        assert context.status_code == 200, context.text
        data = context.json()
        assert data["editable"] is True and data["checks"][0]["id"] == "vocab.product_category"
        assert "product.unit_price_gbp" not in [f["field_path"] for f in data["fields"]]
        preview = await client.post(
            f"/api/reviews/{item['id']}/edits/preview",
            json={k: v for k, v in edit_body(item).items() if k != "idempotency_key"},
        )
        assert preview.status_code == 200 and preview.json()["verdict"] == "NEEDS_REVIEW"
        request = edit_body(item)
        created = await client.post(f"/api/reviews/{item['id']}/edits", json=request)
        assert created.status_code == 201, created.text
        edit = created.json()["edit"]
        assert edit["edit_number"] == 1 and len(edit["content_hash"]) == 64
        assert set(edit["edited_at"]) == {"instant", "display", "timezone"}
        replay = await client.post(f"/api/reviews/{item['id']}/edits", json=request)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert replay.json()["edit"]["id"] == edit["id"]
        detail = (await client.get(f"/api/reviews/{item['id']}")).json()
        assert [e["edit"]["id"] for e in detail["edits"]] == [edit["id"]]
        assert detail["edits"][0]["overridden_by_edit_number"] is None
        assert detail["item"]["assessment_sequence"] == 2 and detail["editable"] is True

    run(app, body)


def test_every_request_error_code(app):
    async def body(client, item):
        url = f"/api/reviews/{item['id']}/edits"
        assert_error(
            await client.post(url, json=edit_body(item, fields=[{"field_path": "product.unit_price_gbp", "input_text": "1"}])),
            422, "edit_field_not_editable", {"field_path"},
        )
        assert_error(await client.post(url, json=edit_body(item, fields=[])), 422, "edit_reason_mismatch", {"reason"})
        assert_error(
            await client.post(url, json=edit_body(
                item, reason="business_rule_updated", fields=[],
                citations=[{"check_id": "vocab.order_status", "field_path": "order.status", "accepted_value": "held"}],
            )),
            422, "edit_citation_invalid", {"check_id", "field_path"},
        )
        assert_error(
            await client.post(url, json=edit_body(
                item, reason="graph_wrong", fields=[],
                dismissals=[{"kind": "duplicate", "counterpart_id": str(uuid4())}],
            )),
            422, "edit_dismissal_invalid", {"descriptor"},
        )
        assert_error(
            await client.post(url, json=edit_body(item, fields=[{"field_path": "product.stock_qty", "input_text": "5"}])),
            422, "edit_no_change", set(),
        )
        assert_error(
            await client.post(url, json=edit_body(item, expected_candidate_revision_id=str(uuid4()))),
            409, "edit_stale", {"current_candidate_revision_id", "current_edit_number"},
        )
        first = edit_body(item, idempotency_key="shared")
        assert (await client.post(url, json=first)).status_code == 201
        assert_error(
            await client.post(url, json=first | {"note": "Different"}),
            409, "edit_idempotency_conflict", set(),
        )
        assert_error(await client.post(url, json=edit_body(item, operator_name="")), 422, "validation_error", {"issues"})

    run(app, body)


def test_not_editable_after_approval(app):
    async def body(client, item):
        decided = await client.post(
            f"/api/reviews/{item['id']}/decisions",
            json={
                "candidate_revision_id": item["candidate_revision_id"], "expected_sequence": 0,
                "outcome": "approve", "operator_name": "Alexis", "idempotency_key": "approve",
            },
        )
        assert decided.status_code == 200, decided.text
        response = await client.post(f"/api/reviews/{item['id']}/edits", json=edit_body(item))
        assert_error(response, 409, "edit_not_editable", {"reason"})
        assert response.json()["error"]["details"]["reason"] == "approved"

    run(app, body)


@pytest.mark.parametrize(
    ("target", "code"),
    [("parse_input", "edit_parse_failed"), ("assess", "edit_recheck_failed"), ("_persist", "edit_persist_failed")],
)
def test_worker_failures_have_codes_and_references(app, monkeypatch, target, code):
    import services.application.edits as edits

    def explode(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(edits, target, explode)

    async def body(client, item):
        response = await client.post(f"/api/reviews/{item['id']}/edits", json=edit_body(item))
        assert_error(response, 500, code, {"stage", "retryable", "reference"})
        assert response.json()["error"]["details"]["retryable"] is True

    run(app, body)


def test_lock_timeout_returns_503(app, engine, monkeypatch):
    import services.application.edits as edits

    monkeypatch.setattr(edits, "LOCK_TIMEOUT_MS", 200)

    async def body(client, item):
        with engine.connect() as holder:
            transaction = holder.begin()
            holder.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('canonical-promotions', 0))"))
            response = await client.post(f"/api/reviews/{item['id']}/edits", json=edit_body(item))
            transaction.rollback()
        assert_error(response, 503, "edit_lock_timeout", {"stage", "retryable", "reference"})

    run(app, body)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest -q tests/integration/api/test_edit_api.py`
Expected: FAIL with 404 responses for the new routes.

- [ ] **Step 3: Add request models to `services/api/models.py`**

```python
from typing import Self

from pydantic import model_validator

from services.application.edits import AppendEdit
from services.domain.edits import (
    Citation,
    Dismissal,
    DismissalKind,
    EditReason,
    FieldInput,
)


class FieldInputBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_path: str = Field(min_length=1)
    input_text: str | None = None
    absent: bool = False

    @model_validator(mode="after")
    def exactly_one_value(self) -> Self:
        if self.absent == (self.input_text is not None):
            raise ValueError("Give either input_text or absent: true")
        return self


class CitationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_id: str = Field(min_length=1)
    field_path: str = Field(min_length=1)
    accepted_value: str | None = None


class DismissalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: DismissalKind
    counterpart_id: UUID
    detail: str | None = None


class EditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_candidate_revision_id: UUID
    reason: EditReason
    note: str | None = Field(default=None, max_length=2000)
    fields: list[FieldInputBody] = Field(default_factory=list[FieldInputBody])
    citations: list[CitationBody] = Field(default_factory=list[CitationBody])
    dismissals: list[DismissalBody] = Field(default_factory=list[DismissalBody])
    operator_name: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)

    def command(self, review_item_id: UUID) -> AppendEdit:
        return AppendEdit(
            review_item_id,
            self.expected_candidate_revision_id,
            self.reason,
            (self.note or "").strip() or None,
            tuple(FieldInput(f.field_path, None if f.absent else f.input_text) for f in self.fields),
            tuple(Citation(c.check_id, c.field_path, c.accepted_value) for c in self.citations),
            tuple(Dismissal(d.kind, d.counterpart_id, d.detail) for d in self.dismissals),
            self.operator_name.strip(),
            self.idempotency_key,
        )


class PreviewBody(EditBody):
    idempotency_key: str = "preview"
```

If Pydantic warns that `fields` shadows a `BaseModel` attribute, add `alias="fields"` and rename the attribute to `field_inputs`, keeping the JSON name `fields`.

- [ ] **Step 4: Add the routes**

```python
# services/api/routes/edits.py
from uuid import UUID

from fastapi import APIRouter, Response

from services.api.dependencies import ReaderDependency, RuntimeDependency
from services.api.models import EditBody, PreviewBody
from services.api.presenters import present
from services.application.edits import append_edit, preview_edit
from services.application.queries import EditContextQuery
from services.application.query_services import get_edit_context

router = APIRouter()


@router.get("/reviews/{review_item_id}/edit-context")
def edit_context(review_item_id: UUID, repo: ReaderDependency) -> object:
    with repo as opened:
        return present(get_edit_context(opened, EditContextQuery(review_item_id)))


@router.post("/reviews/{review_item_id}/edits/preview")
def preview(review_item_id: UUID, body: PreviewBody, runtime: RuntimeDependency) -> object:
    with runtime as opened:
        return present(
            preview_edit(
                body.command(review_item_id),
                opened.uow_factory,
                opened.clock,
                error_kind=opened.database_error_kind,
            )
        )


@router.post("/reviews/{review_item_id}/edits", status_code=201)
def append(
    review_item_id: UUID, body: EditBody, runtime: RuntimeDependency, response: Response
) -> object:
    with runtime as opened:
        outcome = append_edit(
            body.command(review_item_id),
            opened.uow_factory,
            opened.clock,
            error_kind=opened.database_error_kind,
        )
    if outcome.replayed:
        response.status_code = 200
    return present(outcome)
```

In `services/api/app.py`, import `edits` from `services.api.routes` and add `edits.router` to the router tuple.

- [ ] **Step 5: Map coded errors in `services/api/errors.py`**

Import `logging`, `uuid4`, `cast`, `CodedError` from `services.application.errors`, `present` from `services.api.presenters`, and `database_error_kind` from `services.infrastructure.db.errors`. Add `logger = logging.getLogger("services.api")`. At the top of `mapped`, before the other branches:

```python
        if isinstance(error, CodedError):
            return _response(
                error.status,
                error.code,
                str(error),
                cast(dict[str, object], present(error.details)),
            )
```

Just before the final `internal_error` return:

```python
        if database_error_kind(error) == "unavailable":
            reference = uuid4()
            logger.error("database_unavailable reference=%s", reference, exc_info=error)
            return _response(
                503,
                "database_unavailable",
                "The database is unavailable. Nothing was changed; try again shortly.",
                {"retryable": True, "reference": str(reference)},
            )
```

Add `CodedError` to the tuple of registered exception classes (before `HTTPException`).

- [ ] **Step 6: Run the API suites**

Run: `.venv/bin/pytest -q tests/integration/api tests/unit/api && .venv/bin/ruff check services tests && .venv/bin/pyright services`
Expected: PASS; clean.

- [ ] **Step 7: Commit**

```bash
git add services/api tests/integration/api/test_edit_api.py
git commit -m "feat: expose edit context, preview and append endpoints with error codes"
```

---

### Task 14: Processing and database error codes

**Files:**
- Modify: `services/application/errors.py` (add `ProcessingStageFailed`, `ProcessingInvalidState`), `services/api/routes/runs.py`
- Test: `tests/integration/api/test_processing_errors.py`

**Interfaces:**
- Consumes: `CodedError`, `database_error_kind` handling in `errors.py` (Task 13).
- Produces: `ProcessingStageFailed(stage, run_id, reference)` with code `processing_{stage}_failed`, status 500; `ProcessingInvalidState(message, run_id, state)` with code `processing_invalid_state`, status 422.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/api/test_processing_errors.py
"""ERR-01, ERR-02: stage failures and an unreachable database have specific codes."""

import asyncio
from uuid import uuid4

import httpx
import pytest

import services.application.process as process
import services.pipeline.classify as classify
from services.api.app import create_app
from services.infrastructure.runtime import Settings
from tests.integration.api.test_product_api import app as app
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.unit.classify.test_rules import CUSTOMER, ORDER, PRODUCT

LEAKS = ("sqlalchemy", "psycopg", "Traceback", "injected", "password")


def fail(*_args):
    raise RuntimeError("injected")


def install(monkeypatch, stage):
    if stage == "parse":
        original = process.parse_run
        monkeypatch.setattr(
            process, "parse_run",
            lambda run_id, size, factory, store, *, clock=None: original(
                run_id, size, factory, store, fail, clock=clock
            ),
        )
    elif stage == "normalise":
        original_normalise = process.normalise_run
        monkeypatch.setattr(
            process, "normalise_run",
            lambda run_id, size, factory, context, **_: original_normalise(
                run_id, size, factory, context, failure_injector=fail
            ),
        )
    elif stage == "classify":
        monkeypatch.setattr(classify, "classify_graph", fail)
    else:
        original_stage = process.stage_run
        monkeypatch.setattr(
            process, "stage_run",
            lambda run_id, factory, clock, *_: original_stage(run_id, factory, clock, fail),
        )


@pytest.mark.parametrize("stage", ["parse", "normalise", "classify", "load"])
def test_processing_failures_return_stage_codes(app, monkeypatch, stage):
    install(monkeypatch, stage)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            uploaded = await client.post(
                "/api/uploads",
                files={"file": ("f.csv", (CUSTOMER + PRODUCT + ORDER).encode())},
                data={"operator_name": "Alexis", "idempotency_key": str(uuid4())},
            )
            run_id = uploaded.json()["run_id"]
            response = await client.post(f"/api/runs/{run_id}/process")
            assert response.status_code == 500, response.text
            error = response.json()["error"]
            assert error["code"] == f"processing_{stage}_failed"
            assert set(error["details"]) == {"stage", "run_id", "retryable", "reference"}
            assert error["details"]["run_id"] == run_id
            assert not any(leak in response.text for leak in LEAKS)
            run = (await client.get(f"/api/runs/{run_id}")).json()["run"]
            assert run["stage_failure"] == f"{stage}_failed"

    asyncio.run(scenario())


def test_unreachable_database_returns_503(tmp_path):
    app = create_app(
        settings=Settings(
            database_url="postgresql+psycopg://user:password@127.0.0.1:1/missing",
            source_root=tmp_path,
        )
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/workspace")
            assert response.status_code == 503, response.text
            error = response.json()["error"]
            assert error["code"] == "database_unavailable"
            assert set(error["details"]) == {"retryable", "reference"}
            assert not any(leak in response.text for leak in LEAKS)

    asyncio.run(scenario())
```

`parse_run`'s failure injector runs after each batch's writes and before commit, so the first batch fails and `parse_failed` is recorded; `stage_run` calls its injector per canonical insert.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest -q tests/integration/api/test_processing_errors.py`
Expected: FAIL; responses currently carry `internal_error` (500) and the unreachable database also returns 500.

- [ ] **Step 3: Add processing errors**

Append to `services/application/errors.py`:

```python
_STAGE_TEXT = {
    "parse": "reading the CSV",
    "normalise": "interpreting values",
    "classify": "checking records",
    "load": "loading canonical records",
}


class ProcessingStageFailed(CodedError):
    def __init__(self, stage: str, run_id: UUID, reference: UUID) -> None:
        super().__init__(
            f"Processing stopped while {_STAGE_TEXT[stage]}. Saved progress is kept; "
            "retry processing to continue.",
            stage=stage,
            run_id=run_id,
            retryable=True,
            reference=reference,
        )
        self.code = f"processing_{stage}_failed"


class ProcessingInvalidState(CodedError):
    code = "processing_invalid_state"
    status = 422

    def __init__(self, message: str, run_id: UUID, state: str) -> None:
        super().__init__(message, run_id=run_id, state=state)
```

- [ ] **Step 4: Map failures in `services/api/routes/runs.py`**

```python
import logging

from services.application.errors import (
    ApplicationValidationError,
    ProcessingInvalidState,
    ProcessingStageFailed,
)
from services.domain.ids import new_id

logger = logging.getLogger("services.api")
_FAILED_STAGE = {
    "parse_failed": "parse",
    "normalise_failed": "normalise",
    "classify_failed": "classify",
    "load_failed": "load",
}


@router.post("/runs/{run_id}/process")
def process(run_id: UUID, runtime: RuntimeDependency) -> object:
    with runtime as opened:
        try:
            result = process_run(
                ProcessRun(run_id), opened.uow_factory, opened.source_store, opened.clock
            )
        except ApplicationValidationError as error:
            with opened.uow_factory() as uow:
                state = uow.runs.get(run_id).state.value
            raise ProcessingInvalidState(str(error), run_id, state) from error
        except Exception as error:
            if opened.database_error_kind(error) == "unavailable":
                raise
            with opened.uow_factory() as uow:
                failure = uow.runs.get(run_id).stage_failure
            stage = _FAILED_STAGE.get(failure or "")
            if stage is None:
                raise
            reference = new_id()
            logger.error(
                "processing_%s_failed reference=%s run=%s", stage, reference, run_id,
                exc_info=error,
            )
            raise ProcessingStageFailed(stage, run_id, reference) from error
    return present(result)
```

The unreachable-database test already passes after Task 13: `build_runtime` wraps the `OperationalError` in `RuntimeConfigurationError`, and the handler follows `__cause__`.

- [ ] **Step 5: Run the API and browser-support suites**

Run: `.venv/bin/pytest -q tests/integration/api tests/unit/api tests/unit/support && .venv/bin/pyright services`
Expected: PASS; 0 errors. The browser acceptance fixture's injected normalisation failure now reaches the UI as `processing_normalise_failed`; the UI only checks for the retry button, so it is unaffected.

- [ ] **Step 6: Commit**

```bash
git add services tests/integration/api/test_processing_errors.py
git commit -m "feat: return stage-specific codes for processing and database failures"
```

---

### Task 15: Web contracts, queries, labels and edit model

**Files:**
- Modify: `apps/web/src/api/contracts.ts`, `apps/web/src/api/queries.ts`, `apps/web/src/labels.ts`, `apps/web/src/test/fixtures.ts` (`review` gains `edit_count: 0, assessment_sequence: 1`), `apps/web/src/test/workflowFixtures.ts` (`detail` gains `edits: [], editable: true, not_editable_reason: null, latest_edit_failure: null`)
- Create: `apps/web/src/features/edits/editModel.ts`
- Test: `apps/web/src/features/edits/editModel.test.ts`

**Interfaces:**
- Produces TypeScript types `EditReason`, `Citation`, `Dismissal`, `EditableField`, `CatalogueCheck`, `Finding`, `EditContext`, `FieldInput`, `EditCommand`, `EditPreviewCommand`, `FieldChangeView`, `HumanEditView`, `EditHistoryEntry`, `EditOutcome`, `EditPreview`, `EditFailure`; `ReviewRow.edit_count`, `ReviewRow.assessment_sequence`; `ReviewDetailView.edits`, `.editable`, `.not_editable_reason`, `.latest_edit_failure`; `RunRecord.edit_count`.
- Functions: `useEditContext(id, enabled)`, `previewEdit(id, command, signal)`, `appendEdit(id, command)`; labels `notEditableText(reason)`, `editErrorText(code)`; model `formatValue`, `inputText`, `initialDraft`, `changedFields`, `pairingMet`, `buildCommand`, `isDirty`, `humanSetLabels`, `type Draft`.

- [ ] **Step 1: Write the failing model tests**

```ts
// apps/web/src/features/edits/editModel.test.ts
import { expect, test } from "vitest";
import type { EditContext } from "../../api/contracts";
import {
  buildCommand,
  changedFields,
  formatValue,
  initialDraft,
  inputText,
  isDirty,
  pairingMet,
} from "./editModel";

const context = {
  review_item_id: "review-1",
  editable: true,
  not_editable_reason: null,
  candidate_revision_id: "candidate-3",
  edit_number: 0,
  entity_type: "product",
  business_identifier: "SKU-2010",
  filename: "garden.csv",
  source_line_start: 41,
  source_line_end: 41,
  effective_state: "pending",
  fields: [
    { field_path: "product.category", state: "known", value: "Garden", source_text: "Garden", set_by_edit_number: null },
    { field_path: "product.stock_qty", state: "known", value: 0, source_text: "0", set_by_edit_number: null },
    { field_path: "product.unit_price", state: "known", value: { amount: "12.50", currency: "USD" }, source_text: "$12.50", set_by_edit_number: null },
    { field_path: "product.notes", state: "absent", value: null, source_text: null, set_by_edit_number: null },
  ],
  checks: [],
  findings: [],
  current_reason: null,
  current_citations: [],
  current_dismissals: [],
} satisfies EditContext;

test("values format for display and for re-entry", () => {
  expect(formatValue({ state: "known", value: { amount: "12.50", currency: "USD" } })).toBe("$12.50");
  expect(formatValue({ state: "known", value: "2023-03-04" })).toBe("4 March 2023");
  expect(formatValue({ state: "known", value: ["vip", "newsletter"] })).toBe("vip|newsletter");
  expect(formatValue({ state: "unresolved", value: null })).toBe("Unresolved");
  expect(inputText("known", { amount: "19.99", currency: "GBP" })).toBe("19.99");
  expect(inputText("known", { amount: "12.50", currency: "USD" })).toBe("$12.50");
  expect(inputText("absent", null)).toBe("");
});

test("only touched fields that differ are submitted", () => {
  const draft = initialDraft(context, "Alexis");
  expect(changedFields(context, draft)).toEqual([]);
  draft.values["product.stock_qty"] = { text: "12", absent: false };
  draft.values["product.notes"] = { text: "", absent: true };
  expect(changedFields(context, draft).map((f) => f.field_path)).toEqual(["product.stock_qty"]);
  draft.values["product.category"] = { text: "", absent: true };
  expect(buildCommand(context, draft, "key-1").fields).toEqual([
    { field_path: "product.category", absent: true },
    { field_path: "product.stock_qty", input_text: "12" },
  ]);
});

test("pairing rules gate append for each reason", () => {
  const draft = initialDraft(context, "Alexis");
  expect(pairingMet(context, draft)).toBe(false);
  draft.values["product.stock_qty"] = { text: "12", absent: false };
  expect(pairingMet(context, draft)).toBe(true);
  draft.reason = "business_rule_updated";
  expect(pairingMet(context, draft)).toBe(false);
  draft.citations["vocab.product_category|product.category"] = { checked: true, accepted: "Garden" };
  expect(pairingMet(context, draft)).toBe(true);
  draft.reason = "graph_wrong";
  expect(pairingMet(context, draft)).toBe(true);
  draft.values["product.stock_qty"] = { text: "0", absent: false };
  expect(pairingMet(context, draft)).toBe(false);
});

test("commands carry only the reason's own citations or dismissals", () => {
  const draft = initialDraft(context, "Alexis");
  draft.reason = "business_rule_updated";
  draft.citations["vocab.product_tags|product.tags"] = { checked: true, accepted: "gift|Promo" };
  draft.dismissals["duplicate|raw-7"] = true;
  draft.note = "  Approved range  ";
  const command = buildCommand(context, draft, "key-2");
  expect(command.citations).toEqual([
    { check_id: "vocab.product_tags", field_path: "product.tags", accepted_value: "gift" },
    { check_id: "vocab.product_tags", field_path: "product.tags", accepted_value: "promo" },
  ]);
  expect(command.dismissals).toEqual([]);
  expect(command.note).toBe("Approved range");
  expect(command.expected_candidate_revision_id).toBe("candidate-3");
  expect(isDirty(context, draft)).toBe(true);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/web && bun run test src/features/edits/editModel.test.ts`
Expected: FAIL, cannot resolve `./editModel`.

- [ ] **Step 3: Extend `apps/web/src/api/contracts.ts`**

Append `edit_count: number; assessment_sequence: number;` to `ReviewRow`, `edit_count: number;` to `RunRecord`, and to `ReviewDetailView`:

```ts
  edits: EditHistoryEntry[];
  editable: boolean;
  not_editable_reason: string | null;
  latest_edit_failure: EditFailure | null;
```

Append:

```ts
export type EditReason = "data_wrong" | "business_rule_updated" | "graph_wrong";
export interface Citation {
  check_id: string;
  field_path: string;
  accepted_value: string | null;
}
export type DismissalKind = "duplicate" | "conflict" | "dependency_match";
export interface Dismissal {
  kind: DismissalKind;
  counterpart_id: string;
  detail: string | null;
}
export interface EditableField {
  field_path: string;
  state: string;
  value: EvidenceValue;
  source_text: string | null;
  set_by_edit_number: number | null;
}
export interface CatalogueCheck {
  id: string;
  kind: "vocabulary" | "format" | "limit" | "invariant" | "interpretation";
  label: string;
  fields: string[];
  fired_fields: string[];
}
export interface Finding extends Dismissal {
  summary: string;
}
export interface EditContext {
  review_item_id: string;
  editable: boolean;
  not_editable_reason: string | null;
  candidate_revision_id: string;
  edit_number: number;
  entity_type: string | null;
  business_identifier: string | null;
  filename: string | null;
  source_line_start: number;
  source_line_end: number;
  effective_state: string;
  fields: EditableField[];
  checks: CatalogueCheck[];
  findings: Finding[];
  current_reason: EditReason | null;
  current_citations: Citation[];
  current_dismissals: Dismissal[];
}
export type FieldInput =
  | { field_path: string; input_text: string }
  | { field_path: string; absent: true };
export interface EditPreviewCommand {
  expected_candidate_revision_id: string;
  reason: EditReason;
  note: string | null;
  fields: FieldInput[];
  citations: Citation[];
  dismissals: Dismissal[];
  operator_name: string;
}
export interface EditCommand extends EditPreviewCommand {
  idempotency_key: string;
}
export interface FieldChangeView {
  field_path: string;
  input_text: string | null;
  before: EvidenceValue;
  after: EvidenceValue;
}
export interface HumanEditView {
  id: string;
  edit_number: number;
  reason: EditReason;
  note: string | null;
  field_changes: FieldChangeView[];
  citations: Citation[];
  dismissals: Dismissal[];
  operator_name: string;
  edited_at: AbsoluteInstant;
  content_hash: string;
  parent_hash: string | null;
  candidate_revision_id: string;
  rules_version: string;
}
export interface EditHistoryEntry {
  edit: HumanEditView;
  overridden_by_edit_number: number | null;
  reason_label: string;
}
export interface ReasonView {
  field_path: string;
  summary: string;
}
export interface EditOutcome {
  edit: HumanEditView;
  terminal_revision_id: string;
  verdict: Verdict;
  readiness: string;
  reasons: ReasonView[];
  legal_outcomes: DecisionOutcome[];
  replayed: boolean;
}
export interface EditPreview {
  changes: FieldChangeView[];
  verdict: Verdict;
  readiness: string;
  reasons: ReasonView[];
}
export interface EditFailure {
  code: string;
  stage: string;
  reference: string;
  occurred_at: AbsoluteInstant;
}
```

In `apps/web/src/test/fixtures.ts` add `edit_count: 0, assessment_sequence: 1,` to `review`; in `apps/web/src/test/workflowFixtures.ts` add `edits: [], editable: true, not_editable_reason: null, latest_edit_failure: null,` to `detail`.

- [ ] **Step 4: Add calls to `apps/web/src/api/queries.ts`**

```ts
export function useEditContext(id: string, enabled = true) {
  return useQuery({
    queryKey: ["edit-context", id],
    queryFn: ({ signal }) =>
      request<EditContext>(`/reviews/${encodeURIComponent(id)}/edit-context`, { signal }),
    enabled,
  });
}
const json = { "Content-Type": "application/json" };
export function previewEdit(id: string, command: EditPreviewCommand, signal?: AbortSignal) {
  return request<EditPreview>(`/reviews/${encodeURIComponent(id)}/edits/preview`, {
    method: "POST",
    headers: json,
    body: JSON.stringify(command),
    signal,
  });
}
export function appendEdit(id: string, command: EditCommand) {
  return request<EditOutcome>(`/reviews/${encodeURIComponent(id)}/edits`, {
    method: "POST",
    headers: json,
    body: JSON.stringify(command),
  });
}
```

Add `EditContext, EditPreviewCommand, EditPreview, EditCommand, EditOutcome` to the type import.

- [ ] **Step 5: Add copy to `apps/web/src/labels.ts`**

Add to `labels`:

```ts
  data_wrong: "Data is wrong",
  business_rule_updated: "Business rule updated",
  graph_wrong: "Record links are wrong",
  HUMAN_EDIT: "Edited by operator",
  listed_date: "Listed date",
  signup_date: "Signup date",
  ordered_at: "Ordered at",
  unit_price: "Unit price",
  lifetime_spend: "Lifetime spend",
  customer_name_raw: "Customer name",
  "vocab.product_category": "Category vocabulary",
  "vocab.customer_status": "Customer status vocabulary",
  "vocab.product_status": "Product status vocabulary",
  "vocab.order_status": "Order status vocabulary",
  "vocab.customer_tags": "Customer tag vocabulary",
  "vocab.product_tags": "Product tag vocabulary",
  "vocab.order_tags": "Order tag vocabulary",
  "format.customer_id": "Customer identifier format",
  "format.sku": "Product identifier format",
  "format.order_id": "Order identifier format",
  "format.email": "Email format",
  "format.date_only": "Date without a time",
  "limit.required_field": "Required field",
  "limit.positive_amount": "Price above zero",
  "limit.nonnegative_amount": "Lifetime spend not negative",
  "limit.order_quantity_nonzero": "Order quantity not zero",
  "limit.customer_quantity_empty": "Customer quantity empty",
  "invariant.refund": "Refund consistency",
  "invariant.stock_status": "Stock and status consistency",
  "interpret.slash_date_mdy": "Slash dates read month/day/year",
  "interpret.single_comma_thousands": "Single comma read as thousands",
  "interpret.status_mapping": "Status spelling mapping",
  "interpret.sku_zero_padding": "SKU zero-padding repair",
  "interpret.line_total_repair": "Line-total repair",
  "interpret.fx_rate_date": "FX conversion date",
```

Append:

```ts
const notEditable: Record<string, string> = {
  approved: "Approved records must be reversed before editing.",
  acknowledged: "Acknowledged records must be rejected before editing.",
  typeless: "Records without a recognised type can't be edited.",
  promoted: "This record is already current canonical data and can't be edited here.",
};
export const notEditableText = (reason: string | null) =>
  (reason && notEditable[reason]) ?? "This record can't be edited.";
const editErrors: Record<string, string> = {
  edit_stale:
    "This record changed since you opened it. The current values are shown; review your changes and append again.",
  edit_not_editable: "This record can no longer be edited.",
  edit_no_change: "Nothing changed. Edit a value, or choose a rule or finding.",
  edit_reason_mismatch: "The chosen reason needs a changed field, a rule, or a finding.",
  edit_field_not_editable: "One of the fields can't be edited.",
  edit_citation_invalid: "One of the chosen rules doesn't apply to this field.",
  edit_dismissal_invalid: "One of the chosen findings is no longer on this record.",
  edit_idempotency_conflict: "This edit was already submitted with different values. Reopen the record and try again.",
  edit_parse_failed: "Your values couldn't be read on the server. Nothing was changed.",
  edit_recheck_failed: "The re-check failed on the server. Nothing was changed.",
  edit_persist_failed: "The edit couldn't be saved. Nothing was changed.",
  edit_lock_timeout: "The record is busy. Nothing was changed.",
  database_unavailable: "The database is unavailable. Nothing was changed.",
};
export const editErrorText = (code: string) =>
  editErrors[code] ?? "The edit couldn't be appended. Nothing was changed.";
export const retryableEditErrors = new Set([
  "edit_parse_failed",
  "edit_recheck_failed",
  "edit_persist_failed",
  "edit_lock_timeout",
  "database_unavailable",
  "network_error",
]);
```

- [ ] **Step 6: Implement `apps/web/src/features/edits/editModel.ts`**

```ts
import type {
  Citation,
  Dismissal,
  DismissalKind,
  EditCommand,
  EditContext,
  EditHistoryEntry,
  EditReason,
  EditableField,
  EvidenceValue,
  FieldInput,
} from "../../api/contracts";
import { displayLabel } from "../../labels";

export interface Draft {
  reason: EditReason;
  values: Record<string, { text: string; absent: boolean }>;
  citations: Record<string, { checked: boolean; accepted: string }>;
  dismissals: Record<string, boolean>;
  operator: string;
  note: string;
}
const symbols: Record<string, string> = { USD: "$", EUR: "€", GBP: "£" };
const isoDate = /^\d{4}-\d{2}-\d{2}$/;
const record = (v: EvidenceValue): Record<string, EvidenceValue> =>
  v && typeof v === "object" && !Array.isArray(v) ? v : {};

function display(value: EvidenceValue): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.map(display).join("|");
  if (typeof value === "object") {
    if ("amount" in value && "currency" in value)
      return `${symbols[String(value.currency)] ?? `${value.currency} `}${value.amount}`;
    if ("display" in value) return String(value.display);
    return JSON.stringify(value);
  }
  if (typeof value === "string" && isoDate.test(value))
    return new Intl.DateTimeFormat("en-GB", {
      day: "numeric",
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    }).format(new Date(`${value}T00:00:00Z`));
  return String(value);
}

/** A candidate field ({state, value}) as operator-facing text. */
export function formatValue(field: EvidenceValue): string {
  const f = record(field);
  if (f.state !== "known") return displayLabel(String(f.state ?? "absent"));
  return display(f.value ?? null);
}

/** Text that re-enters through the CSV normalisers as the same value. */
export function inputText(state: string, value: EvidenceValue): string {
  if (state !== "known" || value === null) return "";
  const v = record(value);
  if ("amount" in v && "currency" in v)
    return v.currency === "GBP" ? String(v.amount) : `${symbols[String(v.currency)] ?? ""}${v.amount}`;
  if (Array.isArray(value)) return value.map(String).join("|");
  return String(value);
}

const citationKey = (c: { check_id: string; field_path: string }) => `${c.check_id}|${c.field_path}`;
const dismissalKey = (d: { kind: string; counterpart_id: string }) => `${d.kind}|${d.counterpart_id}`;

export function initialDraft(context: EditContext, operator: string): Draft {
  const citations: Draft["citations"] = {};
  for (const c of context.current_citations) {
    const key = citationKey(c);
    const previous = citations[key]?.accepted;
    citations[key] = {
      checked: true,
      accepted: [previous, c.accepted_value].filter(Boolean).join("|"),
    };
  }
  return {
    reason: context.current_reason ?? "data_wrong",
    values: Object.fromEntries(
      context.fields.map((f) => [f.field_path, { text: inputText(f.state, f.value), absent: false }]),
    ),
    citations,
    dismissals: Object.fromEntries(context.current_dismissals.map((d) => [dismissalKey(d), true])),
    operator,
    note: "",
  };
}

function changed(field: EditableField, draft: Draft) {
  const value = draft.values[field.field_path];
  if (!value) return false;
  if (value.absent) return field.state !== "absent";
  return value.text !== inputText(field.state, field.value);
}

export function changedFields(context: EditContext, draft: Draft): FieldInput[] {
  return context.fields
    .filter((f) => changed(f, draft))
    .map((f) =>
      draft.values[f.field_path].absent
        ? { field_path: f.field_path, absent: true as const }
        : { field_path: f.field_path, input_text: draft.values[f.field_path].text },
    )
    .sort((a, b) => a.field_path.localeCompare(b.field_path));
}

function citations(draft: Draft): Citation[] {
  return Object.entries(draft.citations)
    .filter(([, c]) => c.checked)
    .flatMap(([key, c]) => {
      const [check_id, field_path] = key.split("|");
      if (!check_id.startsWith("vocab.")) return [{ check_id, field_path, accepted_value: null }];
      const values = check_id.endsWith("_tags")
        ? c.accepted.split("|").map((t) => t.trim().toLowerCase()).filter(Boolean)
        : [c.accepted.trim()].filter(Boolean);
      return values.map((accepted_value) => ({ check_id, field_path, accepted_value }));
    });
}

function dismissals(context: EditContext, draft: Draft): Dismissal[] {
  return context.findings
    .filter((f) => draft.dismissals[dismissalKey(f)])
    .map((f) => ({ kind: f.kind as DismissalKind, counterpart_id: f.counterpart_id, detail: f.detail }));
}

export function pairingMet(context: EditContext, draft: Draft): boolean {
  const changes = changedFields(context, draft).length;
  if (draft.reason === "data_wrong") return changes > 0;
  if (draft.reason === "business_rule_updated") return citations(draft).length > 0;
  return changes + dismissals(context, draft).length > 0;
}

export function buildCommand(context: EditContext, draft: Draft, key: string): EditCommand {
  return {
    expected_candidate_revision_id: context.candidate_revision_id,
    reason: draft.reason,
    note: draft.note.trim() || null,
    fields: changedFields(context, draft),
    citations: draft.reason === "business_rule_updated" ? citations(draft) : [],
    dismissals: draft.reason === "graph_wrong" ? dismissals(context, draft) : [],
    operator_name: draft.operator.trim(),
    idempotency_key: key,
  };
}

export function isDirty(context: EditContext, draft: Draft): boolean {
  const initial = initialDraft(context, draft.operator);
  return (
    changedFields(context, draft).length > 0 ||
    draft.note.trim() !== "" ||
    draft.reason !== initial.reason ||
    JSON.stringify(citations(draft)) !== JSON.stringify(citations(initial)) ||
    JSON.stringify(dismissals(context, draft)) !== JSON.stringify(dismissals(context, initial))
  );
}

/** Field name → "✎ Edit #N · operator" for the latest edit that set it. */
export function humanSetLabels(edits: EditHistoryEntry[]): Record<string, string> {
  const labels: Record<string, string> = {};
  for (const { edit } of edits)
    for (const change of edit.field_changes) {
      const name = change.field_path.split(".").slice(1).join(".");
      labels[name] ??= `✎ Edit #${edit.edit_number} · ${edit.operator_name}`;
    }
  return labels;
}
```

- [ ] **Step 7: Run tests and type check**

Run: `cd apps/web && bun run test src/features/edits/editModel.test.ts && bun run typecheck && bun run test`
Expected: PASS, including every existing web test.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src
git commit -m "feat: add web edit contracts, API calls, copy and draft model"
```

---

### Task 16: Edit dialog

**Files:**
- Create: `apps/web/src/features/edits/EditDialog.tsx`, `apps/web/src/features/edits/editFixtures.ts`, `apps/web/src/features/edits/EditDialog.test.tsx`
- Modify: `apps/web/src/test/setup.ts` (dialog polyfill), `apps/web/src/styles/global.css`

**Interfaces:**
- Consumes: Task 15 model, calls and copy.
- Produces: `EditDialog({ reviewId, focusField?, onClose, onAppended })`. `onAppended(outcome: EditOutcome)` fires after a successful append, before `onClose`.

The approved layout is C, the comparison table ([mockup](../../../.superpowers/brainstorm/86906-1789763248/content/edit-popup-detail.html)).

- [ ] **Step 1: Polyfill `<dialog>` for jsdom**

Append to `apps/web/src/test/setup.ts`:

```ts
// jsdom lacks modal dialogs; browsers provide them natively.
if (!HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
}
```

- [ ] **Step 2: Write fixtures**

```ts
// apps/web/src/features/edits/editFixtures.ts
import type { EditContext, EditOutcome, EditPreview } from "../../api/contracts";

export const editContext: EditContext = {
  review_item_id: "review-1",
  editable: true,
  not_editable_reason: null,
  candidate_revision_id: "candidate-3",
  edit_number: 0,
  entity_type: "product",
  business_identifier: "SKU-2010",
  filename: "garden.csv",
  source_line_start: 41,
  source_line_end: 41,
  effective_state: "pending",
  fields: [
    { field_path: "product.category", state: "known", value: "Garden", source_text: "Garden", set_by_edit_number: null },
    { field_path: "product.stock_qty", state: "known", value: 0, source_text: "0", set_by_edit_number: null },
    { field_path: "product.listed_date", state: "known", value: "2024-01-01", source_text: "2024-01-01", set_by_edit_number: null },
    { field_path: "product.status", state: "known", value: "in_stock", source_text: "in stock", set_by_edit_number: null },
    { field_path: "product.notes", state: "absent", value: null, source_text: null, set_by_edit_number: null },
  ],
  checks: [
    { id: "vocab.product_category", kind: "vocabulary", label: "Category vocabulary", fields: ["product.category"], fired_fields: ["product.category"] },
    { id: "invariant.stock_status", kind: "invariant", label: "Stock and status consistency", fields: ["product.stock_qty"], fired_fields: ["product.stock_qty"] },
    { id: "format.sku", kind: "format", label: "Product identifier format", fields: ["product.sku"], fired_fields: [] },
  ],
  findings: [
    { kind: "duplicate", counterpart_id: "raw-7", detail: null, summary: "Duplicate of line 12" },
  ],
  current_reason: null,
  current_citations: [],
  current_dismissals: [],
};

export const datePreview: EditPreview = {
  changes: [
    {
      field_path: "product.listed_date",
      input_text: "03/04/2023",
      before: { state: "known", value: "2024-01-01" },
      after: { state: "known", value: "2023-03-04" },
    },
  ],
  verdict: "NEEDS_REVIEW",
  readiness: "ineligible",
  reasons: [
    { field_path: "product.category", summary: "Category 'Garden' is outside the registered vocabulary." },
  ],
};

export const appended: EditOutcome = {
  edit: {
    id: "edit-1",
    edit_number: 1,
    reason: "data_wrong",
    note: null,
    field_changes: datePreview.changes,
    citations: [],
    dismissals: [],
    operator_name: "Alexis",
    edited_at: {
      instant: "2026-09-18T13:07:31.123456Z",
      display: "18 September 2026, 14:07 BST",
      timezone: "Europe/London",
    },
    content_hash: "3f9a0c".padEnd(64, "0"),
    parent_hash: null,
    candidate_revision_id: "candidate-4",
    rules_version: "r".repeat(64),
  },
  terminal_revision_id: "candidate-5",
  verdict: "NEEDS_REVIEW",
  readiness: "ineligible",
  reasons: datePreview.reasons,
  legal_outcomes: ["approve", "reject"],
  replayed: false,
};

export const apiError = (status: number, code: string, details: Record<string, unknown> = {}) =>
  new Response(JSON.stringify({ error: { code, message: "server text", details } }), { status });
```

- [ ] **Step 3: Write the failing tests**

```tsx
// apps/web/src/features/edits/EditDialog.test.tsx
import { useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axe from "axe-core";
import { afterEach, expect, test, vi } from "vitest";
import { EditDialog } from "./EditDialog";
import { apiError, appended, datePreview, editContext } from "./editFixtures";

afterEach(() => vi.unstubAllGlobals());

type Handler = (url: string, init?: RequestInit) => Response | undefined;

function stub(handler: Handler = () => undefined) {
  const calls: { url: string; body: Record<string, unknown> | null }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
      const override = handler(url, init);
      if (override) return override;
      if (url === "/api/reviews/review-1/edit-context") return Response.json(editContext);
      if (url === "/api/reviews/review-1/edits/preview") return Response.json(datePreview);
      if (url === "/api/reviews/review-1/edits") return Response.json(appended, { status: 201 });
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  return calls;
}

function Harness({ onAppended = vi.fn() }: { onAppended?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Edit record</button>
      {open && <EditDialog reviewId="review-1" onClose={() => setOpen(false)} onAppended={onAppended} />}
    </>
  );
}

async function open() {
  const user = userEvent.setup();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Edit record" }));
  const dialog = await screen.findByRole("dialog", { name: "Edit SKU-2010" });
  return { user, dialog, view };
}

test("UI-19 reason changes the sections and gates append", async () => {
  stub();
  const { user, dialog } = await open();
  const append = within(dialog).getByRole("button", { name: "Append edit" });
  expect(within(dialog).queryByRole("group", { name: "Which rule is out of date?" })).toBeNull();
  expect(within(dialog).queryByRole("group", { name: "Cross-record findings on this version" })).toBeNull();
  expect(append).toBeDisabled();
  await user.type(within(dialog).getByLabelText("Operator name"), "Alexis");
  await user.click(within(dialog).getByRole("radio", { name: "Business rule updated" }));
  const rules = within(dialog).getByRole("group", { name: "Which rule is out of date?" });
  expect(within(rules).getAllByRole("checkbox")).toHaveLength(2);
  expect(append).toBeDisabled();
  await user.click(within(rules).getByRole("checkbox", { name: /Category vocabulary/ }));
  expect(within(rules).getByLabelText("Accepted value for Category vocabulary")).toHaveValue("Garden");
  expect(append).toBeEnabled();
  await user.click(within(dialog).getByRole("radio", { name: "Record links are wrong" }));
  expect(within(dialog).queryByRole("group", { name: "Which rule is out of date?" })).toBeNull();
  const findings = within(dialog).getByRole("group", { name: "Cross-record findings on this version" });
  expect(append).toBeDisabled();
  await user.click(within(findings).getByRole("checkbox", { name: /Duplicate of line 12/ }));
  expect(append).toBeEnabled();
});

test("UI-20 rows show source, current and live preview", async () => {
  stub();
  const { user, dialog } = await open();
  const row = within(dialog).getByRole("row", { name: /Listed date/ });
  expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual(
    expect.arrayContaining(["2024-01-01", "1 January 2024"]),
  );
  const input = within(row).getByLabelText("New Listed date");
  await user.clear(input);
  await user.type(input, "03/04/2023");
  expect(await within(row).findByText("→ 4 March 2023")).toBeVisible();
  expect(within(row).getByText("Changed")).toBeVisible();
  expect(await within(dialog).findByText(/1 issue remains/)).toBeVisible();
});

test("UI-21 focus stays inside, escape confirms, focus returns", async () => {
  stub();
  const { user, dialog } = await open();
  expect(dialog).toContainElement(document.activeElement as HTMLElement);
  for (let i = 0; i < 40; i++) {
    await user.tab();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
  }
  await user.type(within(dialog).getByLabelText("New Stock quantity"), "5");
  await user.keyboard("{Escape}");
  expect(within(dialog).getByText("Discard your changes?")).toBeVisible();
  await user.click(within(dialog).getByRole("button", { name: "Keep editing" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  await user.keyboard("{Escape}");
  await user.click(within(dialog).getByRole("button", { name: "Discard changes" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByRole("button", { name: "Edit record" })).toHaveFocus();
});

test("UI-22 stale edits keep input and refresh the record", async () => {
  let posts = 0;
  const calls = stub((url, init) =>
    url.endsWith("/edits") && init?.method === "POST" && ++posts === 1
      ? apiError(409, "edit_stale", { current_candidate_revision_id: "candidate-9", current_edit_number: 1 })
      : undefined,
  );
  const { user, dialog } = await open();
  await user.type(within(dialog).getByLabelText("Operator name"), "Alexis");
  const stock = within(dialog).getByLabelText("New Stock quantity");
  await user.clear(stock);
  await user.type(stock, "12");
  await user.click(within(dialog).getByRole("button", { name: "Append edit" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("This record changed since you opened it");
  expect(stock).toHaveValue("12");
  await waitFor(() =>
    expect(calls.filter((c) => c.url.endsWith("/edit-context")).length).toBeGreaterThan(1),
  );
});

test("UI-22 worker failures show code and reference and retry with the same key", async () => {
  let posts = 0;
  const calls = stub((url, init) =>
    url.endsWith("/edits") && init?.method === "POST" && ++posts === 1
      ? apiError(500, "edit_recheck_failed", { stage: "recheck", retryable: true, reference: "7c1e-a94b" })
      : undefined,
  );
  const { user, dialog } = await open();
  await user.type(within(dialog).getByLabelText("Operator name"), "Alexis");
  const stock = within(dialog).getByLabelText("New Stock quantity");
  await user.clear(stock);
  await user.type(stock, "12");
  await user.click(within(dialog).getByRole("button", { name: "Append edit" }));
  const alert = await within(dialog).findByRole("alert");
  expect(alert).toHaveTextContent("The re-check failed on the server. Nothing was changed.");
  expect(alert).toHaveTextContent("Code edit_recheck_failed · reference 7c1e-a94b");
  await user.click(within(alert).getByRole("button", { name: "Retry append" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  const keys = calls.filter((c) => c.url.endsWith("/edits")).map((c) => c.body?.idempotency_key);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});

test("UI-22 not-editable responses explain why", async () => {
  stub((url, init) =>
    url.endsWith("/edits") && init?.method === "POST"
      ? apiError(409, "edit_not_editable", { reason: "approved" })
      : undefined,
  );
  const { user, dialog } = await open();
  await user.type(within(dialog).getByLabelText("Operator name"), "Alexis");
  await user.type(within(dialog).getByLabelText("New Stock quantity"), "7");
  await user.click(within(dialog).getByRole("button", { name: "Append edit" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "Approved records must be reversed before editing.",
  );
  expect(within(dialog).queryByRole("button", { name: "Retry append" })).toBeNull();
});

test("UI-25 dialog states have no axe violations", async () => {
  stub((url, init) =>
    url.endsWith("/edits") && init?.method === "POST"
      ? apiError(500, "edit_persist_failed", { stage: "persist", retryable: true, reference: "ref" })
      : undefined,
  );
  const { user, dialog } = await open();
  const check = async () =>
    expect((await axe.run(dialog, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } })).violations).toEqual([]);
  await check();
  await user.click(within(dialog).getByRole("radio", { name: "Business rule updated" }));
  await check();
  await user.click(within(dialog).getByRole("radio", { name: "Record links are wrong" }));
  await check();
  await user.type(within(dialog).getByLabelText("Operator name"), "Alexis");
  await user.click(within(dialog).getByRole("checkbox", { name: /Duplicate of line 12/ }));
  await user.click(within(dialog).getByRole("button", { name: "Append edit" }));
  await within(dialog).findByRole("alert");
  await check();
});
```

- [ ] **Step 4: Run to verify failure**

Run: `cd apps/web && bun run test src/features/edits/EditDialog.test.tsx`
Expected: FAIL, cannot resolve `./EditDialog`.

- [ ] **Step 5: Implement `apps/web/src/features/edits/EditDialog.tsx`**

```tsx
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import type {
  EditCommand,
  EditContext,
  EditOutcome,
  EditPreview,
  EditPreviewCommand,
  EditReason,
} from "../../api/contracts";
import { appendEdit, previewEdit, useEditContext } from "../../api/queries";
import { displayLabel, editErrorText, notEditableText, retryableEditErrors } from "../../labels";
import {
  buildCommand,
  changedFields,
  formatValue,
  initialDraft,
  isDirty,
  pairingMet,
  type Draft,
} from "./editModel";

const REASONS: EditReason[] = ["data_wrong", "business_rule_updated", "graph_wrong"];
const HINTS: Record<EditReason, string> = {
  data_wrong: "Correct values that were wrong in the file. Every rule still applies.",
  business_rule_updated:
    "Name the rule that is out of date. Only the rules you choose are set aside, for this record only.",
  graph_wrong:
    "Change a reference field to re-point a link, or mark a cross-record finding as wrong.",
};
const OPERATOR_KEY = "operatorName";
const FOCUSABLE =
  'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), summary, [tabindex="0"]';

function rememberedOperator() {
  try {
    return localStorage.getItem(OPERATOR_KEY) ?? "";
  } catch {
    return "";
  }
}

function previewBody(command: EditCommand): EditPreviewCommand {
  return {
    expected_candidate_revision_id: command.expected_candidate_revision_id,
    reason: command.reason,
    note: command.note,
    fields: command.fields,
    citations: command.citations,
    dismissals: command.dismissals,
    operator_name: command.operator_name || "preview",
  };
}

interface Props {
  reviewId: string;
  focusField?: string | null;
  onClose: () => void;
  onAppended: (outcome: EditOutcome) => void;
}

export function EditDialog({ reviewId, focusField = null, onClose, onAppended }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(document.activeElement as HTMLElement | null);
  const context = useEditContext(reviewId);
  useEffect(() => {
    const node = dialog.current;
    if (node && !node.open) node.showModal();
    const returnTo = opener.current;
    return () => {
      node?.close();
      returnTo?.focus();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="edit-dialog"
      aria-labelledby="edit-heading"
      onCancel={(event) => event.preventDefault()}
    >
      {context.data ? (
        <EditForm
          context={context.data}
          focusField={focusField}
          onClose={onClose}
          onAppended={onAppended}
          onRefresh={() => void context.refetch()}
        />
      ) : context.isError ? (
        <div role="alert" className="edit-body">
          <h2 id="edit-heading">Record could not be loaded</h2>
          <p>{context.error.message}</p>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      ) : (
        <div role="status" className="edit-body">
          <h2 id="edit-heading">Loading record…</h2>
        </div>
      )}
    </dialog>
  );
}

interface FormProps {
  context: EditContext;
  focusField: string | null;
  onClose: () => void;
  onAppended: (outcome: EditOutcome) => void;
  onRefresh: () => void;
}

function EditForm({ context, focusField, onClose, onAppended, onRefresh }: FormProps) {
  const client = useQueryClient();
  const form = useRef<HTMLFormElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const keep = useRef<HTMLButtonElement>(null);
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null);
  const [draft, setDraft] = useState<Draft>(() => initialDraft(context, rememberedOperator()));
  const [showAll, setShowAll] = useState(false);
  const [showAllRules, setShowAllRules] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [preview, setPreview] = useState<EditPreview | null>(null);
  const [error, setError] = useState<{ code: string; message: string; reference?: string } | null>(null);
  const changed = new Set(changedFields(context, draft).map((f) => f.field_path));
  const ready = pairingMet(context, draft);

  useEffect(() => {
    const target = focusField
      ? form.current?.querySelector<HTMLInputElement>(`[data-field="${focusField}"]`)
      : null;
    (target ?? heading.current)?.focus();
  }, []);
  useEffect(() => {
    if (confirming) keep.current?.focus();
  }, [confirming]);

  const previewCommand = ready ? previewBody(buildCommand(context, draft, "preview")) : null;
  const previewKey = previewCommand ? JSON.stringify(previewCommand) : "";
  useEffect(() => {
    if (!previewCommand) {
      setPreview(null);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      previewEdit(context.review_item_id, previewCommand, controller.signal).then(
        setPreview,
        () => setPreview(null),
      );
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [previewKey]);

  const mutation = useMutation({
    mutationFn: (command: EditCommand) => appendEdit(context.review_item_id, command),
    onSuccess: async (outcome) => {
      attempt.current = null;
      try {
        localStorage.setItem(OPERATOR_KEY, draft.operator.trim());
      } catch {
        // Remembering the operator is a convenience only.
      }
      await Promise.all(
        ["workspace", "run", "reviews", "review", "edit-context"].map((key) =>
          client.invalidateQueries({ queryKey: [key] }),
        ),
      );
      onAppended(outcome);
      onClose();
    },
    onError: (failure) => {
      if (!(failure instanceof ApiError)) {
        setError({ code: "network_error", message: editErrorText("network_error") });
        return;
      }
      const reference = failure.details.reference;
      const message =
        failure.code === "edit_not_editable"
          ? notEditableText(String(failure.details.reason ?? ""))
          : editErrorText(failure.code);
      setError({
        code: failure.code,
        message,
        reference: typeof reference === "string" ? reference : undefined,
      });
      if (!retryableEditErrors.has(failure.code)) attempt.current = null;
      if (failure.code === "edit_stale") onRefresh();
    },
  });
  const canAppend = ready && draft.operator.trim() !== "" && !mutation.isPending;

  function update(next: Partial<Draft>) {
    setDraft((current) => ({ ...current, ...next }));
    setError(null);
  }
  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!canAppend) return;
    const fingerprint = JSON.stringify(buildCommand(context, draft, ""));
    if (attempt.current?.fingerprint !== fingerprint)
      attempt.current = { fingerprint, key: crypto.randomUUID() };
    setError(null);
    mutation.mutate(buildCommand(context, draft, attempt.current.key));
  }
  function requestClose() {
    if (isDirty(context, draft)) setConfirming(true);
    else onClose();
  }
  function keyDown(event: KeyboardEvent<HTMLFormElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      requestClose();
      return;
    }
    if (event.key !== "Tab") return;
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (!items.length) return;
    const index = items.indexOf(document.activeElement as HTMLElement);
    if (event.shiftKey && index <= 0) {
      event.preventDefault();
      items[items.length - 1].focus();
    } else if (!event.shiftKey && index === items.length - 1) {
      event.preventDefault();
      items[0].focus();
    }
  }

  const lines =
    context.source_line_start === context.source_line_end
      ? `line ${context.source_line_start}`
      : `lines ${context.source_line_start}–${context.source_line_end}`;
  const visible = context.fields.filter(
    (f) =>
      showAll ||
      f.state !== "absent" ||
      f.source_text ||
      changed.has(f.field_path) ||
      f.field_path === focusField,
  );
  const checkRows = context.checks.flatMap((check) =>
    (showAllRules ? check.fields : check.fired_fields).map((field) => ({ check, field })),
  );
  const nameOf = (path: string) => displayLabel(path.split(".").slice(1).join("."));

  if (!context.editable)
    return (
      <div className="edit-body" role="alert">
        <h2 id="edit-heading" ref={heading} tabIndex={-1}>
          Edit {context.business_identifier ?? "record"}
        </h2>
        <p>{notEditableText(context.not_editable_reason)}</p>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    );

  return (
    <form ref={form} className="edit-body" noValidate onSubmit={submit} onKeyDown={keyDown}>
      <header className="edit-header">
        <h2 id="edit-heading" ref={heading} tabIndex={-1}>
          Edit {context.business_identifier ?? "record"}
        </h2>
        <p className="muted">
          {context.filename ?? "Unknown file"} · {lines} · {displayLabel(context.effective_state)}
        </p>
      </header>

      <fieldset className="edit-reasons">
        <legend>Why are you editing?</legend>
        <div className="segments">
          {REASONS.map((reason) => (
            <label key={reason} className="segment">
              <input
                type="radio"
                name="edit-reason"
                value={reason}
                checked={draft.reason === reason}
                onChange={() => update({ reason })}
              />
              {displayLabel(reason)}
            </label>
          ))}
        </div>
        <p className="muted">{HINTS[draft.reason]}</p>
      </fieldset>

      <table className="edit-table">
        <caption className="sr-only">Fields of this record</caption>
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">Source text</th>
            <th scope="col">Current</th>
            <th scope="col">New value</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((field) => {
            const name = nameOf(field.field_path);
            const value = draft.values[field.field_path];
            const isChanged = changed.has(field.field_path);
            const after = preview?.changes.find((c) => c.field_path === field.field_path);
            const problems = preview?.reasons.filter((r) => r.field_path === field.field_path) ?? [];
            return (
              <tr key={field.field_path} className={isChanged ? "changed" : undefined}>
                <th scope="row">
                  {name}
                  {isChanged && <span className="tag">Changed</span>}
                  {field.set_by_edit_number !== null && (
                    <span className="tag">✎ Edit #{field.set_by_edit_number}</span>
                  )}
                </th>
                <td>
                  <span className="exact-value">{field.source_text ?? "(none)"}</span>
                </td>
                <td>{formatValue({ state: field.state, value: field.value })}</td>
                <td>
                  <input
                    data-field={field.field_path}
                    aria-label={`New ${name}`}
                    value={value.text}
                    disabled={value.absent}
                    onChange={(e) =>
                      update({
                        values: { ...draft.values, [field.field_path]: { text: e.target.value, absent: false } },
                      })
                    }
                  />
                  <label className="inline">
                    <input
                      type="checkbox"
                      checked={value.absent}
                      onChange={(e) =>
                        update({
                          values: {
                            ...draft.values,
                            [field.field_path]: { text: value.text, absent: e.target.checked },
                          },
                        })
                      }
                    />
                    Mark empty
                  </label>
                  {after && <p className="row-result">→ {formatValue(after.after)}</p>}
                  {problems.map((problem, i) => (
                    <p key={i} className="row-problem">
                      ▲ {problem.summary}
                    </p>
                  ))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!showAll && visible.length < context.fields.length && (
        <button type="button" className="link-button" onClick={() => setShowAll(true)}>
          Show all fields ({context.fields.length})
        </button>
      )}

      {draft.reason === "business_rule_updated" && (
        <fieldset className="edit-checks">
          <legend>Which rule is out of date?</legend>
          {checkRows.length === 0 && <p>No rule flagged this record.</p>}
          {checkRows.map(({ check, field }) => {
            const key = `${check.id}|${field}`;
            const state = draft.citations[key] ?? { checked: false, accepted: "" };
            const fired = check.fired_fields.includes(field);
            return (
              <div key={key} className="check-row">
                <label>
                  <input
                    type="checkbox"
                    checked={state.checked}
                    onChange={(e) =>
                      update({
                        citations: {
                          ...draft.citations,
                          [key]: {
                            checked: e.target.checked,
                            accepted: state.accepted || draft.values[field]?.text || "",
                          },
                        },
                      })
                    }
                  />
                  {check.label}
                  {check.fields.length > 1 ? ` · ${nameOf(field)}` : ""}
                  {fired && <span className="muted"> · flagged on this record</span>}
                </label>
                {state.checked && check.kind === "vocabulary" && (
                  <input
                    aria-label={`Accepted value for ${check.label}`}
                    value={state.accepted}
                    onChange={(e) =>
                      update({ citations: { ...draft.citations, [key]: { checked: true, accepted: e.target.value } } })
                    }
                  />
                )}
              </div>
            );
          })}
          {!showAllRules && (
            <button type="button" className="link-button" onClick={() => setShowAllRules(true)}>
              Show all rules
            </button>
          )}
        </fieldset>
      )}

      {draft.reason === "graph_wrong" && (
        <fieldset className="edit-findings">
          <legend>Cross-record findings on this version</legend>
          {context.findings.length === 0 && (
            <p>No cross-record findings. Edit a reference field to re-point a link.</p>
          )}
          {context.findings.map((finding) => {
            const key = `${finding.kind}|${finding.counterpart_id}`;
            return (
              <label key={key} className="finding-row">
                <input
                  type="checkbox"
                  checked={!!draft.dismissals[key]}
                  onChange={(e) =>
                    update({ dismissals: { ...draft.dismissals, [key]: e.target.checked } })
                  }
                />
                {finding.summary} <span className="muted">· mark as wrong</span>
              </label>
            );
          })}
        </fieldset>
      )}

      <section className="edit-result" aria-live="polite">
        <h3>After append</h3>
        <p>
          {preview
            ? `${displayLabel(preview.verdict)} · ${
                preview.reasons.length === 0
                  ? "No issues remain"
                  : `${preview.reasons.length} ${preview.reasons.length === 1 ? "issue remains" : "issues remain"}`
              } · Still needs approval`
            : ready
              ? "Checking the edited record…"
              : "Make a change to see the result."}
        </p>
      </section>

      <div className="edit-actor">
        <label>
          Operator name
          <input value={draft.operator} onChange={(e) => update({ operator: e.target.value })} />
        </label>
        <label>
          Note
          <textarea rows={2} maxLength={2000} value={draft.note} onChange={(e) => update({ note: e.target.value })} />
        </label>
      </div>

      {error && (
        <div role="alert" className="error-message">
          <p>
            <strong>Edit was not appended.</strong> {error.message}
          </p>
          {error.reference && (
            <p className="small">
              Code {error.code} · reference {error.reference}
            </p>
          )}
          {retryableEditErrors.has(error.code) && (
            <button type="button" onClick={() => submit()}>
              Retry append
            </button>
          )}
        </div>
      )}

      {confirming && (
        <div className="discard-confirm" role="group" aria-labelledby="discard-heading">
          <p id="discard-heading">Discard your changes?</p>
          <button
            type="button"
            ref={keep}
            onClick={() => {
              setConfirming(false);
              heading.current?.focus();
            }}
          >
            Keep editing
          </button>
          <button type="button" onClick={onClose}>
            Discard changes
          </button>
        </div>
      )}

      <footer className="edit-footer">
        <p className="muted">
          Appending adds a new version as edit #{context.edit_number + 1}; earlier versions never change.
        </p>
        <button type="button" onClick={requestClose}>
          Cancel
        </button>
        <button type="submit" className="primary" disabled={!canAppend}>
          {mutation.isPending ? "Appending…" : "Append edit"}
        </button>
      </footer>
    </form>
  );
}
```

`previewKey` intentionally drives the preview effect; the command object is rebuilt every render.

- [ ] **Step 6: Add styles to `apps/web/src/styles/global.css`**

```css
.edit-dialog {
  width: min(960px, calc(100vw - 32px));
  max-height: calc(100vh - 32px);
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-panel);
  background: var(--surface);
  color: var(--ink);
}
.edit-dialog::backdrop {
  background: rgb(0 0 0 / 0.35);
}
.edit-body {
  display: grid;
  gap: 16px;
  padding: 16px;
}
.edit-header p,
.edit-footer p {
  margin: 0;
}
.segments {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.segment {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: 999px;
}
.segment:has(input:checked) {
  border-color: var(--ink);
  font-weight: 600;
}
.edit-table {
  width: 100%;
  border-collapse: collapse;
}
.edit-table th,
.edit-table td {
  padding: 8px;
  border-bottom: 1px dashed var(--border);
  text-align: left;
  vertical-align: top;
}
.edit-table tr.changed > * {
  background: var(--field);
}
.edit-table input:not([type="checkbox"]) {
  width: 100%;
  min-height: 32px;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--field);
}
.edit-table .tag {
  margin-left: 6px;
}
.row-result {
  margin: 4px 0 0;
  color: var(--green);
}
.row-problem {
  margin: 4px 0 0;
  color: var(--amber);
}
.check-row,
.finding-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-height: 32px;
}
.edit-footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
.edit-footer p {
  flex: 1 1 240px;
}
.discard-confirm {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--amber);
  border-radius: var(--radius-control);
}
.link-button {
  justify-self: start;
}
@media (max-width: 640px) {
  .edit-table thead {
    display: none;
  }
  .edit-table,
  .edit-table tbody,
  .edit-table tr,
  .edit-table th,
  .edit-table td {
    display: block;
    width: 100%;
  }
  .edit-table tr {
    margin-bottom: 8px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
  }
}
@media (prefers-reduced-motion: reduce) {
  .edit-dialog,
  .edit-dialog::backdrop {
    transition: none;
    animation: none;
  }
}
```

If `.tag` or `.small` are not defined in `global.css`, add `.tag { display: inline-block; padding: 0 8px; border: 1px solid var(--border); border-radius: 999px; font-size: 12px; }` and `.small { font-size: 12px; }`.

- [ ] **Step 7: Run tests and type check**

Run: `cd apps/web && bun run test src/features/edits && bun run typecheck`
Expected: PASS. If `user.tab()` in UI-21 leaves the dialog in jsdom, check that the `keyDown` trap handles `index === -1` (focus on the heading) and fix the trap, not the test.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src
git commit -m "feat: add comparison-table edit dialog with preview and error states"
```

---

### Task 17: Edit history and review-detail integration

**Files:**
- Create: `apps/web/src/features/edits/EditHistory.tsx`, `apps/web/src/features/edits/EditHistory.test.tsx`
- Modify: `apps/web/src/features/reviews/ReviewDetail.tsx`, `apps/web/src/features/runs/EvidencePanel.tsx`, `apps/web/src/styles/global.css`

**Interfaces:**
- Consumes: `EditDialog` (Task 16), `humanSetLabels`, `formatValue` (Task 15), `ReviewDetailView.edits/editable/not_editable_reason/latest_edit_failure`.
- Produces: `EditHistory({ edits })`; `EvidencePanel` gains optional `humanSet?: Record<string, string>` and `onEditField?: (fieldName: string) => void`.

- [ ] **Step 1: Write the failing tests**

```tsx
// apps/web/src/features/edits/EditHistory.test.tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, expect, test, vi } from "vitest";
import type { EditHistoryEntry } from "../../api/contracts";
import { run } from "../../test/fixtures";
import { detail, fakeWorkflow, renderWorkflow } from "../../test/workflow";
import { EditHistory } from "./EditHistory";
import { appended, editContext } from "./editFixtures";

afterEach(() => vi.unstubAllGlobals());

const second = {
  ...appended.edit,
  id: "edit-2",
  edit_number: 2,
  reason: "business_rule_updated" as const,
  field_changes: [],
  citations: [{ check_id: "vocab.product_category", field_path: "product.category", accepted_value: "Garden" }],
  note: "Garden range approved",
  content_hash: "a1b2c3".padEnd(60, "0") + "d21e",
  parent_hash: appended.edit.content_hash,
  edited_at: { instant: "2026-09-18T13:20:00Z", display: "18 September 2026, 14:20 BST", timezone: "Europe/London" },
};
const history: EditHistoryEntry[] = [
  { edit: second, overridden_by_edit_number: null, reason_label: "Business rule updated" },
  { edit: appended.edit, overridden_by_edit_number: 2, reason_label: "Data is wrong" },
];

test("UI-23 history shows current and overridden edits with absolute times and hashes", async () => {
  const { container } = render(<EditHistory edits={history} />);
  const items = screen.getAllByRole("listitem", { name: /Edit #/ });
  expect(items).toHaveLength(2);
  expect(items[0]).toHaveTextContent("Edit #2");
  expect(items[0]).toHaveTextContent("Current");
  expect(items[0]).toHaveTextContent("Business rule updated · Category vocabulary · accepted Garden");
  expect(items[0]).toHaveTextContent("“Garden range approved”");
  expect(items[1]).toHaveTextContent("Overridden by edit #2");
  expect(items[1]).toHaveTextContent("Listed date: 1 January 2024 → 4 March 2023");
  expect(items[1]).toHaveTextContent("18 September 2026, 14:07 BST");
  expect(container.textContent).not.toMatch(/ago|just now|today|yesterday/i);
  expect(items[0]).toHaveTextContent("Hash a1b2c3…d21e · follows 3f9a0c…0000");
  await userEvent.setup().click(within(items[0]).getByText(/^Hash/));
  expect(within(items[0]).getByText(second.content_hash)).toBeVisible();
  expect((await axe.run(container)).violations).toEqual([]);
});

test("UI-24 edit button is disabled with a reason for approved records", async () => {
  fakeWorkflow((url) =>
    url === "/api/reviews/review-1"
      ? Response.json({ ...detail, editable: false, not_editable_reason: "approved" })
      : undefined,
  );
  await renderWorkflow(`/reviews?scope=current&run_id=${run.id}&review=review-1`);
  const button = await screen.findByRole("button", { name: "Edit record" });
  expect(button).toBeDisabled();
  expect(screen.getByText("Approved records must be reversed before editing.")).toBeVisible();
});

test("appending from the review detail announces the edit", async () => {
  fakeWorkflow((url) => {
    if (url === "/api/reviews/review-1/edit-context") return Response.json(editContext);
    if (url === "/api/reviews/review-1/edits/preview") return Response.json({ changes: [], verdict: "NEEDS_REVIEW", readiness: "ineligible", reasons: [] });
    if (url === "/api/reviews/review-1/edits") return Response.json(appended, { status: 201 });
    return undefined;
  });
  const user = userEvent.setup();
  await renderWorkflow(`/reviews?scope=current&run_id=${run.id}&review=review-1`);
  await user.click(await screen.findByRole("button", { name: "Edit record" }));
  const dialog = await screen.findByRole("dialog", { name: "Edit SKU-2010" });
  await user.type(within(dialog).getByLabelText("Operator name"), "Alexis");
  await user.type(within(dialog).getByLabelText("New Stock quantity"), "7");
  await user.click(within(dialog).getByRole("button", { name: "Append edit" }));
  expect(
    await screen.findByText("Edit #1 appended · 18 September 2026, 14:07 BST"),
  ).toBeVisible();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Edit record" })).toHaveFocus();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/web && bun run test src/features/edits/EditHistory.test.tsx`
Expected: FAIL, cannot resolve `./EditHistory`.

- [ ] **Step 3: Implement `apps/web/src/features/edits/EditHistory.tsx`**

```tsx
import type { Dismissal, EditHistoryEntry } from "../../api/contracts";
import { displayLabel } from "../../labels";
import { formatValue } from "./editModel";

const short = (hash: string) => `${hash.slice(0, 6)}…${hash.slice(-4)}`;
const fieldName = (path: string) => displayLabel(path.split(".").slice(1).join("."));
const dismissalText = (d: Dismissal) =>
  d.kind === "duplicate"
    ? "duplicate link"
    : d.kind === "conflict"
      ? "business ID conflict"
      : `${d.detail ?? "record"} link`;

export function EditHistory({ edits }: { edits: EditHistoryEntry[] }) {
  if (!edits.length) return null;
  return (
    <section className="edit-history" aria-labelledby="edit-history-heading">
      <h3 id="edit-history-heading">Edits</h3>
      <p className="muted">
        {edits.length} {edits.length === 1 ? "edit" : "edits"} · current: edit #
        {edits[0].edit.edit_number}
      </p>
      <ol className="edit-timeline">
        {edits.map(({ edit, overridden_by_edit_number: overriddenBy }) => (
          <li
            key={edit.id}
            aria-labelledby={`edit-${edit.id}`}
            className={overriddenBy ? "overridden" : undefined}
          >
            <h4 id={`edit-${edit.id}`}>Edit #{edit.edit_number}</h4>
            <span className="tag">
              {overriddenBy ? `Overridden by edit #${overriddenBy}` : "Current"}
            </span>{" "}
            <span className="tag">{displayLabel(edit.reason)}</span>
            <p className="muted">
              {edit.operator_name} ·{" "}
              <time dateTime={edit.edited_at.instant} title={edit.edited_at.instant}>
                {edit.edited_at.display}
              </time>
            </p>
            {edit.field_changes.length > 0 && (
              <ul>
                {edit.field_changes.map((change) => (
                  <li key={change.field_path}>
                    {fieldName(change.field_path)}: {formatValue(change.before)} →{" "}
                    <strong>{formatValue(change.after)}</strong>
                  </li>
                ))}
              </ul>
            )}
            {edit.citations.map((c) => (
              <p key={`${c.check_id}|${c.field_path}|${c.accepted_value}`}>
                Business rule updated · {displayLabel(c.check_id)}
                {c.accepted_value ? ` · accepted ${c.accepted_value}` : ""}
              </p>
            ))}
            {edit.dismissals.map((d) => (
              <p key={`${d.kind}|${d.counterpart_id}`}>Marked wrong: {dismissalText(d)}</p>
            ))}
            {edit.note && <p className="muted">“{edit.note}”</p>}
            <details>
              <summary>
                Hash {short(edit.content_hash)}
                {edit.parent_hash ? ` · follows ${short(edit.parent_hash)}` : " · first edit"}
              </summary>
              <p className="exact-value">{edit.content_hash}</p>
              {edit.parent_hash && <p className="exact-value">{edit.parent_hash}</p>}
            </details>
          </li>
        ))}
      </ol>
    </section>
  );
}
```

The history list items are named by their heading (`aria-labelledby`), which is what the test's `name: /Edit #/` relies on; the nested change lists are unnamed.

- [ ] **Step 4: Show edit provenance in `EvidencePanel.tsx`**

Add the optional props:

```tsx
export function EvidencePanel({
  evidence,
  rawId,
  candidateId,
  humanSet = {},
  onEditField,
}: {
  evidence: EvidenceView;
  rawId: string;
  candidateId: string | null;
  humanSet?: Record<string, string>;
  onEditField?: (fieldName: string) => void;
}) {
```

Inside each interpreted field's `<dd>`, immediately after `<span>{displayLabel(field.state)}</span>`:

```tsx
                            {humanSet[key] && <span className="tag">{humanSet[key]}</span>}
                            {onEditField && (
                              <button
                                type="button"
                                className="link-button"
                                aria-label={`Edit ${displayLabel(key)}`}
                                onClick={() => onEditField(key)}
                              >
                                Edit
                              </button>
                            )}
```

- [ ] **Step 5: Wire `ReviewDetail.tsx`**

Add imports:

```tsx
import { useState } from "react";
import { notEditableText } from "../../labels";
import { EditDialog } from "../edits/EditDialog";
import { EditHistory } from "../edits/EditHistory";
import { humanSetLabels } from "../edits/editModel";
```

(Merge `useState` into the existing `react` import.) At the top of the component, after `const heading = ...`:

```tsx
  const [editing, setEditing] = useState<{ focusField: string | null } | null>(null);
  const [announcement, setAnnouncement] = useState("");
```

Replace the block from `<p className="small">Decision sequence: ...` to the closing `<EvidencePanel ... />` with:

```tsx
      <p className="small">Decision sequence: {item.decision_sequence}</p>
      <div className="edit-actions">
        <button
          type="button"
          onClick={() => setEditing({ focusField: null })}
          disabled={!detail.editable || query.isFetching}
          aria-describedby={detail.editable ? undefined : "edit-disabled-reason"}
        >
          Edit record
        </button>
        {!detail.editable && (
          <p className="muted" id="edit-disabled-reason">
            {notEditableText(detail.not_editable_reason)}
          </p>
        )}
      </div>
      {announcement && (
        <p role="status" className="notice">
          {announcement}
        </p>
      )}
      {detail.latest_edit_failure && (
        <p className="error-message">
          The last edit attempt failed during {detail.latest_edit_failure.stage}. Nothing was
          changed. Code {detail.latest_edit_failure.code} · reference{" "}
          {detail.latest_edit_failure.reference}
        </p>
      )}
      <DecisionForm detail={detail} disabled={query.isError || query.isFetching} />
      <DecisionHistory decisions={detail.decisions} />
      <EditHistory edits={detail.edits} />
      <EvidencePanel
        evidence={detail.evidence}
        rawId={item.raw_record_id}
        candidateId={item.candidate_revision_id}
        humanSet={humanSetLabels(detail.edits)}
        onEditField={
          detail.editable
            ? (name) =>
                setEditing({ focusField: `${(item.entity_type ?? "").toLowerCase()}.${name}` })
            : undefined
        }
      />
      {editing && (
        <EditDialog
          reviewId={item.id}
          focusField={editing.focusField}
          onClose={() => setEditing(null)}
          onAppended={(outcome) =>
            setAnnouncement(
              `Edit #${outcome.edit.edit_number} appended · ${outcome.edit.edited_at.display}`,
            )
          }
        />
      )}
```

Add to `global.css`:

```css
.edit-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.edit-timeline {
  margin: 8px 0 12px 4px;
  padding-left: 12px;
  border-left: 2px solid var(--border);
  list-style: none;
}
.edit-timeline > li {
  margin-bottom: 12px;
}
.edit-timeline > li.overridden {
  color: var(--muted);
}
.edit-timeline h4 {
  display: inline;
  margin-right: 6px;
}
```

The overridden entry is muted with color *and* its "Overridden by edit #N" text, so color is never the only signal.

- [ ] **Step 6: Run the full web suite**

Run: `cd apps/web && bun run test && bun run typecheck && bun run build`
Expected: PASS, including existing `ReviewPage`, `RunPage`, and `WorkspacePage` tests (their fixtures gained `editable: true` in Task 15, so "Edit record" is enabled there and nothing else changes).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src
git commit -m "feat: show edit history, provenance and edit entry points on records"
```

---

### Task 18: Browser acceptance (E2E-02)

**Files:**
- Modify: `apps/web/e2e/operator-flow.spec.ts` (add a second test; reuse the file's `accessibility` helper)

**Interfaces:**
- Consumes: the whole feature through the real API, PostgreSQL, and filesystem source storage started by `tests/support/browser_server.py`.

- [ ] **Step 1: Write the acceptance test**

Append to `apps/web/e2e/operator-flow.spec.ts` (add `import { writeFileSync } from "node:fs";` and `ReviewDetailView` is already imported):

```ts
// Edit -> re-append (override) -> stale second tab refused -> approve -> canonical equals edit 2.
test("operator edits, re-appends, and approves an edited record", async ({ page, context, request }, testInfo) => {
  const csv = testInfo.outputPath("garden.csv");
  writeFileSync(
    csv,
    "record_type,id,name,contact_or_sku,value,quantity,date,status,tags,notes\n" +
      "CUSTOMER,CUST-7001,Ana Silva,ana@example.org,100,,2024-01-01,active,vip,\n" +
      "PRODUCT,SKU-7001,Hose,Garden,12.50,5,2024-01-01,in_stock,home,\n",
  );
  await page.goto("/");
  await page.getByLabel("CSV file").setInputFiles(csv);
  await page.getByLabel("Operator name").fill("Alex");
  const upload = page.waitForResponse(
    (r) => r.url().endsWith("/api/uploads") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Upload and process" }).click();
  const runId: string = (await (await upload).json()).run_id;
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
  const queue: ReviewQueueView = await (
    await request.get(`/api/reviews?scope=current&run_id=${runId}`)
  ).json();
  const item = queue.items.find((row) => row.business_identifier === "SKU-7001")!;
  const url = `/reviews?scope=current&run_id=${runId}&review=${item.id}`;

  const stale = await context.newPage();
  await stale.goto(url);
  await stale.getByRole("button", { name: "Edit record" }).click();
  const staleDialog = stale.getByRole("dialog", { name: "Edit SKU-7001" });
  await staleDialog.getByLabel("Operator name").fill("Sam");
  await staleDialog.getByLabel("New Stock quantity").fill("9");

  await page.goto(url);
  await page.getByRole("button", { name: "Edit record" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit SKU-7001" });
  await accessibility(page);
  await dialog.getByLabel("Operator name").fill("Alex");
  await dialog.getByLabel("New Stock quantity").fill("7");
  await expect(dialog.getByText("→ 7")).toBeVisible();
  await dialog.getByRole("button", { name: "Append edit" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Edit #1 appended ·" })).toBeVisible();

  await page.getByRole("button", { name: "Edit record" }).click();
  await dialog.getByRole("radio", { name: "Business rule updated" }).check();
  await dialog.getByRole("checkbox", { name: /Category vocabulary/ }).check();
  await expect(dialog.getByLabel("Accepted value for Category vocabulary")).toHaveValue("Garden");
  await expect(dialog.getByText(/No issues remain/)).toBeVisible();
  await dialog.getByRole("button", { name: "Append edit" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Edit #2 appended ·" })).toBeVisible();

  const history = page.getByRole("region", { name: "Edits" });
  await expect(history.getByRole("listitem", { name: "Edit #2" })).toContainText("Current");
  await expect(history.getByRole("listitem", { name: "Edit #1" })).toContainText(
    "Overridden by edit #2",
  );
  await expect(history).toContainText("Stock quantity: 5 → 7");

  await staleDialog.getByRole("button", { name: "Append edit" }).click();
  await expect(staleDialog.getByRole("alert")).toContainText(
    "This record changed since you opened it",
  );
  await expect(staleDialog.getByLabel("New Stock quantity")).toHaveValue("9");
  const afterStale: ReviewDetailView = await (await request.get(`/api/reviews/${item.id}`)).json();
  expect(afterStale.edits).toHaveLength(2);
  await stale.close();

  await page.locator(".decision-form").getByLabel("Operator name").fill("Alex");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Decision saved. Approved." }),
  ).toBeVisible();

  const approved: ReviewDetailView = await (await request.get(`/api/reviews/${item.id}`)).json();
  expect(approved.item.canonical_effect).toBe("current");
  const canonical = approved.evidence.nodes.find(
    (n) => n.kind === "canonical_revision" && n.id === approved.item.canonical_revision_id,
  )!;
  expect(canonical.attributes.candidate_revision_id).toBe(approved.item.candidate_revision_id);
  const terminal = approved.evidence.nodes.find((n) => n.id === approved.item.candidate_revision_id)!;
  const payload = terminal.attributes.payload as Record<string, { value: unknown }>;
  expect(payload.stock_qty.value).toBe(7);
  expect(payload.category.value).toBe("Garden");
  expect(approved.editable).toBe(false);
  await accessibility(page);
  await page.screenshot({ path: testInfo.outputPath("edited-and-approved.png"), fullPage: true });
});
```

- [ ] **Step 2: Run the acceptance suite**

Run (from the repository root, with ports 8000 and 5173 free): `cd apps/web && bunx playwright test e2e/operator-flow.spec.ts`
Expected: both tests PASS. On failure, open the retained trace in `apps/web/test-results/` before changing code.

- [ ] **Step 3: Commit**

```bash
git add apps/web/e2e/operator-flow.spec.ts
git commit -m "test: prove edit, override, stale refusal and approval end to end"
```

---

### Task 19: Documentation and quality gate

**Files:**
- Modify: `docs/ARCHITECTURE.md`, `TODO.md`, `README.md`, `docs/superpowers/specs/2026-09-18-human-field-editing-design.md` (status line)

- [ ] **Step 1: Amend `docs/ARCHITECTURE.md`** (it is the authority, so the feature is not done until it says so)

- §2 *Candidate revisions*: add "An operator edit appends a `HUMAN_EDIT` revision tied to its run; later derived revisions (FX) may follow it."
- §3.6 *Review and decide*: add the effective-state rule (a decision governs only the version it was made on), `legal_outcomes(verdict, edited)`, terminal-following dependency readiness, and "the cascade never promotes an edited record".
- New §3.7 *Human field editing*: summarise spec §§4–8 in one paragraph each (data model, check catalogue, append transaction, re-check, decisions) and link the spec.
- §6 ownership table: rows for `human_edit` (edit command service, append-only), `review_assessment` (edit command service and classify, append-only; latest is current), `edit_attempt_failure` (edit command service, append-only).
- §7.1 API table: the three edit endpoints; note the error codes table in spec §9.2.
- §8: a subsection for the edit dialog (layout C), history, provenance labels and entry points.
- §11: add a subsection "11.12 Human field editing" listing the test IDs `EDT-01`…`EDT-41`, `API-12`…`API-14`, `ERR-01`, `ERR-02`, `UI-19`…`UI-25`, `E2E-02` with a pointer to spec §11 for their required assertions.

- [ ] **Step 2: Update `TODO.md` and `README.md`**

In `TODO.md`, delete "Add human editing through append-only candidate revisions; do not mutate source or existing revisions." Under *Configurable business rules* add: "Use stored edit citations (`human_edit.citations`) as the evidence base when proposing catalogue changes."

In `README.md` *Operator flow*, add after step 4: "To correct a record under review, choose **Edit record**, pick why (data wrong, business rule updated, record links wrong), change values in the comparison table, and **Append edit**. Each edit is timestamped, hashed and kept; a later edit overrides it without overwriting it. The record is re-checked and still needs an approval."

- [ ] **Step 3: Run the complete quality gate**

Run from the repository root with the app stopped: `./scripts/quality-gate.sh`
Expected: every stage passes (pytest, Ruff, strict Pyright, Vitest, TypeScript, production build, Playwright). Record the new totals.

- [ ] **Step 4: Record results**

Replace the test counts in `README.md` *Verification* and `docs/ARCHITECTURE.md` §11.11 with the totals from Step 3 and today's date. Change the spec's status line to "Implemented and verified on <date>".

- [ ] **Step 5: Commit**

```bash
git add docs README.md TODO.md
git commit -m "docs: record human field editing in architecture, backlog and README"
```

---

## Spec coverage

| Spec section | Task |
|---|---|
| §4.1 `human_edit`, pairing, fingerprint | 1, 2, 8, 9 |
| §4.2 `HUMAN_EDIT` revisions and transformations | 3, 5 |
| §4.3 `review_assessment` and backfill; classify writes assessment 1 | 2, 8 |
| §4.4 `check_id` and backfill | 2, 3 |
| §4.5 `edit_attempt_failure` | 2, 8, 10 |
| §5 catalogue; status vocabularies | 3, 5, 7 |
| §6.1–6.2 append transaction | 9 |
| §6.3 hash chain and verification | 1, 9 |
| §6.4 override | 7, 9, 12 |
| §7.1–7.4 graph, isolation, context, repairs, FX | 6, 7, 9 |
| §7.5 waivers | 7 |
| §7.6 dismissals | 7, 9 |
| §7.7 verdict and result | 7 |
| §7.8 symmetric conflicts | 7 |
| §8 effective state, legal outcomes, readiness, cascade | 4, 11, 12 |
| §9.1 endpoints and projections | 12, 13 |
| §9.2 error codes | 9, 10, 13, 14 |
| §10 interface | 15, 16, 17 |
| §11 test contract | every task; E2E in 18 |
| §14 documentation | 19 |
