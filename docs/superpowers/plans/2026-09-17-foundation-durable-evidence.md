# Foundation and Durable Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the typed Python foundation, Postgres persistence, content-addressed source storage, exact-file ingest deduplication, immutable parsing, and checkpoint recovery.

**Architecture:** Domain and application code remain independent of FastAPI and SQLAlchemy. Infrastructure implements repository, transaction, clock, and source-store ports. Ingest serializes on the source hash so repeated exact bytes preserve occurrences while sharing one current run; parsing persists immutable logical records in checkpointed batches.

**Tech Stack:** Python 3.12, uv, pytest, SQLAlchemy 2, Alembic, PostgreSQL 16, Typer, Docker Compose

**Spec:** [`docs/superpowers/specs/2026-09-16-local-csv-ingestion-review-design.md`](../specs/2026-09-16-local-csv-ingestion-review-design.md)

## Global Constraints

- Follow red-green-refactor: every production behavior begins with the named failing test from architecture section 11.
- Preserve frozen bytes exactly; decoding is allowed only after storage.
- Domain and application packages must not import SQLAlchemy, FastAPI, Typer, or Pydantic.
- Every database write occurs through a unit of work with an explicit transaction boundary.
- Identical bytes preserve every new occurrence but reuse one current run unless `reprocess` is explicit.
- Unit tests use the fixed clock `2026-09-16T12:00:00Z` and perform no network access.
- Use deterministic UUIDv5 identities for pipeline-owned children and UUIDv4 only for canonical identities.
- Python functions and public types require complete type annotations; `pyright` runs in strict mode.

---

## File map

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Python dependencies, pytest, Ruff, and pyright configuration |
| `compose.yaml` | Local PostgreSQL service and health check |
| `alembic.ini`, `db/migrations/` | Schema migration environment and revisions |
| `services/domain/` | Framework-free identifiers, field states, run states, and raw-record types |
| `services/application/ports.py` | Repository, unit-of-work, source-store, and clock protocols |
| `services/application/ingest.py` | Ingest and reprocess commands |
| `services/application/process.py` | Parse orchestration and retry entry points |
| `services/infrastructure/db/` | SQLAlchemy models, repositories, and unit of work |
| `services/infrastructure/source_store.py` | Atomic content-addressed filesystem storage |
| `services/pipeline/parse.py` | CSV logical-record parser and record classifier |
| `services/cli/main.py` | Typer adapter for ingest/process/retry/reprocess |
| `tests/unit/ingest/` | `ING-01` through `ING-10` |
| `tests/unit/pipeline/test_parse.py` | `PAR-01` through `PAR-10` |
| `tests/integration/foundation/` | Postgres, migrations, concurrency, and source-store integration |

### Task 1: Python project and domain primitives

**Files:**
- Create: `pyproject.toml`
- Create: `services/__init__.py`
- Create: `services/domain/__init__.py`
- Create: `services/domain/ids.py`
- Create: `services/domain/fields.py`
- Create: `services/domain/runs.py`
- Create: `services/domain/raw.py`
- Test: `tests/unit/domain/test_fields.py`
- Test: `tests/unit/domain/test_ids.py`

**Interfaces:**
- Produces: `new_id() -> UUID`, `deterministic_id(namespace: UUID, *parts: object) -> UUID`
- Produces: `CandidateField[T]`, `FieldState`, `RunState`, `RawRecordKind`, `RawRecord`

- [ ] **Step 1: Write failing field-state tests**

```python
def test_known_field_requires_value() -> None:
    with pytest.raises(ValueError, match="known field requires a value"):
        CandidateField.known(None, source_refs=())

def test_non_known_field_cannot_carry_value() -> None:
    with pytest.raises(ValueError, match="cannot carry a value"):
        CandidateField(state=FieldState.ABSENT, value="x", source_refs=())
```

- [ ] **Step 2: Run the tests and confirm the expected import failure**

Run: `pytest -q tests/unit/domain/test_fields.py`

