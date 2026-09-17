# Actionable Review Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a working local application in which a nontechnical operator uploads a CSV, processes it, inspects exceptions, records whole-record decisions, and sees approved revisions become current canonical data with complete history.

**Architecture:** Keep the completed immutable-evidence pipeline and add one application orchestrator, append-only decision/promotion services, a consolidated FastAPI adapter, and a focused React operator client. PostgreSQL transactions own all state changes; source bytes remain content-addressed on the filesystem; the browser consumes typed API projections and never owns business rules.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 16, pytest, HTTPX, React, TypeScript, Vite, Bun, TanStack Query, React Router, CSS Modules, Vitest, Testing Library, `user-event`, `axe-core`, Playwright

**Spec:** [`docs/superpowers/specs/2026-09-16-local-csv-ingestion-review-design.md`](../specs/2026-09-16-local-csv-ingestion-review-design.md)

## Global Constraints

- Preserve frozen source bytes, raw records, candidate revisions, classifications, issues, decisions, canonical revisions, and promotion events; never update or delete evidence rows.
- Continue to use the persisted terminal run state `staged`; present it to operators as `Processed`.
- Promote only terminal `CLEAN` and `AUTO_REPAIRED` candidates whose dependencies are ready; keep `NEEDS_REVIEW`, `REJECTED`, `DUPLICATE`, and blocked candidates outside canonical state until their defined outcome.
- Decisions operate on a whole record and require the exact candidate revision, expected decision sequence, operator name, and idempotency key.
- `NEEDS_REVIEW` and changed-value conflicts allow approve or reject. `REJECTED` and `DUPLICATE` allow acknowledge or reject and can never create canonical data.
- Decision rows and promotion events are append-only. Reversal appends the next effective outcome and moves the rebuildable `canonical_current` projection.
- Decision and canonical effects commit in one PostgreSQL transaction. Stale commands return conflict with no partial writes.
- Use the pinned local ECB fixture; make no runtime network calls.
- API timestamps include machine instants and absolute timezone-bearing display values; never produce relative time.
- Use Geist/Vercel styling, rounded controls, neutral surfaces, plain grey timestamps, visible focus, text with every semantic color, and light-grey filters.
- MVP routes are `/`, `/runs/:runId`, and `/reviews`; file scopes are exactly `Current file`, `Selected files`, and `All files`.
- The MVP uses textual outcome counts and adds no charting dependency.
- Follow test-driven development and commit each task only after its focused tests, Ruff, and strict Pyright pass.

---

## File map

| Path | Responsibility |
|---|---|
| `services/application/process.py` | Legal-stage orchestration, pinned FX selection, retry, and complete run result |
| `services/domain/decisions.py` | Decision outcomes, commands/results, promotion actions, and invariants |
| `services/application/decisions.py` | Locked decision, canonical promotion/reversal, and dependent-unblocking transaction |
| `services/application/decision_ports.py` | Narrow decision and promotion repository protocols |
| `services/application/queries.py` | File scopes, filters, and immutable query inputs |
| `services/application/views.py` | Framework-free workspace, run, review, evidence, and decision views |
| `services/application/query_services.py` | Projection orchestration and plain domain errors |
| `services/infrastructure/db/decision_repository.py` | SQLAlchemy decision/promotion/current persistence |
| `services/infrastructure/db/read_repository.py` | Snapshot-consistent workspace/run/review SQL projections |
| `services/api/` | FastAPI factory, dependencies, request/response models, routes, presenters, and error mapping |
| `db/migrations/versions/0005_review_decisions.py` | Append-only decisions, promotion events, current projection, constraints, and backfill |
| `apps/web/src/features/workspace/` | Upload/process, scope, attention, failure, outcome, and recent-run UI |
| `apps/web/src/features/runs/` | Run summary, occurrence history, stages, records, and evidence UI |
| `apps/web/src/features/reviews/` | Filtered queue, persistent detail, decision form/history, and stale recovery |
| `tests/integration/test_messy_sample_data.py` | Frozen 54-line complete-pipeline proof |
| `tests/integration/review/` | Decision, promotion, reversal, stale-write, and API integration tests |
| `apps/web/e2e/operator-flow.spec.ts` | Complete operator acceptance flow |

