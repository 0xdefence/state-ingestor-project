# Operator Web Interface and Final Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved Geist operator interface for workspace, run, and review workflows and prove the complete local application against its functional, accessibility, responsive, and recovery contracts.

**Architecture:** A React client consumes only the typed read API. URL query state carries file scope, filters, and selected review item; TanStack Query owns request lifecycles. Shared design tokens and focused feature components implement the approved visual baseline while preserving semantic HTML, keyboard navigation, text equivalents, and reduced-motion behavior.

**Tech Stack:** Bun, React, TypeScript strict mode, Vite, React Router, TanStack Query, CSS Modules, Vitest, Testing Library, `user-event`, `axe-core`, Playwright

**Spec:** [`docs/superpowers/specs/2026-09-16-local-csv-ingestion-review-design.md`](../specs/2026-09-16-local-csv-ingestion-review-design.md)

## Global Constraints

- Complete the first three plans before starting this plan.
- Implement `UI-01` through `UI-17` test-first.
- Follow the approved [revision 9 mockup](../../../.superpowers/brainstorm/79491-1789562129/content/workspace-review-layout-v9.html) and architecture section 8; written requirements win if the artifact differs.
- Use Geist Sans and Geist Mono, neutral surfaces, crisp borders, nested radii, and semantic state colors.
- Never communicate state through color alone.
- Never display relative processing timestamps.
- Scope options are exactly `Current file`, `Selected files`, and `All files`.
- Essential chart information must exist as text outside tooltips.
- Use semantic HTML before ARIA and honor `prefers-reduced-motion`.
- No review mutation, approval, publication, authentication, or upload workflow is added in this slice.

---

## File map

| Path | Responsibility |
|---|---|
| `apps/web/package.json` | Bun scripts and frontend dependencies |
| `apps/web/src/main.tsx` | React entry point and providers |
| `apps/web/src/app/router.tsx` | `/`, `/runs/:runId`, `/review` routes and query-state parsing |
| `apps/web/src/api/` | Generated-shaped response types, fetcher, and TanStack Query hooks |
| `apps/web/src/styles/` | Geist fonts, tokens, global styles, focus, breakpoints, reduced motion |
| `apps/web/src/components/` | Shared button, field, tooltip, status, skeleton, and error components |
| `apps/web/src/features/workspace/` | Scope, breakdown, outstanding review, and recent runs |
| `apps/web/src/features/runs/` | Run summary, occurrence history, stage navigation, evidence |
| `apps/web/src/features/review/` | Queue, filters, detail, and selection behavior |
| `apps/web/src/test/` | Render helpers, deterministic API fixtures, axe helper |
| `apps/web/e2e/` | Keyboard, responsive, routing, and complete local-flow Playwright checks |