Expected: FAIL because `services.domain.fields` does not exist.

- [ ] **Step 3: Add project configuration and minimal domain types**

```python
class FieldState(StrEnum):
    KNOWN = "known"
    ABSENT = "absent"
    DEFERRED = "deferred"
    UNRESOLVED = "unresolved"

@dataclass(frozen=True, slots=True)
class CandidateField(Generic[T]):
    state: FieldState
    value: T | None
    source_refs: tuple[SourceRef, ...]
    transformation_refs: tuple[UUID, ...] = ()
    issue_refs: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if self.state is FieldState.KNOWN and self.value is None:
            raise ValueError("known field requires a value")
        if self.state is not FieldState.KNOWN and self.value is not None:
            raise ValueError(f"{self.state} field cannot carry a value")
```

- [ ] **Step 4: Add deterministic-ID tests and implementation**

```python
def test_deterministic_id_is_stable_and_order_sensitive() -> None:
    assert deterministic_id(RAW_NAMESPACE, "run-1", 2, 3) == deterministic_id(
        RAW_NAMESPACE, "run-1", 2, 3
    )
    assert deterministic_id(RAW_NAMESPACE, "run-1", 2, 3) != deterministic_id(
        RAW_NAMESPACE, "run-1", 3, 2
    )
```

Use a length-prefixed UTF-8 serialization of each part before UUIDv5 hashing; never join unescaped strings with a delimiter.

- [ ] **Step 5: Run domain tests and static checks**

Run: `pytest -q tests/unit/domain && ruff check services tests/unit/domain && pyright services/domain`

Expected: PASS with no warnings.

- [ ] **Step 6: Commit the domain foundation**

```bash
git add pyproject.toml services/domain services/__init__.py tests/unit/domain
git commit -m "feat: add typed domain foundation"
```

### Task 2: Postgres schema and transaction ports

**Files:**
- Create: `compose.yaml`
- Create: `alembic.ini`
- Create: `db/migrations/env.py`
- Create: `db/migrations/versions/0001_source_and_runs.py`
- Create: `services/application/ports.py`
- Create: `services/infrastructure/db/models.py`
- Create: `services/infrastructure/db/uow.py`
- Test: `tests/integration/foundation/test_migrations.py`
- Test: `tests/unit/application/test_uow_contract.py`

**Interfaces:**
- Consumes: typed IDs and run states from Task 1
- Produces: `SourceStore`, `Clock`, `UnitOfWork`, repository protocols, `SqlAlchemyUnitOfWork`
- Produces tables: `source_file`, `source_occurrence`, `run`, `run_source_occurrence`, `pipeline_checkpoint`, `pipeline_event`, `raw_record`

- [ ] **Step 1: Write a failing empty-database migration test**

```python
def test_upgrade_creates_foundation_tables(postgres_url: str) -> None:
    command.upgrade(AlembicConfig.for_url(postgres_url), "head")
    assert set(inspect(create_engine(postgres_url)).get_table_names()) >= {
        "source_file", "source_occurrence", "run", "run_source_occurrence",
        "pipeline_checkpoint", "pipeline_event", "raw_record",
    }
```

- [ ] **Step 2: Start isolated Postgres and verify the migration test fails**

Run: `docker compose up -d postgres && pytest -q tests/integration/foundation/test_migrations.py -x`

Expected: FAIL because Alembic configuration and revision are absent.

- [ ] **Step 3: Define the foundation migration**

The migration must include:

```python
op.create_index("uq_source_file_sha256", "source_file", ["sha256"], unique=True)
op.create_index(
    "uq_initial_run_per_source",
    "run",
    ["source_file_id"],
    unique=True,
    postgresql_where=sa.text("predecessor_run_id IS NULL"),
)
op.create_unique_constraint(
    "uq_run_source_occurrence", "run_source_occurrence", ["run_id", "source_occurrence_id"]
)
```