### Task 1: Close the pipeline and prove the frozen sample

**Files:**
- Modify: `services/application/process.py`
- Modify: `services/application/ports.py`
- Modify: `services/application/fx_ports.py`
- Modify: `services/infrastructure/db/repositories.py`
- Modify: `services/infrastructure/db/fx_repository.py`
- Modify: `services/infrastructure/runtime.py`
- Modify: `services/cli/main.py`
- Create: `tests/support/graph_snapshot.py`
- Create: `tests/integration/test_messy_sample_data.py`
- Modify: `tests/unit/pipeline/test_recovery.py`
- Modify: `tests/unit/cli/test_main.py`

**Interfaces:**
- Consumes: `parse_run`, `normalise_run`, `classify_run`, `stage_run`, `default_registry`, `NormaliseContext`, pinned `FxSnapshot`
- Produces: `process_run(ProcessRun, UnitOfWorkFactory, SourceStore, Clock) -> RunResult`
- Produces: `retry_run(RetryRun, UnitOfWorkFactory, SourceStore, Clock) -> RunResult`
- Produces: `FxRepository.latest() -> FxSnapshot` and `RunRepository.pin_fx_snapshot(run_id: UUID, snapshot_id: UUID) -> None`
- Produces: `RunResult(run_id, state, parse, normalise, classification_counts, promoted_count)`

- [ ] **Step 1: Write failing orchestration tests**

Add tests proving an ingested run advances through all legal stages, a `staged` run is a no-write replay, and parse/normalise/classify/load failures resume at their durable boundary. Use a stage-call recorder for the unit test:

```python
def test_process_dispatches_each_stage_until_staged(monkeypatch, store):
    calls: list[str] = []
    monkeypatch.setattr(process, "parse_run", lambda *a, **k: calls.append("parse") or StageResult(store.run_id, 52))
    monkeypatch.setattr(process, "normalise_run", lambda *a, **k: calls.append("normalise") or StageResult(store.run_id, 52))
    monkeypatch.setattr(process, "classify_run", lambda *a, **k: calls.append("classify") or store.classification)
    monkeypatch.setattr(process, "stage_run", lambda *a, **k: calls.append("load") or LoadResult(store.run_id, 31))
    result = process_run(ProcessRun(store.run_id, 10), store.uow, store.sources, store.clock)
    assert calls == ["parse", "normalise", "classify", "load"]
    assert (result.state, result.promoted_count) == (RunState.STAGED, 31)
```

- [ ] **Step 2: Run the orchestration tests and verify the current parse-only implementation fails**

Run: `.venv/bin/pytest -q tests/unit/pipeline/test_recovery.py tests/unit/cli/test_main.py -k 'process or retry or complete'`

Expected: FAIL because `RunResult` and `process_run` expose only parse behavior.

- [ ] **Step 3: Add pinned-snapshot selection and legal-stage orchestration**

Implement the loop around persisted state, not assumed call order:

```python
while True:
    with uow_factory() as uow:
        run = uow.runs.get(command.run_id)
    if run.state in (RunState.INGESTED, RunState.PARSING):
        parsed = parse_run(...)
    elif run.state in (RunState.PARSED, RunState.NORMALISING):
        normalised = normalise_run(..., NormaliseContext(clock))
    elif run.state in (RunState.NORMALISED, RunState.CLASSIFYING):
        classified = classify_run(..., default_registry(), uow_factory(), clock)
    elif run.state in (RunState.CLASSIFIED, RunState.LOADING):
        loaded = stage_run(...)
    elif run.state is RunState.STAGED:
        return RunResult(...)
    else:
        raise ValueError(f"Run cannot be processed from {run.state}")
```

Before normalisation, pin `uow.fx.latest().id` only when the run has no snapshot. `build_runtime` imports `data/fx/ecb_history.csv` and its manifest idempotently before exposing the runtime. Do not re-pin an existing run.

