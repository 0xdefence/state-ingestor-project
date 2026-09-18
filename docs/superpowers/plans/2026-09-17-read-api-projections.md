# Read API and Application Projections Implementation Plan

> **Historical plan — do not execute.** Replaced by the delivered [actionable-review vertical slice](2026-09-17-actionable-review-vertical-slice.md), verified on 18 September 2026. Current behavior and verification commands live in [the canonical architecture](../../ARCHITECTURE.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose internally consistent workspace, run-detail, and review-queue projections through a typed, read-only local FastAPI service.

**Architecture:** Query services read purpose-built projections and never acquire command repositories. Presenters add plain-language labels and absolute localized timestamps while retaining internal codes and machine instants. FastAPI validates scope and route inputs, maps typed application errors, and performs no pipeline mutations.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 Core projections, PostgreSQL 16, pytest, HTTPX

**Spec:** [`docs/superpowers/specs/2026-09-16-local-csv-ingestion-review-design.md`](../specs/2026-09-16-local-csv-ingestion-review-design.md)

## Global Constraints

- Complete the foundation and derived-pipeline plans before starting this plan.
- Implement `API-01` through `API-09` test-first.
- API and presenter packages cannot import command repositories or stage functions.
- Every aggregate request requires one explicit file scope: current, non-empty selected, or all.
- A response computes totals, chart segments, recent runs, and review rows from one query snapshot.
- Return internal codes and plain-language labels together; unknown codes receive a readable fallback.
- Return machine instants and absolute timezone-bearing display timestamps; never return relative time.
- Preserve stable identifiers and evidence links without copying candidate values into review DTOs.

---

## File map

| Path | Responsibility |
|---|---|
| `services/application/queries.py` | Scope variants and query input types |
| `services/application/views.py` | Framework-free immutable view models |
| `services/application/query_services.py` | Workspace, run, and review query orchestration |
| `services/application/read_ports.py` | Narrow read-repository protocols |
| `services/infrastructure/db/read_models.py` | SQLAlchemy Core queries and snapshot handling |
| `services/api/app.py` | FastAPI factory and dependency wiring |
| `services/api/routes/` | Health, workspace, run, and review endpoints |
| `services/api/models.py` | Pydantic response and request-boundary models |
| `services/api/presenters.py` | Labels, timestamps, and error presentation |
| `tests/unit/application/test_queries.py` | `API-01` through `API-06` and `API-09` |
| `tests/unit/api/test_presenters.py` | `API-07` and `API-08` |
| `tests/integration/api/` | Route, serialization, snapshot, and error tests |

### Task 1: Scope queries and immutable view models

**Files:**
- Create: `services/application/queries.py`
- Create: `services/application/views.py`
- Create: `services/application/read_ports.py`
- Test: `tests/unit/application/test_queries.py`

**Interfaces:**
- Produces: `CurrentFileScope`, `SelectedFilesScope`, `AllFilesScope`, `FileScope`
- Produces: `WorkspaceView`, `RunDetailView`, `ReviewQueueView`, `OccurrenceView`, `StageView`, `EvidenceView`
- Produces: `WorkspaceReadRepository`, `RunReadRepository`, `ReviewReadRepository`

- [ ] **Step 1: Write failing scope-construction tests**

```python
def test_selected_scope_rejects_empty_selection() -> None:
    with pytest.raises(ValueError, match="at least one run"):
        SelectedFilesScope(run_ids=())

def test_scope_variants_are_exclusive() -> None:
    assert CurrentFileScope(RUN_ID).kind == "current"
    assert AllFilesScope().kind == "all"
```

- [ ] **Step 2: Run scope tests and verify failure**

Run: `pytest -q tests/unit/application/test_queries.py -k 'scope'`

Expected: FAIL because query types are absent.

- [ ] **Step 3: Implement frozen scope and view dataclasses**

Use a discriminated union:

```python
FileScope = CurrentFileScope | SelectedFilesScope | AllFilesScope

@dataclass(frozen=True, slots=True)
class WorkspaceQuery:
    scope: FileScope
    timezone: ZoneInfo
```

View models contain typed IDs, internal codes, domain instants, counts, and evidence references. They do not contain formatted labels or strings derived from locale.

- [ ] **Step 4: Define narrow read ports**

Each repository accepts one query/scope and returns one projection. Do not expose ORM sessions, generic filters, or mutation methods.

- [ ] **Step 5: Run query-model tests**

Run: `pytest -q tests/unit/application/test_queries.py -k 'scope or view'`

Expected: PASS.

- [ ] **Step 6: Commit query contracts**

```bash
git add services/application/queries.py services/application/views.py services/application/read_ports.py tests/unit/application/test_queries.py
git commit -m "feat: define read projection contracts"
```

### Task 2: Workspace projection and snapshot consistency

**Files:**
- Create: `services/application/query_services.py`
- Create: `services/infrastructure/db/read_models.py`
- Modify: `tests/unit/application/test_queries.py`
- Create: `tests/integration/api/test_workspace_projection.py`

**Interfaces:**
- Produces: `get_workspace(query, repository) -> WorkspaceView`
- Implements: `SqlWorkspaceReadRepository.fetch(scope) -> WorkspaceView`

- [ ] **Step 1: Write failing `API-01` through `API-04` tests**

Build three runs with distinct counts and review states. Assert current, selected, and all scopes include exactly the intended IDs and that every segment sum equals the displayed total.

- [ ] **Step 2: Run workspace tests and verify failure**

Run: `pytest -q tests/unit/application/test_queries.py -k 'workspace'`

Expected: FAIL because workspace query service is absent.

- [ ] **Step 3: Implement one-snapshot workspace query**

Open one read-only transaction at PostgreSQL `REPEATABLE READ`. Resolve scoped run IDs once, then query immutable `run.counts`, outstanding review, failures, and recent runs using that set.

- [ ] **Step 4: Add database snapshot integration test**

Pause the projection after resolving scoped IDs, commit a new run from another connection, continue the projection, and assert the response remains internally consistent and excludes the later commit.

- [ ] **Step 5: Run workspace projection tests**

Run: `pytest -q tests/unit/application/test_queries.py -k 'workspace' tests/integration/api/test_workspace_projection.py`

Expected: `API-01` through `API-04` PASS.

- [ ] **Step 6: Commit workspace projections**

```bash
git add services/application/query_services.py services/infrastructure/db/read_models.py tests/unit/application/test_queries.py tests/integration/api/test_workspace_projection.py
git commit -m "feat: query consistent workspace snapshots"
```

### Task 3: Run detail, occurrence history, and evidence lineage

**Files:**
- Modify: `services/application/query_services.py`
- Modify: `services/infrastructure/db/read_models.py`
- Modify: `tests/unit/application/test_queries.py`
- Create: `tests/integration/api/test_run_projection.py`

**Interfaces:**
- Produces: `get_run_detail(query, repository) -> RunDetailView`
- Includes all `OccurrenceView` rows ordered by ingest time and ID

- [ ] **Step 1: Write failing `API-05` and `API-09` tests**

```python
assert detail.occurrence_count == 2
assert [item.relation for item in detail.occurrences] == ["initiated", "duplicate_upload"]
assert detail.records[0].candidate_chain[-1].classification.review_item_id == REVIEW_ID
```

- [ ] **Step 2: Run run-detail tests and verify failure**

Run: `pytest -q tests/unit/application/test_queries.py -k 'run_detail or source_occurrences'`

Expected: FAIL because run-detail projection is absent.

- [ ] **Step 3: Implement lineage-preserving run projection**

Load run/source metadata, all occurrence associations, ordered stages/events, raw records, candidate revision chains, transformations, issues, classifications, readiness, review references, and staged revisions. Return IDs and references rather than embedding copied candidate values into multiple DTOs.

- [ ] **Step 4: Verify query count and ordering**

The integration test asserts stable occurrence/revision ordering and a bounded query count that does not grow per record. Use bulk queries keyed by run ID and assemble the graph in memory.

- [ ] **Step 5: Run run projection tests**

Run: `pytest -q tests/unit/application/test_queries.py -k 'run_detail or source_occurrences' tests/integration/api/test_run_projection.py`

Expected: `API-05` and `API-09` PASS.

- [ ] **Step 6: Commit run projections**

```bash
git add services/application/query_services.py services/infrastructure/db/read_models.py tests/unit/application/test_queries.py tests/integration/api/test_run_projection.py
git commit -m "feat: project run evidence and upload history"
```

### Task 4: Review queue filtering

**Files:**
- Modify: `services/application/query_services.py`
- Modify: `services/infrastructure/db/read_models.py`
- Modify: `tests/unit/application/test_queries.py`
- Create: `tests/integration/api/test_review_projection.py`

**Interfaces:**
- Produces: `get_review_queue(query, repository) -> ReviewQueueView`
- Consumes: explicit file scope plus zero or more review-state/verdict/issue filters

- [ ] **Step 1: Write failing `API-06` tests**

Assert a `NEEDS_REVIEW` filter inside selected files never leaks matching rows from unselected runs, and selected-item detail belongs to the same scoped result set.

- [ ] **Step 2: Run review tests and verify failure**

Run: `pytest -q tests/unit/application/test_queries.py -k 'review_filter'`

Expected: FAIL because review query service is absent.

- [ ] **Step 3: Implement composable allow-listed filters**

Map typed enum filters to SQL expressions. Reject unknown fields; do not offer arbitrary column names, sorting, or JSON paths. Reasons remain stable issue/reference IDs plus factual summaries.

- [ ] **Step 4: Run review projection tests**

Run: `pytest -q tests/unit/application/test_queries.py -k 'review_filter' tests/integration/api/test_review_projection.py`

Expected: `API-06` PASS.

- [ ] **Step 5: Commit review projections**

```bash
git add services/application/query_services.py services/infrastructure/db/read_models.py tests/unit/application/test_queries.py tests/integration/api/test_review_projection.py
git commit -m "feat: query scoped review work"
```

### Task 5: Plain-language and timestamp presenters

**Files:**
- Create: `services/api/presenters.py`
- Create: `services/api/labels.py`
- Create: `tests/unit/api/test_presenters.py`

**Interfaces:**
- Produces: `display_label(raw_code: str) -> str`
- Produces: `present_timestamp(instant: datetime, timezone: ZoneInfo) -> TimestampResponse`

- [ ] **Step 1: Write failing `API-07` and `API-08` tests**

```python
def test_unknown_code_has_readable_fallback() -> None:
    assert display_label("new_pipeline_state") == "New pipeline state"

def test_timestamp_is_absolute_and_zoned() -> None:
    result = present_timestamp(INSTANT, ZoneInfo("Europe/London"))
    assert result.instant == "2026-09-16T12:00:00Z"
    assert result.display == "16 Sep 2026, 13:00 BST"
    assert "ago" not in result.display
```

- [ ] **Step 2: Run presenter tests and verify failure**

Run: `pytest -q tests/unit/api/test_presenters.py`

Expected: FAIL because presenters are absent.

- [ ] **Step 3: Implement centralized label registry and fallback**

Register all known verdict, readiness, issue, run-state, and stage values. Formatting never changes the stored/internal code included alongside the label.

- [ ] **Step 4: Implement explicit timezone presentation**

Require timezone input, reject naive instants, emit canonical UTC ISO text plus localized absolute display and timezone abbreviation.

- [ ] **Step 5: Run presenter tests**

Run: `pytest -q tests/unit/api/test_presenters.py`

Expected: `API-07` and `API-08` PASS.

- [ ] **Step 6: Commit presenters**

```bash
git add services/api/presenters.py services/api/labels.py tests/unit/api/test_presenters.py
git commit -m "feat: present operational labels and timestamps"
```

### Task 6: FastAPI application and error contract

**Files:**
- Create: `services/api/app.py`
- Create: `services/api/dependencies.py`
- Create: `services/api/models.py`
- Create: `services/api/errors.py`
- Create: `services/api/routes/health.py`
- Test: `tests/integration/api/test_health.py`
- Test: `tests/integration/api/test_errors.py`

**Interfaces:**
- Produces: `create_app(settings, query_dependencies) -> FastAPI`
- Produces error body: `{code: str, message: str, details: object | null}`

- [ ] **Step 1: Write failing health and typed-error tests**

Assert healthy database returns 200, unavailable database returns 503, unknown run returns 404, and invalid selected scope returns 422 with stable code and plain-language message.

- [ ] **Step 2: Run application tests and verify failure**

Run: `pytest -q tests/integration/api/test_health.py tests/integration/api/test_errors.py`

Expected: FAIL because the app factory is absent.

- [ ] **Step 3: Implement app factory and dependencies**

Construct query-only repository dependencies. Do not attach command unit-of-work providers to the FastAPI dependency graph.

- [ ] **Step 4: Implement typed exception mapping**

Map `NotFound`, `InvalidScope`, and `RepositoryUnavailable` to 404, 422, and 503. Unexpected exceptions log a correlation ID and return a generic 500 without internal detail.

- [ ] **Step 5: Run health and error tests**

Run: `pytest -q tests/integration/api/test_health.py tests/integration/api/test_errors.py`

Expected: PASS.

- [ ] **Step 6: Commit API foundation**

```bash
git add services/api tests/integration/api/test_health.py tests/integration/api/test_errors.py
git commit -m "feat: add read-only local api foundation"
```

### Task 7: Workspace, run, and review routes

**Files:**
- Create: `services/api/routes/workspace.py`
- Create: `services/api/routes/runs.py`
- Create: `services/api/routes/review.py`
- Modify: `services/api/app.py`
- Modify: `services/api/models.py`
- Create: `tests/integration/api/test_routes.py`

**Interfaces:**
- Produces: `GET /api/workspace`
- Produces: `GET /api/runs/{run_id}`
- Produces: `GET /api/review-items`

- [ ] **Step 1: Write failing route-contract tests**

Test exact query shapes:

```text
/api/workspace?scope=current&run_id=<uuid>&timezone=Europe/London
/api/workspace?scope=selected&run_id=<uuid>&run_id=<uuid>&timezone=Europe/London
/api/workspace?scope=all&timezone=Europe/London
/api/runs/<uuid>?review=<review-uuid>&timezone=Europe/London
/api/review-items?scope=current&run_id=<uuid>&state=open
```

- [ ] **Step 2: Run route tests and verify 404 failures**

Run: `pytest -q tests/integration/api/test_routes.py`

Expected: FAIL because routes are unregistered.

- [ ] **Step 3: Implement explicit scope parsing and response serialization**

Reject missing current run, empty selected IDs, selected IDs on all scope, and duplicate IDs after normalization. Present views only after application query services return.

- [ ] **Step 4: Assert read-only OpenAPI surface**

The test inspects OpenAPI and asserts no `POST`, `PUT`, `PATCH`, or `DELETE` path exists under `/api`.

- [ ] **Step 5: Run all route and serialization tests**

Run: `pytest -q tests/integration/api/test_routes.py tests/integration/api`

Expected: all route, error, serialization, and read-only assertions PASS.

- [ ] **Step 6: Commit routes**

```bash
git add services/api/routes services/api/app.py services/api/models.py tests/integration/api/test_routes.py
git commit -m "feat: expose workspace run and review queries"
```

### Task 8: API quality gate

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/integration/api/test_openapi.py`

**Interfaces:**
- Verifies all plan interfaces without adding product behavior

- [ ] **Step 1: Add OpenAPI schema assertions**

Assert required response fields, enum values, occurrence list, timestamp shape, internal code/display label pairs, and documented 404/422/503 responses.

- [ ] **Step 2: Run all application and API tests**

Run: `pytest -q tests/unit/application tests/unit/api tests/integration/api`

Expected: `API-01` through `API-09` and all route tests PASS.

- [ ] **Step 3: Run static and architecture checks**

Run: `ruff check services/application services/api services/infrastructure/db/read_models.py tests/unit/application tests/unit/api tests/integration/api`

Run: `pyright services/application services/api`

Run: `python -c "import pathlib; s=''.join(p.read_text() for p in pathlib.Path('services/api').rglob('*.py')); assert 'services.application.ingest' not in s and 'services.application.load' not in s"`

Expected: PASS with no mutation-service imports.

- [ ] **Step 4: Commit API verification**

```bash
git add pyproject.toml tests/integration/api/test_openapi.py
git commit -m "test: verify read api contract"
```