Use `JSONB` for ordered raw fields and parse metadata. Add foreign keys and check constraints for occurrence relation values `initiated` and `duplicate_upload`.

- [ ] **Step 4: Define narrow transaction and repository protocols**

```python
class UnitOfWork(Protocol):
    sources: SourceRepository
    runs: RunRepository
    raw_records: RawRecordRepository
    checkpoints: CheckpointRepository
    events: EventRepository
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
```

- [ ] **Step 5: Implement SQLAlchemy models and unit of work**

Use SQLAlchemy 2 annotated mappings. `SqlAlchemyUnitOfWork.__enter__` opens one session; `commit` is explicit; `__exit__` rolls back unless committed and always closes.

- [ ] **Step 6: Verify upgrade, downgrade, and transaction rollback**

Run: `pytest -q tests/integration/foundation/test_migrations.py tests/unit/application/test_uow_contract.py`

Expected: PASS, including upgrade → downgrade → upgrade from an empty database.

- [ ] **Step 7: Commit persistence foundation**

```bash
git add compose.yaml alembic.ini db/migrations services/application/ports.py services/infrastructure/db tests/integration/foundation tests/unit/application/test_uow_contract.py
git commit -m "feat: add source and run persistence"
```

### Task 3: Atomic content-addressed source store

**Files:**
- Create: `services/infrastructure/source_store.py`
- Test: `tests/unit/ingest/test_source_store.py`
- Test: `tests/integration/foundation/test_source_store.py`

**Interfaces:**
- Implements: `SourceStore.freeze(content: BinaryIO) -> FrozenSource`
- Implements: `SourceStore.open(locator: str) -> BinaryIO`
- Produces: `FrozenSource(sha256: str, byte_size: int, locator: str, reused: bool)`

- [ ] **Step 1: Write failing exact-byte and content-reuse tests**

```python
def test_freeze_preserves_bytes_and_reuses_content(tmp_path: Path) -> None:
    payload = b"\xef\xbb\xbfA,\r\n\xf0\x9f\x8c\x9f\n"
    store = FilesystemSourceStore(tmp_path)
    first = store.freeze(BytesIO(payload))
    second = store.freeze(BytesIO(payload))
    assert store.open(first.locator).read() == payload
    assert first.sha256 == hashlib.sha256(payload).hexdigest()
    assert (first.reused, second.reused) == (False, True)
```

- [ ] **Step 2: Run the source-store test and verify failure**

Run: `pytest -q tests/unit/ingest/test_source_store.py -x`

Expected: FAIL because `FilesystemSourceStore` is absent.

- [ ] **Step 3: Implement streaming freeze and atomic rename**

Write to a unique temporary file beneath the store root, update SHA-256 per chunk, flush and `os.fsync`, create the two-character shard, then use an atomic no-overwrite publish. If the target already exists, delete only the temporary file and return `reused=True`.

- [ ] **Step 4: Test interrupted writes and concurrent freezes**

Inject a stream that raises after one chunk and assert no final object exists. Run two threads freezing the same payload and assert one final object, identical hashes, and no temporary files.

- [ ] **Step 5: Run source-store tests**

Run: `pytest -q tests/unit/ingest/test_source_store.py tests/integration/foundation/test_source_store.py`

Expected: PASS.

- [ ] **Step 6: Commit source storage**

```bash
git add services/infrastructure/source_store.py tests/unit/ingest/test_source_store.py tests/integration/foundation/test_source_store.py
git commit -m "feat: freeze sources by content hash"
```

### Task 4: Exact-file ingest, occurrence preservation, and reprocess chain

**Files:**
- Create: `services/application/ingest.py`
- Create: `services/infrastructure/db/repositories.py`
- Test: `tests/unit/ingest/test_ingest.py`
- Test: `tests/integration/foundation/test_concurrent_ingest.py`

**Interfaces:**
- Consumes: `SourceStore`, `UnitOfWork`, `Clock`
- Produces: `IngestFile`, `ReprocessSource`, `IngestResult`
- Produces: `ingest_file(...) -> IngestResult`, `reprocess_source(...) -> IngestResult`