- [ ] **Step 4: Add the frozen 54-line golden test and graph snapshot helper**

Assert the immutable fixture identity and the complete outcome graph:

```python
assert sha256(source_bytes).hexdigest() == "e550ba421ebaac800d2e734c513f65d2bc4197f8b622a2fc82230974744110c4"
assert (len(source_bytes), source_bytes.count(b"\n")) == (5953, 54)
assert raw_counts == {"data": 48, "header": 1, "repeated_header": 1, "blank": 2}
assert physical_spans == {(15, 16), (36, 37)}
assert run.state is RunState.STAGED
assert sum(run.counts.values()) == 48
```

`graph_snapshot(engine, run_id)` must sort IDs and retain source, raw, candidate, transformation, issue, classification, dependency, review, canonical, checkpoint, and terminal run facts while excluding database transaction metadata.

- [ ] **Step 5: Add one representative recovery-equality proof**

Inject a failure in normalisation batch 2, call `retry_run`, and assert the resulting graph snapshot equals an uninterrupted run after replacing only the run/source identities with stable labels. Retain the existing boundary-specific unit tests; do not add a new exhaustive interruption matrix.

- [ ] **Step 6: Update CLI output for complete processing**

Replace parse-only copy with:

```python
typer.echo(f"State: {'Processed' if result.state is RunState.STAGED else result.state.value}")
typer.echo(f"Raw records: {result.parse.record_count}")
typer.echo(f"Candidates normalised: {result.normalise.record_count}")
typer.echo(f"Outcomes: {result.classification_counts}")
typer.echo(f"Canonical revisions promoted: {result.promoted_count}")
```

- [ ] **Step 7: Run the complete Task 1 gate**

Run: `.venv/bin/pytest -q tests/unit/pipeline tests/unit/cli tests/integration/test_messy_sample_data.py tests/integration/pipeline`

Run: `.venv/bin/ruff check services tests && .venv/bin/pyright services`

Expected: all pass; the golden test accounts for 54 physical lines and 48 terminal classifications.

- [ ] **Step 8: Commit Task 1**

```bash
git add services/application services/infrastructure services/cli tests/support tests/integration/test_messy_sample_data.py tests/unit/pipeline tests/unit/cli
git commit -m "feat: orchestrate complete ingestion pipeline"
```

### Task 2: Add append-only decisions and canonical promotion

**Files:**
- Create: `services/domain/decisions.py`
- Create: `services/application/decision_ports.py`
- Create: `services/application/decisions.py`
- Create: `services/infrastructure/db/decision_repository.py`
- Create: `db/migrations/versions/0005_review_decisions.py`
- Modify: `services/domain/canonical.py`
- Modify: `services/application/canonical_ports.py`
- Modify: `services/application/derived_ports.py`
- Modify: `services/application/ports.py`
- Modify: `services/infrastructure/db/models.py`
- Modify: `services/infrastructure/db/canonical_repository.py`
- Modify: `services/infrastructure/db/derived_repositories.py`
- Modify: `services/infrastructure/db/uow.py`
- Modify: `services/infrastructure/runtime.py`
- Create: `tests/unit/review/test_decisions.py`
- Create: `tests/integration/review/test_decision_migration.py`
- Create: `tests/integration/review/test_decisions.py`

**Interfaces:**
- Produces: `DecisionOutcome(APPROVE, REJECT, ACKNOWLEDGE)` and `PromotionAction(ACTIVATE, WITHDRAW)`
- Produces: `DecideReview(review_item_id, candidate_revision_id, expected_sequence, outcome, operator_name, reason, idempotency_key, supersedes_decision_id=None)`
- Produces: `DecisionResult(decision, canonical_revision, effective_state, replayed)`
- Produces: `decide_review(command: DecideReview, uow: UnitOfWork, clock: Clock) -> DecisionResult`
- Produces: `UnitOfWork.decisions: DecisionRepository`
- Extends: `CanonicalRepository` with `current`, `activate`, `withdraw`, `next_revision_number`, and governed-key identity locking
- Produces repositories for locked review reads, idempotency lookup, ordered decisions, promotion events, and `canonical_current`

