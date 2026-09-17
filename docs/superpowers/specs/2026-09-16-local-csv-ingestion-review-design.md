# Local CSV Ingestion and Review Design Specification

**Status:** Pending owner approval
**Date:** 16 September 2026
**Amended:** 17 September 2026 — exact-file submission deduplication
**Implements:** [`docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md)
**Fixture contract:** [`data/messy_sample_data.schema.md`](../../../data/messy_sample_data.schema.md)
**Visual baseline:** [workspace and review layout, revision 9](../../../.superpowers/brainstorm/79491-1789562129/content/workspace-review-layout-v9.html)

## 1. Purpose

Build the first complete local ingestion-and-review slice for mixed-record CSV files. The system freezes source bytes, preserves a complete raw representation, derives typed candidate revisions, classifies cross-record outcomes, stages eligible canonical revisions atomically, and presents the run to a nontechnical operations reviewer.

The design optimizes for traceability and safe retry. Every displayed or staged value must remain connected to its frozen source, raw fields, transformations, issues, rules version, and candidate revision.

## 2. Scope

The slice includes:

- filesystem CSV ingestion through a CLI;
- content-addressed local source storage;
- exact-file submission deduplication with preserved occurrence history;
- immutable raw records with physical-line spans;
- typed customer, product, and order candidates;
- deterministic normalization, FX conversion, classification, and registered repair rules;
- durable batch checkpoints and idempotent retry;
- atomic staging of eligible canonical revisions;
- Postgres persistence and migrations;
- a local read API;
- the workspace overview, run page, and review queue;
- the exact automated test contract in architecture section 11.

The slice ends at staged canonical data and read-only review display. Human editing, approval, publication, logical deletion, authentication, production hosting, remote object storage, scheduled FX updates, and expanded audit governance remain in [`TODO.md`](../../../TODO.md).

## 3. Technology decisions

| Concern | Decision | Reason |
|---|---|---|
| Python | Python 3.12 | Modern typing, stable ecosystem, and explicit minimum runtime. |
| Python packaging | `pyproject.toml` with `uv` for locked development environments | Fast deterministic local setup while preserving ordinary `pytest` commands inside the environment. |
| Domain/application | Plain dataclasses, enums, protocols, and services | Keeps pipeline behavior independent from HTTP and ORM objects. |
| API | FastAPI | Typed local read API with straightforward dependency injection and OpenAPI inspection. |
| Validation boundary | Pydantic v2 | Used only for CLI/API inputs and serialized responses. Domain objects remain framework-independent. |
| Persistence | SQLAlchemy 2 and Alembic | Explicit transaction control, Postgres support, and inspectable migrations. |
| Database | PostgreSQL 16 or newer | Relational lineage, constraints, transactional staging, and JSONB only where evidence shape is genuinely variable. |
| CLI | Typer | Small typed adapter around application commands. |
| Web | React, TypeScript, Vite, and Bun | Focused client application with fast tests and no server-rendering requirement for the local slice. |
| UI data access | TanStack Query | Cache, stale/refetch states, and explicit request lifecycles required by the UI contract. |
| UI styling | CSS Modules plus shared design tokens | Direct control over Geist-based presentation without introducing a competing component aesthetic. |
| Charts | Accessible SVG component owned by the application | The approved donut has small, fixed requirements; a charting dependency would add unnecessary behavior and styling. |
| Python tests | pytest | Matches the canonical test contract. |
| Web tests | Vitest, Testing Library, `user-event`, and `axe-core` | Component behavior, keyboard flow, and accessibility checks. |

No external service or network call is required at runtime. FX rates come from a pinned fixture imported into Postgres.

## 4. Repository structure

```text
apps/
  web/
    src/
      api/                 typed API client and query keys
      components/          reusable Geist-based UI elements
      features/workspace/  scope control, breakdown, outstanding review, recent runs
      features/runs/       run page, pipeline stages, evidence panel
      features/review/     queue, filters, selected-item details
      styles/              tokens, typography, global and reduced-motion rules
services/
  domain/                  pure types, identities, field states, verdicts, invariants
  application/             commands, queries, ports, orchestration, state transitions
  pipeline/                ingest, parse, normalize, classify, load, rules, FX logic
  infrastructure/          SQLAlchemy models/repos, transactions, source store, clock
  api/                     FastAPI application, routes, response models, presenters
  cli/                     Typer commands and terminal formatting
db/
  migrations/              Alembic revisions
data/
  messy_sample_data.csv
  messy_sample_data.schema.md
  fx/                      pinned ECB-history fixture and provenance manifest
tests/
  unit/                    exact Python unit contract
  integration/             Postgres, filesystem, API, and golden-pipeline tests
docs/
  superpowers/specs/       approved design specifications
  superpowers/plans/       executable implementation plans
var/
  sources/                 runtime content-addressed bytes; ignored by Git
```

Each Python directory is part of one installable project but exposes a deliberate package boundary. Imports flow inward:

```text
api / cli / infrastructure
            |
            v
       application
       /         \
      v           v
  pipeline ----> domain
```

`domain` imports no other application package. `application` depends on domain types and port protocols. `pipeline` implements domain operations used by application orchestration. Infrastructure implements ports. API and CLI call application services and never write repositories directly.

## 5. Core domain model

### 5.1 Identities

Every persisted object uses a typed UUID identifier. Deterministic pipeline-owned child IDs are UUIDv5 values derived from their stable parent identity, stage, source position, revision number, and applicable rules version. Canonical entity IDs are UUIDv4 values created only by the canonical command service.

Business identifiers such as `CUST-1001`, `SKU-2001`, and `ORD-3001` are attributes with uniqueness/conflict rules. They are never database identity keys.

### 5.2 Field values

```python
FieldState = Literal["known", "absent", "deferred", "unresolved"]

@dataclass(frozen=True)
class CandidateField(Generic[T]):
    state: FieldState
    value: T | None
    source_refs: tuple[SourceRef, ...]
    transformation_refs: tuple[UUID, ...]
    issue_refs: tuple[UUID, ...]
```

Construction enforces that only `known` may carry `value`, and `known` must carry a value. The four states are never represented through overlapping nullable columns or magic strings.

### 5.3 Candidate revisions

`CustomerCandidate`, `ProductCandidate`, and `OrderCandidate` are frozen discriminated dataclasses containing typed `CandidateField` values. A `CandidateRevision` envelope contains:

- revision ID;
- raw-record ID;
- entity type;
- revision number starting at 1;
- optional parent revision ID;
- origin: `normalise` or registered rule ID;
- candidate payload;
- created timestamp.

An invalid discriminator produces `RejectedCandidateShell`, which retains the raw-record reference and structural issues without inventing an entity type.

### 5.4 Classification and readiness

```python
Verdict = Literal["CLEAN", "AUTO_REPAIRED", "NEEDS_REVIEW", "REJECTED", "DUPLICATE"]
Readiness = Literal["eligible", "blocked_by_dependency", "ineligible"]
```

Verdict describes the assessed terminal candidate revision. Readiness describes whether its dependencies allow staging. They are stored separately.

`ClassificationResult` is immutable for `(candidate_revision_id, rules_version, fx_snapshot_id)`. Issues reference an exact candidate revision, issue code, severity, field path, factual summary, source references, and optional explicitly tentative cause.

## 6. Persistence model

### 6.1 Source and processing tables

| Table | Essential keys and constraints |
|---|---|
| `source_file` | UUID PK, unique SHA-256, byte size, relative content locator |
| `source_occurrence` | UUID PK, source-file FK, filename, original locator, actor label, idempotency key, ingested timestamp; unique idempotency key when supplied |
| `run` | UUID PK, source-file FK, optional predecessor-run FK, reprocess sequence, processing state, stage failure, fixed FX snapshot, rules/build revisions, immutable final counts; one initial run per source file |
| `run_source_occurrence` | run FK plus occurrence FK PK, relation `initiated` or `duplicate_upload`, linked timestamp; append-only |
| `pipeline_checkpoint` | run FK plus stage PK, last committed batch and record identity, updated timestamp |
| `pipeline_event` | UUID PK, run FK, stage, event type, structured facts, timestamp; append-only |
| `raw_record` | deterministic UUID PK, run FK, inclusive source lines, kind, ordered fields JSONB, field count, parse metadata JSONB |

The raw JSONB fields contain arrays and parser facts, not business state. Database constraints prevent updates to frozen and append-only tables through the repository layer; tests verify the service contract rather than relying solely on triggers. A partial unique constraint permits only one run with no predecessor for each source file. Explicit reprocess runs form a predecessor chain, and the terminal run is the reuse target for later identical submissions.

### 6.2 Derived and review tables

| Table | Essential keys and constraints |
|---|---|
| `candidate_revision` | deterministic UUID PK, raw-record FK, optional parent FK, entity type, revision number, origin, typed payload JSONB, unique raw/revision number |
| `transformation_event` | UUID PK, revision FK, rule/operation code, field path, before/after structured values, ordered sequence |
| `data_quality_issue` | deterministic UUID PK, revision FK, registered code, severity, field path, summary, tentative cause, source refs JSONB |
| `classification_result` | deterministic UUID PK, revision FK, rules version, FX snapshot FK, verdict, readiness, assessed timestamp, unique revision/rules/snapshot |
| `dependency_record` | UUID PK, classification FK, dependency kind, referenced business value, resolved entity FK when present, state |
| `duplicate_relation` | UUID PK, later raw FK, earlier raw FK, comparison scope |
| `review_item` | deterministic UUID PK, run FK, raw FK, classification FK, display state `open`, stable reason refs, created timestamp |

Review items are read-only in this slice. Their `open` state is a display fact, not the deferred governed workflow described in `TODO.md`.

### 6.3 Canonical and FX tables

| Table | Essential keys and constraints |
|---|---|
| `canonical_identity` | UUID PK, entity type, created timestamp |
| `canonical_revision` | UUID PK, identity FK, candidate-revision FK, revision number, staged timestamp, unique identity/revision number |
| `canonical_business_key` | identity FK, key type, value, effective revision, governed uniqueness constraint |
| `fx_snapshot` | UUID PK, manifest hash, effective run timestamp, source label |
| `fx_rate` | snapshot FK, currency, publication date, EUR reference rate, source provenance; immutable after reference |

Canonical payload is read through its candidate revision rather than copied into a second mutable row. Canonical revisions record governed identity history and the exact accepted candidate.

## 7. Application ports and services

### 7.1 Ports

```python
class SourceStore(Protocol):
    def freeze(self, content: BinaryIO) -> FrozenSource: ...
    def open(self, locator: str) -> BinaryIO: ...

class UnitOfWork(Protocol):
    sources: SourceRepository
    runs: RunRepository
    raw_records: RawRecordRepository
    candidates: CandidateRepository
    classifications: ClassificationRepository
    reviews: ReviewRepository
    canonicals: CanonicalRepository
    fx: FxRepository
    def commit(self) -> None: ...
    def rollback(self) -> None: ...

class Clock(Protocol):
    def now(self) -> datetime: ...
```

Repositories expose aggregate-level operations, not generic `save(anything)` methods. Query services use read projections and cannot acquire command repositories.

### 7.2 Commands

```python
ingest_file(command: IngestFile, uow: UnitOfWork, source_store: SourceStore, clock: Clock) -> IngestResult
process_run(command: ProcessRun, uow_factory: UnitOfWorkFactory, source_store: SourceStore, clock: Clock) -> RunResult
retry_run(command: RetryRun, uow_factory: UnitOfWorkFactory, source_store: SourceStore, clock: Clock) -> RunResult
reprocess_source(command: ReprocessSource, uow: UnitOfWork, clock: Clock) -> IngestResult
```

`IngestResult` contains `source_file_id`, `source_occurrence_id`, `run_id`, `source_reused`, `run_reused`, `duplicate_upload`, and an optional resume checkpoint. The CLI exposes `ingest`, `process`, `retry`, and `reprocess` commands. `ingest --process` is a convenience composition of the first two commands and does not create an alternative pipeline path. For a duplicate incomplete run it invokes `retry_run` on the returned run; for a completed run it performs no additional pipeline work.

### 7.3 Queries

```python
get_workspace(query: WorkspaceQuery) -> WorkspaceView
get_run_detail(query: RunDetailQuery) -> RunDetailView
get_review_queue(query: ReviewQueueQuery) -> ReviewQueueView
```

`WorkspaceQuery` requires one explicit scope variant: current run ID, a non-empty tuple of selected run IDs, or all runs. Counts, chart segments, recent runs, and review results use the same scope predicate.

## 8. Pipeline design

### 8.1 State machine

```text
created
  -> ingested
  -> parsing -> parsed
  -> normalising -> normalised
  -> classifying -> classified
  -> loading -> staged
```

Each active stage has a corresponding recoverable failure fact. A load failure returns the primary state to `classified` and sets `load_failed`; it does not erase classifications. Illegal transitions are rejected before side effects.

### 8.2 Ingest

Ingest streams the filesystem file through SHA-256 calculation into a temporary file, fsyncs it, and atomically renames it to `var/sources/<first-two-hash-chars>/<full-hash>`. Existing content reuses the same object. After the content object exists, ingest upserts and locks the `source_file` row so concurrent byte-identical submissions serialize.

The same idempotency key returns the same occurrence and run. A new submission always preserves its own occurrence metadata. Under the source lock, ingest selects the terminal run in that source's predecessor chain. If one exists, it adds a `duplicate_upload` run-occurrence link and returns that run; it does not create or repeat pipeline data. If none exists, it creates the initial run and an `initiated` link. Exact bytes determine duplication even when filenames differ. The same filename with different bytes creates a different source file and run.

When processing is requested for a reused incomplete or failed run, orchestration calls `retry_run` and resumes from the durable stage boundary. A reused completed run returns immediately. Explicit `reprocess` is the only command that creates a successor run for known content, and that successor becomes the reuse target for subsequent duplicate submissions.

### 8.3 Parse

The parser reads the frozen object with `utf-8-sig` and Python's RFC-compatible CSV parser. It records logical fields and the parser's inclusive physical-line range. It recognizes the first header, exact repeated headers, and blank records without dropping them.

Parse commits configurable batches. A deterministic raw-record ID includes run ID, line span, kind, and logical ordinal. The checkpoint advances in the same transaction as its batch. Short and long rows are preserved unchanged.

### 8.4 Normalize

Normalize maps each data raw record to one initial candidate revision or rejected shell. It applies only registered, single-record value operations:

- edge trimming with provenance;
- field-sensitive null/deferred recognition;
- decimal money parsing and annotations;
- approved date formats;
- approved status mappings;
- tag parsing;
- display-preserving match keys;
- FX conversion from the fixed snapshot;
- explicitly registered structural salvage.

Unsupported forms become unresolved and create reviewable issues. The registry cannot infer rules from notes or surrounding rows.

### 8.5 Classify

Classification starts only after every data record has an initial candidate or rejected shell. It loads the complete run graph, applies ordered registered rules, and may append candidate revisions for rules explicitly marked repairable.

Rules handle SKU zero-padding, order line-total repair, relations, referrals, invariants, exact duplicates, same-run conflicts, earlier-run re-observation/conflict, and dependency readiness. The final rules version is a content hash of ordered rule definitions and relevant normalization configuration.

Classification writes results, issues, relations, and review items idempotently by deterministic IDs.

### 8.6 Load

Load calculates the full eligible set before opening its write transaction. It stages `CLEAN` and terminal `AUTO_REPAIRED` candidate revisions whose readiness is `eligible`. All canonical identity, key, and revision writes commit together.

`NEEDS_REVIEW`, `REJECTED`, `DUPLICATE`, and dependency-blocked candidates create no canonical write. Any load error rolls back the complete staged set and records `load_failed` outside the failed transaction.

## 9. Error behavior

Errors use stable typed codes and preserve causal detail for logs:

| Error family | Examples | Run effect |
|---|---|---|
| Source | unreadable path, content-store write failure | No opened run unless source freeze succeeded |
| Parse | invalid UTF-8, CSV engine failure, injected batch failure | Stage failure and last committed checkpoint retained |
| Normalize | registry/configuration error, batch persistence failure | Stage failure and last committed checkpoint retained |
| Classify | incomplete candidate barrier, invalid rule registry | No partial active-rules classification set |
| Load | constraint or injected transaction failure | Entire canonical set rolled back; state `classified` plus `load_failed` |
| Query | unknown run, invalid/empty selected scope | Typed 404 or 422 API response with plain-language message |

Data-quality problems are persisted outcomes, not thrown application errors. A malformed record should become reviewable evidence whenever its source can be preserved safely.

## 10. Local API

The FastAPI process serves read-only product endpoints:

| Method and path | Result |
|---|---|
| `GET /api/workspace` | Workspace view for `scope=current|selected|all`; current uses `run_id`, selected uses repeated `run_id` parameters |
| `GET /api/runs/{run_id}` | Run metadata, all linked source occurrences, duplicate-submission summary, stages, counts, records, selected review item, and evidence graph |
| `GET /api/review-items` | Scoped and filtered queue plus selected-item details |
| `GET /api/health` | Local process and database connectivity status |

The browser does not start runs or mutate review decisions in this slice. CLI commands own processing. API response models carry internal codes alongside plain display labels, absolute ISO instants, localized display timestamps, and explicit timezones.

## 11. Operator interface

### 11.1 Application shell

The client uses routes `/`, `/runs/:runId`, and `/review`. The query string stores shareable context: scope, selected run IDs, filters, and selected review item. Route parsing rejects malformed state and falls back to the nearest valid view with an explanatory message.

### 11.2 Workspace

The workspace renders, in document order:

1. outstanding review and failure summary;
2. active-scope breakdown with accessible donut;
3. outstanding-review file links;
4. compact recent-runs table.

The scope control exposes exactly `Current file`, `Selected files`, and `All files`. The selected-file search appears only for the selected scope. All panels consume a single `WorkspaceView` so their totals cannot represent different snapshots.

### 11.3 Run and review

The run page shows file identity, absolute timestamp, count summary, pipeline stages, records, and evidence details. When exact content has been submitted more than once, it shows a plain-language exact-file notice, total submission count, and an expandable list of every filename, actor label, and absolute ingest timestamp. `/runs/:runId?review=:reviewItemId` selects the requested review item without discarding run context.

The review route uses a queue/detail split. Keyboard selection updates the detail panel, announces the new item, and retains filter/scope position. Pipeline-stage buttons are enabled only for reached stages with evidence; future stages are disabled and labelled not reached.

### 11.4 Presentation system

Shared tokens define Geist typography, neutral surfaces, borders, nested radii, semantic status colors, focus rings, hit areas, and motion duration. Components always pair color with text. Dates are absolute, plain grey text rather than pills. Filters use light-grey fields. The donut reserves SVG padding for focus/hover emphasis and exposes an equivalent legend and summary.

All component states and accessibility criteria from architecture sections 8.11 and 8.12 are acceptance requirements.

## 12. Test design

Architecture section 11 is the authoritative behavior list: 92 named unit/component tests plus the frozen golden integration test. Delivery follows test-first red-green-refactor at the smallest behavior boundary.

Additional integration boundaries are:

- SQLAlchemy repositories against isolated Postgres;
- Alembic migration from an empty database and downgrade/upgrade round trip during development;
- source-store atomic write and content reuse in a temporary directory;
- concurrent byte-identical ingest serialization against Postgres;
- FastAPI response serialization and error mapping;
- full pipeline equality between uninterrupted and injected-failure/retry runs;
- web API adapters against deterministic response fixtures.

Tests use the fixed clock and no network. The golden file hash must match before any expected outcome is evaluated.

## 13. Delivery decomposition

The work is too broad for one safe execution plan. It will be implemented through four sequential plans, each leaving a working testable increment:

1. **Foundation and durable evidence** — project tooling, domain types, migrations, repositories, content-addressed source store, ingest, parse, and their tests.
2. **Derived pipeline and atomic staging** — normalization, pinned FX, rules, classification, duplicate/conflict behavior, load, recovery, and the golden graph.
3. **Read API and application projections** — workspace, run, and review query services; FastAPI routes; presenter labels and timestamp contracts.
4. **Operator web interface and final verification** — approved Geist UI, responsive/accessibility states, full component suite, and complete quality gate.

Each plan references this design and the canonical architecture. A later plan may consume only interfaces delivered and verified by earlier plans; it may not silently redefine them.

## 14. Acceptance criteria

The design is implemented when:

- a fresh local setup can migrate Postgres, import the pinned FX fixture, ingest the frozen CSV, process it, and serve the read API and web application;
- every physical source line is accounted for and every data record has one active-rules terminal classification;
- retries after every injected boundary produce a persisted graph equal to uninterrupted processing;
- submitting identical bytes repeatedly preserves every occurrence while reusing one current run unless explicit reprocessing is requested;
- staging is atomic and contains only eligible clean or repaired terminal candidates;
- the workspace, run page, and review queue match the approved visual and behavior requirements;
- all required unit, integration, component, accessibility, and golden tests pass without unexpected warnings;
- no deferred capability from `TODO.md` is exposed as if implemented.