- [ ] **Step 1: Write `ING-01` through `ING-05` as failing tests**

Use real domain objects and in-memory fake repositories. Assert exact result fields:

```python
assert second == IngestResult(
    source_file_id=first.source_file_id,
    source_occurrence_id=second.source_occurrence_id,
    run_id=first.run_id,
    source_reused=True,
    run_reused=True,
    duplicate_upload=True,
    resume_from_checkpoint=None,
)
```

- [ ] **Step 2: Run the first ingest slice and verify expected failures**

Run: `pytest -q tests/unit/ingest/test_ingest.py -k 'freezes_source or identical_content or same_idempotency or explicit_reprocess or same_filename'`

Expected: FAIL because ingest commands are absent.

- [ ] **Step 3: Implement idempotent ingest and explicit reprocess**

Order operations as: freeze bytes → check idempotency replay → create/reuse source row → create occurrence → lock source row → select terminal run → create initial run or duplicate link → commit. `reprocess_source` locks the source, creates a successor of the terminal run, and links the requesting occurrence as `initiated`.

- [ ] **Step 4: Write `ING-06` through `ING-09` and concurrency tests**

The Postgres concurrency test uses two independent sessions released from a barrier. Both results must return one run ID; the database must contain two occurrences and two association rows.

- [ ] **Step 5: Implement current-run selection and database serialization**

`RunRepository.lock_source(source_file_id)` issues `SELECT ... FOR UPDATE`. `get_terminal_run` follows the unique successor chain and raises a domain invariant error if the graph forks.

- [ ] **Step 6: Run all ingest tests**

Run: `pytest -q tests/unit/ingest/test_ingest.py -k 'not ingest_does_not_interpret_csv' tests/integration/foundation/test_concurrent_ingest.py`

Expected: all `ING-01` through `ING-09` cases PASS.

- [ ] **Step 7: Commit ingest deduplication**

```bash
git add services/application/ingest.py services/infrastructure/db/repositories.py tests/unit/ingest/test_ingest.py tests/integration/foundation/test_concurrent_ingest.py
git commit -m "feat: deduplicate exact-file ingest runs"
```

### Task 5: Immutable CSV parser

**Files:**
- Create: `services/pipeline/parse.py`
- Test: `tests/unit/pipeline/test_parse.py`

**Interfaces:**
- Consumes: frozen binary stream and `run_id`
- Produces: `Iterator[RawRecord]` with deterministic IDs and inclusive line spans

- [ ] **Step 1: Write failing `PAR-01` through `PAR-08` tests**

Use inline `BytesIO` fixtures. For multiline CSV assert:

```python
assert record.source_line_start == 2
assert record.source_line_end == 3
assert record.fields[-1] == "first line\nsecond line"
```

- [ ] **Step 2: Run parser tests and verify failure**

Run: `pytest -q tests/unit/pipeline/test_parse.py -k 'not failed_batch and not retry'`

Expected: FAIL because `parse_records` is absent.

- [ ] **Step 3: Implement logical parsing without structural repair**

Use `TextIOWrapper(binary, encoding="utf-8-sig", newline="")` and `csv.reader`. Track the prior and current `reader.line_num` for inclusive spans. Classify empty arrays as `blank`, the first exact header as `header`, subsequent exact headers as `repeated_header`, and all other arrays as `data`.

- [ ] **Step 4: Run pure parser tests**

Run: `pytest -q tests/unit/pipeline/test_parse.py -k 'not failed_batch and not retry'`

Expected: `PAR-01` through `PAR-08` PASS.

- [ ] **Step 5: Commit immutable parsing**

```bash
git add services/pipeline/parse.py tests/unit/pipeline/test_parse.py
git commit -m "feat: preserve complete csv raw evidence"
```

### Task 6: Parse batching, checkpoints, and retry