- [ ] **Step 1: Write failing domain-invariant tests**

```python
def test_rejection_requires_reason():
    with pytest.raises(ValueError, match="reason"):
        DecideReview(REVIEW, CANDIDATE, 0, DecisionOutcome.REJECT, "Alex", None, "key")

def test_operator_and_idempotency_key_must_be_nonempty():
    with pytest.raises(ValueError):
        DecideReview(REVIEW, CANDIDATE, 0, DecisionOutcome.APPROVE, " ", None, "key")
```

Also test UUIDv4 decision/promotion identities, positive monotonic sequences, and same-review supersession.

- [ ] **Step 2: Run the domain tests and verify failure**

Run: `.venv/bin/pytest -q tests/unit/review/test_decisions.py`

Expected: FAIL because the decision domain is absent.

- [ ] **Step 3: Implement domain types and migration constraints**

Create `review_decision`, `canonical_promotion_event`, and `canonical_current`. The migration must enforce:

```text
UNIQUE (review_item_id, sequence)
UNIQUE (idempotency_key)
CHECK sequence > 0
CHECK outcome IN ('approve','reject','acknowledge')
CHECK length(trim(operator_name)) > 0
CHECK outcome <> 'reject' OR length(trim(reason)) > 0
```

Use a same-review composite foreign key for `supersedes_decision_id`. Promotion events use `activate|withdraw`, reference the canonical identity/revision, optional decision, prior-current revision, and timestamp. Backfill one activation event and `canonical_current` row for the maximum existing revision per identity; using the existing canonical revision UUID as the backfill event UUID preserves valid UUIDv4 values without a database extension.

- [ ] **Step 4: Write failing command tests for all legal outcomes**

Cover `DEC-01` through `DEC-07`: approval of new identity, approval of changed-value conflict, rejection, acknowledge/reject exclusion for rejected/duplicate records, identical idempotency replay, mismatched replay rejection, stale candidate/sequence conflict, and reversal.

```python
result = decide_review(
    DecideReview(item.id, terminal.id, 0, DecisionOutcome.APPROVE, "Alex", "Checked source", "decision-1"),
    uow,
    clock,
)
assert result.canonical_revision.revision_number == 1
assert uow.canonicals.current(result.canonical_revision.identity_id) == result.canonical_revision
```

- [ ] **Step 5: Implement the locked decision transaction**

The command must execute in this order inside one UoW:

1. return the stored result for an identical idempotency replay;
2. lock the review item and load its classification plus exact terminal candidate;
3. compare candidate ID and latest sequence;
4. validate the legal outcome for verdict/readiness;
5. append the decision;
6. append/activate or withdraw canonical state when required;
7. re-evaluate dependency-blocked dependants after activation;
8. commit once.

Raise typed `StaleDecisionError`, `IllegalDecisionError`, and `IdempotencyConflictError`; do not encode HTTP status in the application layer.

- [ ] **Step 6: Implement conflict revision append and reversal**

For an approved changed-value conflict, resolve the existing identity by governed business key, lock it, calculate `max(revision_number) + 1`, append the revision and activation event, and replace `canonical_current`. Reversing approval appends a rejecting decision plus withdrawal event and restores `prior_current_revision_id`; reversing rejection appends an approving decision plus activation event.

- [ ] **Step 7: Test PostgreSQL atomicity and backfill**

Inject failure after each decision/canonical write and assert zero partial rows and unchanged `canonical_current`. Upgrade a database containing Task 1 canonical rows and assert every identity has one backfilled current projection and activation event. Downgrade/upgrade the empty schema in development.

- [ ] **Step 8: Run the Task 2 gate**

Run: `.venv/bin/pytest -q tests/unit/review tests/integration/review tests/unit/load tests/integration/pipeline/test_atomic_load.py`