### Task 1: Web project, providers, and Geist design tokens

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/index.html`
- Create: `apps/web/src/main.tsx`
- Create: `apps/web/src/app/providers.tsx`
- Create: `apps/web/src/styles/tokens.css`
- Create: `apps/web/src/styles/global.css`
- Create: `apps/web/src/test/render.tsx`
- Create: `apps/web/src/app/providers.test.tsx`

**Interfaces:**
- Produces: `AppProviders`, shared render helper, design-token contract
- Consumes: `/api` base URL from Vite environment with local default

- [ ] **Step 1: Write a failing provider smoke test**

```tsx
test('renders application content with query and router providers', () => {
  renderApp(<h1>Workspace</h1>, { route: '/' });
  expect(screen.getByRole('heading', { name: 'Workspace' })).toBeVisible();
});
```

- [ ] **Step 2: Run the smoke test and verify setup failure**

Run: `cd apps/web && bun test src/app/providers.test.tsx`

Expected: FAIL because project configuration and providers are absent.

- [ ] **Step 3: Configure strict TypeScript, Vite, Vitest, and dependencies**

Scripts must include `dev`, `build`, `test`, `test:watch`, `typecheck`, and `e2e`. Vitest uses `jsdom`, a setup file extending DOM matchers, and restores mocks after every test.

- [ ] **Step 4: Implement providers and global tokens**

Define tokens for neutral surfaces, borders, text, semantic green/amber/red/purple/grey, focus ring, nested radii, spacing, type scale, target size, and motion. Global CSS includes visible `:focus-visible` and a reduced-motion media query.

- [ ] **Step 5: Run smoke, type, and build checks**

Run: `cd apps/web && bun test src/app/providers.test.tsx && bun run typecheck && bun run build`

Expected: PASS without warnings.

- [ ] **Step 6: Commit web foundation**

```bash
git add apps/web
git commit -m "feat: add operator web foundation"
```

### Task 2: Typed API client and URL scope state

**Files:**
- Create: `apps/web/src/api/types.ts`
- Create: `apps/web/src/api/client.ts`
- Create: `apps/web/src/api/queries.ts`
- Create: `apps/web/src/app/scope.ts`
- Create: `apps/web/src/app/scope.test.ts`
- Create: `apps/web/src/test/fixtures.ts`

**Interfaces:**
- Produces: `FileScope`, `parseScope(searchParams)`, `writeScope(scope)`
- Produces: `workspaceQuery(scope)`, `runQuery(runId, reviewId)`, `reviewQuery(scope, filters)`

- [ ] **Step 1: Write failing exact-scope tests**

```tsx
test.each([
  ['current', { kind: 'current', runId: RUN_1 }],
  ['selected', { kind: 'selected', runIds: [RUN_1, RUN_2] }],
  ['all', { kind: 'all' }],
])('round trips %s scope', (_, scope) => {
  expect(parseScope(writeScope(scope))).toEqual(scope);
});
```

- [ ] **Step 2: Run scope tests and verify failure**

Run: `cd apps/web && bun test src/app/scope.test.ts`

Expected: FAIL because scope functions are absent.

- [ ] **Step 3: Implement strict API response types and fetch errors**

Mirror the API contract exactly. `ApiError` includes HTTP status, stable code, message, and details. Reject structurally invalid JSON at the boundary with a plain application-unavailable error.

- [ ] **Step 4: Implement scope parsing and query keys**

Reject selected scope without IDs, current scope without one ID, and malformed UUIDs. Canonicalize selected IDs by stable de-duplication while preserving first selection order.

- [ ] **Step 5: Run API/scope tests and typecheck**

Run: `cd apps/web && bun test src/app/scope.test.ts && bun run typecheck`

Expected: PASS.

- [ ] **Step 6: Commit API and scope state**

```bash
git add apps/web/src/api apps/web/src/app/scope.ts apps/web/src/app/scope.test.ts apps/web/src/test/fixtures.ts
git commit -m "feat: add typed read api client"
```

### Task 3: Workspace hierarchy and scope selection

**Files:**
- Create: `apps/web/src/features/workspace/WorkspacePage.tsx`
- Create: `apps/web/src/features/workspace/FileScopeControl.tsx`
- Create: `apps/web/src/features/workspace/SelectedFilePicker.tsx`
- Create: `apps/web/src/features/workspace/workspace.module.css`
- Create: `apps/web/src/features/workspace/WorkspacePage.test.tsx`

**Interfaces:**
- Consumes: `WorkspaceResponse`, URL `FileScope`
- Produces: scope changes through router search params; no local shadow copy of scope

- [ ] **Step 1: Write failing `UI-01` through `UI-04` tests**

Assert section document order, exact option labels, conditional selected-file picker, removable filename controls, and closed selected count.

- [ ] **Step 2: Run workspace hierarchy tests and verify failure**

Run: `cd apps/web && bun test src/features/workspace/WorkspacePage.test.tsx`

Expected: FAIL because workspace components are absent.

- [ ] **Step 3: Implement semantic workspace shell**

Use one `<main>`, a single page `<h1>`, and ordered `<section aria-labelledby>` blocks. Fetch one workspace response for the active scope; child sections receive response data and do not issue their own count queries.

- [ ] **Step 4: Implement scope and selected-file controls**

Use a labeled radio/select control for the exact three options. Selected files use a searchable listbox with removable buttons and an announced count. Switching away removes selected IDs from the URL.

- [ ] **Step 5: Run workspace hierarchy and keyboard tests**

Run: `cd apps/web && bun test src/features/workspace/WorkspacePage.test.tsx`

Expected: `UI-01` through `UI-04` PASS.

- [ ] **Step 6: Commit workspace scope UI**

```bash
git add apps/web/src/features/workspace
git commit -m "feat: add scoped workspace overview"
```

### Task 4: Breakdown chart, outstanding review, and recent runs

**Files:**
- Create: `apps/web/src/features/workspace/OutcomeDonut.tsx`
- Create: `apps/web/src/features/workspace/OutstandingReview.tsx`
- Create: `apps/web/src/features/workspace/RecentRuns.tsx`
- Create: `apps/web/src/features/workspace/OutcomeDonut.test.tsx`
- Modify: `apps/web/src/features/workspace/WorkspacePage.test.tsx`

**Interfaces:**
- Produces: accessible SVG chart with persistent legend and summary
- Produces: file links `/runs/:runId?review=:reviewItemId`

- [ ] **Step 1: Write failing `UI-05`, `UI-09`, `UI-10`, and `UI-11` tests**

Assert review filename routing, absolute timestamps, chart focus/hover equivalence, segment/legend parity, and visible status text alongside color.

- [ ] **Step 2: Run breakdown tests and verify failure**

Run: `cd apps/web && bun test src/features/workspace/OutcomeDonut.test.tsx src/features/workspace/WorkspacePage.test.tsx`

Expected: FAIL because breakdown components are absent.

- [ ] **Step 3: Implement owned SVG donut**

Use a padded view box, focusable segment buttons or paired legend controls, and a live detail line. Keep the total in the center, exact counts in the legend, and a visually hidden textual summary. Segment activation navigates to the review route with scope and filter.

- [ ] **Step 4: Implement compact review and run tables**

Outstanding review uses real anchors for filenames. Recent runs uses a semantic table, status dot plus text, segmented outcome bar with accessible labels, absolute timestamp text, and row link.

- [ ] **Step 5: Run workspace breakdown tests**

Run: `cd apps/web && bun test src/features/workspace`

Expected: `UI-05`, `UI-09`, `UI-10`, and `UI-11` PASS.

- [ ] **Step 6: Commit workspace evidence UI**

```bash
git add apps/web/src/features/workspace
git commit -m "feat: show workspace outcomes and runs"
```

### Task 5: Run page, occurrence history, and pipeline evidence

**Files:**
- Create: `apps/web/src/features/runs/RunPage.tsx`
- Create: `apps/web/src/features/runs/OccurrenceHistory.tsx`
- Create: `apps/web/src/features/runs/PipelineStages.tsx`
- Create: `apps/web/src/features/runs/EvidencePanel.tsx`
- Create: `apps/web/src/features/runs/run.module.css`
- Create: `apps/web/src/features/runs/RunPage.test.tsx`

**Interfaces:**
- Consumes: `RunDetailResponse`, optional review query parameter
- Produces: selected stage state initialized from current problematic stage

- [ ] **Step 1: Write failing `UI-06` through `UI-08` and `UI-17` tests**

Assert stage reachability, keyboard stage selection, evidence replacement, required normalisation wording, absence of reversal wording, and duplicate-submission occurrence disclosure.

- [ ] **Step 2: Run run-page tests and verify failure**

Run: `cd apps/web && bun test src/features/runs/RunPage.test.tsx`

Expected: FAIL because run components are absent.

- [ ] **Step 3: Implement run summary and occurrence history**

Show primary filename, run ID, record count, and absolute time. For multiple occurrences show “This exact file was submitted N times” and an expandable list of filename, actor, relation, and absolute ingest timestamp.

- [ ] **Step 4: Implement pipeline stages and evidence panel**

Use a tablist only if keyboard behavior follows the ARIA tab pattern; otherwise use buttons with `aria-pressed`. Disable future stages. Stage activation updates heading, result, facts, evidence, and timestamp and announces the new panel heading.

- [ ] **Step 5: Run run-page tests**

Run: `cd apps/web && bun test src/features/runs/RunPage.test.tsx`

Expected: `UI-06`, `UI-07`, `UI-08`, and `UI-17` PASS.

- [ ] **Step 6: Commit run evidence UI**

```bash
git add apps/web/src/features/runs
git commit -m "feat: add run evidence and duplicate history"
```

### Task 6: Review queue and detail panel

**Files:**
- Create: `apps/web/src/features/review/ReviewPage.tsx`
- Create: `apps/web/src/features/review/ReviewFilters.tsx`
- Create: `apps/web/src/features/review/ReviewQueue.tsx`
- Create: `apps/web/src/features/review/ReviewDetail.tsx`
- Create: `apps/web/src/features/review/review.module.css`
- Create: `apps/web/src/features/review/ReviewPage.test.tsx`

**Interfaces:**
- Consumes: scoped `ReviewQueueResponse`
- Produces: URL-backed selected review ID and filters

- [ ] **Step 1: Write failing queue-context and keyboard tests**

Select a review item, change pipeline stage, move to the next item, and assert scope/filter URL parameters remain. Assert the selected item's reason, raw/interpreted values, dependencies, and provenance are visible.

- [ ] **Step 2: Run review tests and verify failure**

Run: `cd apps/web && bun test src/features/review/ReviewPage.test.tsx`

Expected: FAIL because review components are absent.

- [ ] **Step 3: Implement URL-backed queue selection**

Use real links so browser navigation works. After keyboard selection, focus the detail heading only when navigation originated in the queue; browser Back restores queue focus to the selected row.

- [ ] **Step 4: Implement plain-language detail with expandable technical evidence**

Primary content shows factual reason, impact, source and interpreted values, and dependency. Registered issue code, revision IDs, raw arrays, and rules version live in `<details>`.

- [ ] **Step 5: Run review tests**

Run: `cd apps/web && bun test src/features/review/ReviewPage.test.tsx`

Expected: PASS.

- [ ] **Step 6: Commit review queue UI**

```bash
git add apps/web/src/features/review
git commit -m "feat: add persistent review queue"
```

### Task 7: Loading, empty, failure, stale, and overflow states

**Files:**
- Create: `apps/web/src/components/StatePanel.tsx`
- Create: `apps/web/src/components/Skeleton.tsx`
- Create: `apps/web/src/components/TechnicalDetails.tsx`
- Create: `apps/web/src/components/components.module.css`
- Create: `apps/web/src/app/states.test.tsx`
- Modify: workspace, run, and review page components

**Interfaces:**
- Produces shared state components with screen-specific title, explanation, and next action

- [ ] **Step 1: Write failing `UI-12` and `UI-13` tests**

Render initial loading, partial progress, empty workspace, empty filter, staged success, active run, stage failure, load failure, stale data, API unavailable, and long-value fixtures. Assert distinct language and actions.

- [ ] **Step 2: Run state tests and verify failure**

Run: `cd apps/web && bun test src/app/states.test.tsx`

Expected: FAIL because state components are absent.

- [ ] **Step 3: Implement shape-matched skeletons and state panels**

Skeletons approximate final blocks and carry `aria-busy` on their region. Errors state what happened, effect, and next action. Stale data remains visible with an announced refresh indicator.

- [ ] **Step 4: Implement resilient overflow behavior**

Truncate visual filenames with CSS while retaining full accessible name and title. Wrap long evidence values. At narrow widths preserve filename, reason, and current status before secondary metadata.

- [ ] **Step 5: Run state and responsive component tests**

Run: `cd apps/web && bun test src/app/states.test.tsx`

Expected: `UI-12` and `UI-13` PASS.

- [ ] **Step 6: Commit screen states**

```bash
git add apps/web/src/components apps/web/src/app/states.test.tsx apps/web/src/features
git commit -m "feat: handle operator interface states"
```

### Task 8: Keyboard, reduced motion, and automated accessibility

**Files:**
- Create: `apps/web/src/app/accessibility.test.tsx`
- Create: `apps/web/src/test/axe.ts`
- Modify: `apps/web/src/styles/global.css`
- Modify: components identified by tests

**Interfaces:**
- Verifies `UI-14`, `UI-15`, and `UI-16`

- [ ] **Step 1: Write the complete keyboard-flow test**

Use `userEvent.tab()` and keyboard activation to change scope, select two files, open review work, change stage, and return. Assert `document.activeElement` and visible focus class at each step.

- [ ] **Step 2: Write reduced-motion and axe tests**

Mock reduced motion and assert nonessential transition durations resolve to zero. Run axe against workspace, run, and review default/loading/empty/failure fixtures.

- [ ] **Step 3: Run accessibility tests and record failures**

Run: `cd apps/web && bun test src/app/accessibility.test.tsx`

Expected: FAIL on the first unimplemented focus, motion, or semantic issue rather than being skipped.

- [ ] **Step 4: Apply minimal semantic and focus fixes**

Prefer native buttons, links, inputs, tables, headings, and details. Add ARIA only where native semantics do not express the relationship. Keep tooltip content duplicated in visible or accessible text.

- [ ] **Step 5: Run all component tests**

Run: `cd apps/web && bun test`

Expected: `UI-01` through `UI-17` PASS with zero axe violations.

- [ ] **Step 6: Commit accessibility behavior**

```bash
git add apps/web/src
git commit -m "feat: complete keyboard and accessible behavior"
```

### Task 9: End-to-end routes and responsive visual verification

**Files:**
- Create: `apps/web/playwright.config.ts`
- Create: `apps/web/e2e/operator-flow.spec.ts`
- Create: `apps/web/e2e/responsive.spec.ts`
- Create: `apps/web/e2e/screenshots/.gitkeep`

**Interfaces:**
- Consumes: running FastAPI and Vite applications populated by deterministic test seed
- Verifies route/state integration without adding new product behavior

- [ ] **Step 1: Write failing end-to-end operator flow**

Navigate from all-files workspace to selected files, open an outstanding filename, verify the selected review query parameter, inspect normalisation details, and return to the filtered review queue.

- [ ] **Step 2: Write responsive and chart-clipping checks**

At 1440×900, 1024×768, and 390×844, assert no horizontal page overflow, key text remains visible, donut segments fit inside the SVG bounding box, and focused chart/tooltip content remains inside the viewport.

- [ ] **Step 3: Run Playwright and verify failure before configuration is complete**

Run: `cd apps/web && bun run e2e`

Expected: FAIL because server/seed wiring or tests are not yet complete.

- [ ] **Step 4: Configure deterministic E2E seed and web servers**

Use the processed golden fixture database snapshot. Playwright starts FastAPI and Vite on fixed test ports, waits for `/api/health`, and never reaches an external network host.

- [ ] **Step 5: Run end-to-end verification**

Run: `cd apps/web && bun run e2e`

Expected: all route, keyboard, responsive, and clipping checks PASS.

- [ ] **Step 6: Commit end-to-end coverage**

```bash
git add apps/web/playwright.config.ts apps/web/e2e
git commit -m "test: verify operator journeys and layouts"
```

### Task 10: Complete application quality gate

**Files:**
- Create: `scripts/quality_gate.py`
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `apps/web/package.json`

**Interfaces:**
- Produces one local command that runs every required deterministic check

- [ ] **Step 1: Add a failing quality-gate smoke test**

The script runs commands sequentially, stops on the first failure, preserves output, and returns the failing exit code. Its command list is asserted in a unit test so required suites cannot disappear silently.

- [ ] **Step 2: Implement the exact quality gate**

Run, in order:

```text
pytest -q tests/unit
pytest -q tests/integration/test_messy_sample_data.py
pytest -q tests/integration/foundation tests/integration/pipeline tests/integration/api
ruff check services tests scripts
pyright services
bun --cwd apps/web test
bun --cwd apps/web run typecheck
bun --cwd apps/web run build
bun --cwd apps/web run e2e
git diff --check
```

- [ ] **Step 3: Document local setup and run commands**

README instructions cover `uv sync`, `bun install`, Postgres startup, migrations, pinned FX import, fixture ingest/process, FastAPI, Vite, and the quality gate. Commands must be copy-pasteable and use no unpublished secrets.

- [ ] **Step 4: Run the complete quality gate**

Run: `python3 scripts/quality_gate.py`

Expected: every command PASS with no unexpected errors or warnings.

- [ ] **Step 5: Verify the working tree contains only intended changes**

Run: `git status --short && git diff --check`

Expected: no generated runtime data, caches, screenshots, or build output are tracked.

- [ ] **Step 6: Commit final verification and documentation**

```bash
git add scripts/quality_gate.py README.md pyproject.toml apps/web/package.json
git commit -m "chore: add complete application quality gate"
```