**Files:**
- Create: `services/application/process.py`
- Modify: `services/infrastructure/db/repositories.py`
- Modify: `tests/unit/pipeline/test_parse.py`
- Create: `tests/integration/foundation/test_parse_recovery.py`

**Interfaces:**
- Produces: `parse_run(run_id, batch_size, uow_factory, source_store, failure_injector=None) -> StageResult`
- Produces: `retry_run(command, ...) -> RunResult` for parse-stage recovery

- [ ] **Step 1: Write failing `PAR-09` and `PAR-10` tests**

Inject failure immediately before the second batch commit. Assert batch 1 and its checkpoint exist, batch 2 has no rows, and retry yields the same deterministic raw IDs as uninterrupted parsing.

- [ ] **Step 2: Verify the recovery tests fail for missing orchestration**

Run: `pytest -q tests/unit/pipeline/test_parse.py -k 'failed_batch or retry'`

Expected: FAIL because `parse_run` is absent.

- [ ] **Step 3: Implement checkpointed batch orchestration**

Each batch uses a fresh unit of work. Insert raw rows and advance the checkpoint in the same transaction. On retry, reopen the source and skip logical records through the checkpoint ordinal; deterministic IDs make an accidental replay harmless.

- [ ] **Step 4: Connect duplicate incomplete ingest to retry**

Implement `ING-07` and `ING-10`: `ingest --process` dispatches `retry_run` when `run_reused` and the run is incomplete; ingest alone never invokes parsing, even for malformed bytes.

- [ ] **Step 5: Run parse recovery and complete ingest suites**

Run: `pytest -q tests/unit/ingest/test_ingest.py tests/unit/pipeline/test_parse.py tests/integration/foundation/test_parse_recovery.py`

Expected: `ING-01` through `ING-10` and `PAR-01` through `PAR-10` PASS.

- [ ] **Step 6: Commit parse recovery**

```bash
git add services/application/process.py services/infrastructure/db/repositories.py tests/unit/ingest/test_ingest.py tests/unit/pipeline/test_parse.py tests/integration/foundation/test_parse_recovery.py
git commit -m "feat: resume parsing from durable checkpoints"
```

### Task 7: CLI adapter and foundation quality gate

**Files:**
- Create: `services/cli/main.py`
- Create: `tests/unit/cli/test_main.py`
- Modify: `pyproject.toml`
- Create: `.gitignore`

**Interfaces:**
- Consumes: application commands only
- Produces commands: `ingest`, `process`, `retry`, `reprocess`

- [ ] **Step 1: Write failing CLI contract tests**

```python
def test_duplicate_ingest_reports_reused_run(runner: CliRunner) -> None:
    result = runner.invoke(app, ["ingest", "sample.csv"])
    assert result.exit_code == 0
    assert "Exact file already ingested" in result.stdout
    assert "Run:" in result.stdout
```

- [ ] **Step 2: Run CLI tests and confirm failure**

Run: `pytest -q tests/unit/cli/test_main.py`

Expected: FAIL because the Typer application is absent.

- [ ] **Step 3: Implement thin CLI commands**

Commands construct Pydantic boundary inputs, call one application function, and format result fields. No repository or SQLAlchemy model is imported by `services/cli/main.py`.

- [ ] **Step 4: Add runtime-data ignores**

Ignore `.venv/`, Python caches, `.pytest_cache/`, `.ruff_cache/`, `.pyright/`, `.env`, and `var/sources/`; do not ignore the pinned data fixtures or migrations.

- [ ] **Step 5: Run the complete foundation gate**

Run: `pytest -q tests/unit/domain tests/unit/ingest tests/unit/pipeline tests/unit/cli tests/integration/foundation`

Run: `ruff check services tests && pyright services`

Expected: all tests and static checks PASS with no unexpected warnings.

- [ ] **Step 6: Commit the CLI and foundation gate**

```bash
git add services/cli tests/unit/cli pyproject.toml .gitignore
git commit -m "feat: expose local ingestion cli"
```