Run: `.venv/bin/ruff check services tests && .venv/bin/pyright services`

Expected: all decision and prior load tests pass with no warnings.

- [ ] **Step 9: Commit Task 2**

```bash
git add db/migrations/versions/0005_review_decisions.py services/domain services/application services/infrastructure tests/unit/review tests/integration/review
git commit -m "feat: add review decisions and canonical promotion"
```

### Task 3: Expose the consolidated local API

**Files:**
- Modify: `pyproject.toml`
- Create: `services/application/queries.py`
- Create: `services/application/views.py`
- Create: `services/application/query_services.py`
- Create: `services/application/read_ports.py`
- Create: `services/infrastructure/db/read_repository.py`
- Create: `services/api/__init__.py`
- Create: `services/api/app.py`
- Create: `services/api/dependencies.py`
- Create: `services/api/errors.py`
- Create: `services/api/models.py`
- Create: `services/api/presenters.py`
- Create: `services/api/routes/uploads.py`
- Create: `services/api/routes/runs.py`
- Create: `services/api/routes/workspace.py`
- Create: `services/api/routes/reviews.py`
- Create: `tests/unit/application/test_queries.py`
- Create: `tests/unit/api/test_presenters.py`
- Create: `tests/integration/api/test_product_api.py`

**Interfaces:**
- Produces: `CurrentFileScope`, `SelectedFilesScope`, `AllFilesScope`, and `FileScope`
- Produces: `WorkspaceView`, `RunDetailView`, `ReviewQueueView`, `ReviewDetailView`, `DecisionView`, and `EvidenceView`
- Produces: FastAPI `create_app(runtime_factory=build_runtime) -> FastAPI`
- Consumes: Task 1 ingest/process services and Task 2 decision service

- [ ] **Step 1: Add FastAPI/HTTPX dependencies and write failing query tests**

Add bounded dependencies `fastapi>=0.116,<1`, `python-multipart>=0.0.20,<1`, `uvicorn>=0.35,<1`, and dev dependency `httpx>=0.28,<1`. Test exclusive scope construction:

```python
def test_selected_scope_rejects_empty_selection():
    with pytest.raises(ValueError, match="at least one run"):
        SelectedFilesScope(())
```

- [ ] **Step 2: Implement immutable query/view types and one read repository**

Use one SQLAlchemy read repository opened at PostgreSQL `REPEATABLE READ` for each aggregate response. Query services receive only the read port. Every view retains stable IDs/internal codes plus plain labels; candidate values are referenced once in the evidence graph rather than copied into queue rows.

The scope/view signatures consumed by Tasks 4 and 5 are:

```python
FileScope = CurrentFileScope | SelectedFilesScope | AllFilesScope
get_workspace(repo: ReadRepository, query: WorkspaceQuery) -> WorkspaceView
get_run_detail(repo: ReadRepository, query: RunDetailQuery) -> RunDetailView
get_review_queue(repo: ReadRepository, query: ReviewQueueQuery) -> ReviewQueueView
get_review_detail(repo: ReadRepository, query: ReviewDetailQuery) -> ReviewDetailView
```

- [ ] **Step 3: Write failing presenter and API error tests**

```python
assert present_code("NEEDS_REVIEW") == "Needs attention"
assert present_code("new_internal_value") == "New internal value"
assert present_instant(instant, ZoneInfo("Europe/London")) == {
    "instant": "2026-09-17T12:00:00Z",
    "display": "17 September 2026, 13:00 BST",
    "timezone": "Europe/London",
}
```

Assert a stable error body: `{"error":{"code":"stale_decision","message":"…","details":{...}}}`.

- [ ] **Step 4: Implement upload and process routes**

`POST /api/uploads` accepts multipart field `file`, form fields `operator_name` and optional `idempotency_key`, calls `ingest_file`, and returns `201` for a new source or `200` for reuse. `POST /api/runs/{run_id}/process` calls `process_run` and returns `200` with stage/outcome facts. Enforce a configured byte limit while streaming to the source store; do not read an unbounded file into memory.

- [ ] **Step 5: Implement workspace/run/review read routes**

Expose:

```text
GET /api/workspace
GET /api/runs/{run_id}
GET /api/reviews
GET /api/reviews/{review_item_id}
```

`scope=current` requires one `run_id`; `scope=selected` requires at least one repeated `run_id`; `scope=all` rejects supplied run IDs. Review filters include effective state and verdict. Run detail lists every exact-file occurrence in ingest order.

- [ ] **Step 6: Implement the decision route and typed error mapping**

`POST /api/reviews/{review_item_id}/decisions` validates the Task 2 command body. Map missing rows to 404, validation/illegal outcomes to 422, stale/idempotency conflicts to 409, and unexpected failures to a safe 500 without database credentials.

- [ ] **Step 7: Prove the seven product endpoints in one integration test**

Using `httpx.AsyncClient` with ASGI transport, upload the frozen sample, process it, read workspace/run/queue/detail, decide one item, and verify the updated detail. Assert `GET /api/health` separately.

- [ ] **Step 8: Run the Task 3 gate**

Run: `.venv/bin/pytest -q tests/unit/application tests/unit/api tests/integration/api`

Run: `.venv/bin/ruff check services tests && .venv/bin/pyright services`

Expected: all route, projection, serialization, and error-contract tests pass.

- [ ] **Step 9: Commit Task 3**

```bash
git add pyproject.toml uv.lock services/application services/infrastructure/db/read_repository.py services/api tests/unit/application tests/unit/api tests/integration/api
git commit -m "feat: expose actionable review api"
```

### Task 4: Build the workspace upload and attention view

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/index.html`
- Create: `apps/web/src/main.tsx`
- Create: `apps/web/src/app/router.tsx`
- Create: `apps/web/src/app/providers.tsx`
- Create: `apps/web/src/api/client.ts`
- Create: `apps/web/src/api/contracts.ts`
- Create: `apps/web/src/api/queries.ts`
- Create: `apps/web/src/styles/tokens.css`
- Create: `apps/web/src/styles/global.css`
- Create: `apps/web/src/components/Status.tsx`
- Create: `apps/web/src/components/AbsoluteTime.tsx`
- Create: `apps/web/src/features/workspace/WorkspacePage.tsx`
- Create: `apps/web/src/features/workspace/UploadPanel.tsx`
- Create: `apps/web/src/features/workspace/ScopeControl.tsx`
- Create: `apps/web/src/features/workspace/AttentionSummary.tsx`
- Create: `apps/web/src/features/workspace/OutcomeBreakdown.tsx`
- Create: `apps/web/src/features/workspace/RecentRuns.tsx`
- Create: `apps/web/src/test/render.tsx`
- Create: `apps/web/src/features/workspace/WorkspacePage.test.tsx`

**Interfaces:**
- Consumes: Task 3 API contracts
- Produces: `/` workspace route and query state `scope=current|selected|all&run_id=...`
- Produces: `uploadFile(file, operatorName, idempotencyKey)` and `processRun(runId)`

- [ ] **Step 1: Scaffold strict Vite/React tests and write the failing workspace test**

Create the package with runtime dependencies `react`, `react-dom`, `react-router-dom`, `@tanstack/react-query`; development dependencies `typescript`, `vite`, `@vitejs/plugin-react`, `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`, `@testing-library/user-event`, `axe-core`, `@axe-core/react`, and `@playwright/test`. Scripts are `dev`, `build`, `test`, `typecheck`, and `e2e`.

```tsx
test('prioritises upload, outstanding work, failures, and recent runs', async () => {
  renderWorkspace();
  expect(await screen.findByRole('heading', {name: 'Workspace'})).toBeVisible();
  expect(screen.getByRole('button', {name: 'Upload and process'})).toBeEnabled();
  expect(screen.getByRole('heading', {name: 'Outstanding review'})).toBeVisible();
  expect(screen.getByRole('heading', {name: 'Recent runs'})).toBeVisible();
});
```

- [ ] **Step 2: Add API contracts, providers, and shared design tokens**

Use TanStack Query for server state and React Router for URL state. Tokens must include Geist font stacks, neutral surfaces, semantic green/amber/red/purple/grey, visible focus, nested radii, and light-grey field backgrounds. Add no gradients or chart library.

- [ ] **Step 3: Implement upload/process behavior**

The panel validates one `.csv` file and nonempty operator name, generates a stable per-attempt idempotency key, disables repeat submission while active, reports exact upload/process errors, and navigates to `/runs/:runId` after processing. A duplicate response states that the exact file was already uploaded and identifies the reused run.

- [ ] **Step 4: Implement scope and attention projections**

Expose exactly `Current file`, `Selected files`, and `All files`. Show the searchable multi-select only for Selected files; removable selections and the selected count remain visible. Outcome counts are textual links to `/reviews` with inherited scope/verdict filters.

- [ ] **Step 5: Implement recent runs without status pills**

Use a compact semantic table with filename, dot plus text status, segmented textual outcome summary, absolute timestamp, and row link. The compact color key includes visible labels. Timestamps render as plain grey text.

- [ ] **Step 6: Test loading, empty, failure, duplicate, long filename, and keyboard states**

Use Testing Library and `user-event`; run `axe` on the default, loading, empty, and failure fixtures. Assert no relative timestamp words and no status represented only by color.

- [ ] **Step 7: Run the Task 4 gate**

Run: `cd apps/web && bun test && bun run typecheck && bun run build`

Expected: component tests, strict TypeScript, and production build pass without warnings.

- [ ] **Step 8: Commit Task 4**

```bash
git add apps/web
git commit -m "feat: add operator workspace"
```

### Task 5: Build run evidence and actionable review workflows

**Files:**
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/api/queries.ts`
- Create: `apps/web/src/features/runs/RunPage.tsx`
- Create: `apps/web/src/features/runs/OccurrenceHistory.tsx`
- Create: `apps/web/src/features/runs/PipelineStages.tsx`
- Create: `apps/web/src/features/runs/EvidencePanel.tsx`
- Create: `apps/web/src/features/reviews/ReviewPage.tsx`
- Create: `apps/web/src/features/reviews/ReviewQueue.tsx`
- Create: `apps/web/src/features/reviews/ReviewDetail.tsx`
- Create: `apps/web/src/features/reviews/DecisionForm.tsx`
- Create: `apps/web/src/features/reviews/DecisionHistory.tsx`
- Create: `apps/web/src/features/runs/RunPage.test.tsx`
- Create: `apps/web/src/features/reviews/ReviewPage.test.tsx`

**Interfaces:**
- Produces: `/runs/:runId?review=:reviewItemId`
- Produces: `/reviews?scope=...&state=...&verdict=...&review=...`
- Consumes: `GET /api/runs/{id}`, `GET /api/reviews`, `GET /api/reviews/{id}`, and decision mutation

- [ ] **Step 1: Write failing run-page evidence tests**

Assert filename, run ID, record count, absolute processed timestamp, exact-submission count/history, stage reachability, selected review item, raw fields, interpreted fields, transformations, issues, dependencies, and canonical lineage.

- [ ] **Step 2: Implement run route and stage evidence**

Completed/current stages with evidence are buttons; future stages are disabled and labelled `Not reached`. Selecting a stage replaces the evidence panel without losing the review query parameter. Use `Normalisation details`; never render `de-normalise`.

- [ ] **Step 3: Write failing review queue/detail tests**

```tsx
await user.keyboard('{ArrowDown}{Enter}');
expect(screen.getByRole('heading', {name: /Review ORD-3001/})).toHaveFocus();
expect(screen.getByText('Exact source value')).toBeVisible();
expect(screen.getByRole('button', {name: 'Approve'})).toBeEnabled();
expect(screen.getByRole('button', {name: 'Reject'})).toBeEnabled();
```

For rejected/duplicate fixtures, assert Acknowledge and Reject are present and Approve is absent. For blocked dependencies, show `Waiting for another record` and no approval action.

- [ ] **Step 4: Implement persistent queue/detail and decision form**

Keep scope/filter/selection in the URL. The detail shows source and interpreted values, registered reasons, pipeline stage, dependency references, prior canonical revision, technical IDs, and ordered decision history. Require operator name for all actions and a reason for rejection.

- [ ] **Step 5: Implement successful, failed, and stale decision states**

Disable repeat submission while saving. On success, invalidate workspace/run/review queries and retain selection long enough to show the resulting effective state and canonical revision. On `409`, preserve operator/reason input, refetch detail, and show: `This record changed before your decision was saved. Review the refreshed evidence and try again.`

- [ ] **Step 6: Test keyboard, accessibility, overflow, and absolute timestamps**

Prove arrow-key queue navigation, visible focus, predictable detail focus, semantic form errors, long filename/value overflow, text accompanying color, and `axe` checks for run/review default/loading/empty/failure/stale fixtures.

- [ ] **Step 7: Run the Task 5 gate**

Run: `cd apps/web && bun test && bun run typecheck && bun run build`

Expected: workspace, run, and review suites pass with no TypeScript or build warnings.

- [ ] **Step 8: Commit Task 5**

```bash
git add apps/web/src
git commit -m "feat: add run and review workflows"
```

### Task 6: Prove the local MVP end to end

**Files:**
- Create: `apps/web/playwright.config.ts`
- Create: `apps/web/e2e/operator-flow.spec.ts`
- Create: `scripts/run-local.sh`
- Create: `scripts/quality-gate.sh`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/superpowers/specs/2026-09-16-local-csv-ingestion-review-design.md`

**Interfaces:**
- Consumes: complete API and web app
- Produces: stable local start and repository quality-gate commands

- [ ] **Step 1: Write the failing Playwright operator flow**

The test must upload `data/messy_sample_data.csv`, process it, open a review item, inspect exact source and interpreted values, make a legal decision, verify the canonical result, reverse it, upload the same bytes again, and verify occurrence count increases while the run is reused.

```ts
await page.getByLabel('CSV file').setInputFiles('../../data/messy_sample_data.csv');
await page.getByLabel('Operator name').fill('Alex');
await page.getByRole('button', {name: 'Upload and process'}).click();
await expect(page.getByText('Processed')).toBeVisible();
```

- [ ] **Step 2: Add deterministic local start scripts**

`scripts/run-local.sh` applies migrations, starts FastAPI on `127.0.0.1:8000`, and starts Vite on `127.0.0.1:5173` with cleanup traps. It uses local PostgreSQL and filesystem storage only. `scripts/quality-gate.sh` runs Python tests, Ruff, Pyright, web tests, typecheck, and build in a fixed order.

- [ ] **Step 3: Add retry and duplicate checks to acceptance**

Use API fixtures/failure injection for one recoverable processing failure, resume the same run, and verify the final projection. Verify a byte-identical second upload creates a new occurrence, reuses the run, and performs no duplicate pipeline or canonical writes.

- [ ] **Step 4: Run the browser and accessibility acceptance gate**

Run: `cd apps/web && bunx playwright test e2e/operator-flow.spec.ts`

Expected: the keyboard-operable upload/process/review/promotion/reversal/duplicate flow passes.

- [ ] **Step 5: Run the complete repository quality gate**

Run: `./scripts/quality-gate.sh`

Expected: all Python tests, web tests, Ruff, strict Pyright, TypeScript, and production build pass with no unexpected warnings.

- [ ] **Step 6: Update canonical documentation with verified commands and status**

Mark the MVP implemented only after Step 5 passes. Document setup, migration, local launch, operator flow, test commands, and the remaining deferred scope. Remove the superseded warning only from plans whose replacement is now delivered; keep historical plans clearly non-executable.

- [ ] **Step 7: Commit Task 6**

```bash
git add apps/web scripts README.md docs
git commit -m "test: prove actionable review mvp"
```
