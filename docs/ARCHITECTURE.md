# Architecture: local CSV ingestion and review

**Status:** Draft canonical implementation specification — pending owner review
**Consolidated:** 16 September 2026
**Scope:** Local CSV → frozen source → parse → normalise → classify → persist issues, review items, and staged canonical candidates → display the run

Deferred production and governance work is tracked in [`TODO.md`](../TODO.md).

## Document authority

This document is the single implementation specification for the current local ingestion-and-review slice. It incorporates the resolved architecture review and the approved UI requirements. Its requirements supersede earlier proposals, mockups, review notes, and conflicting descriptions of this slice.

The authority order is:

1. this document for current-slice behavior and acceptance criteria;
2. [`TODO.md`](../TODO.md) for explicitly deferred work;
3. the sample schema and fixtures as test inputs, not as independent product policy.

There are no open architecture or UI questions in the current specification. Items that require later product decisions remain outside this slice in `TODO.md`. Changes found during owner review must amend this document directly.

## 1. System boundary

The first slice proves the data loop locally. A CLI starts a run from a filesystem CSV. Python application services own parsing, normalisation, classification, persistence, and retry behavior. A local web interface provides the workspace overview, run details, and review queue through a local application API. Postgres stores application state; immutable source bytes live in content-addressed local storage.

```text
filesystem CSV
      |
      v
CLI / local application API
      |
      v
ingest -> parse -> normalise -> classify -> atomic staged load
  |         |          |             |             |
  v         v          v             v             v
source   raw rows   candidate     verdicts,     staged canonical
bytes                revisions     issues,       revisions
                                  review items
                        |
                        v
              run detail + review display
```

The pipeline has no dependency on HTTP or UI objects. The CLI and local API call the same application services.

## 2. Data representations

### Frozen source

The ingested file is stored byte-for-byte under its SHA-256 hash. `source_file` represents unique content. `source_occurrence` records each supplied filename, original locator, ingest time, and actor label against that content. Reusing identical bytes does not destroy per-ingest metadata.

### Raw records

Parse writes immutable records for data rows, headers, repeated headers, and blank lines. Each record stores inclusive `source_line_start` and `source_line_end`, ordered parsed fields, field count, kind, and parse metadata. The discriminator is derived from the first field instead of stored twice.

### Candidate revisions

Derived interpretations live separately from raw evidence. One raw data record has one initial candidate revision and may have later append-only automatic-repair revisions. Every later revision names its parent and origin.

Candidate fields use this logical shape:

```text
{
  state: known | absent | deferred | unresolved,
  value: typed value only when state = known,
  source_refs: [...],
  transformation_refs: [...],
  issue_refs: [...]
}
```

Candidates are a discriminated union: `CustomerCandidate | ProductCandidate | OrderCandidate`. An invalid or absent discriminator produces a rejected candidate shell with source evidence; the pipeline never invents an entity type.

### Classification results

A verdict is an immutable assessment of one candidate revision under one rules version and FX snapshot. It never modifies the raw record.

```text
classification_result(
  id, candidate_revision_id, rules_version, fx_snapshot_id,
  verdict, evaluated_at
)
```

Linked issues explain transformations, repairs, unresolved fields, and failed rules.

### Staged canonical data

Canonical identity uses an internal UUID. Business identifiers are governed attributes. Canonical revisions are separate from identity so history can coexist without overwriting earlier interpretations. This slice creates staged revisions only.

## 3. Pipeline

### 3.1 Ingest

Ingest reads a filesystem source, freezes and hashes its bytes, creates or reuses `source_file`, creates a `source_occurrence`, and opens a new run. It does not parse CSV content.

```text
IngestInput = {
  source: FilesystemPath,
  filename,
  actor_label,
  idempotency_key?,
  requested_fx_snapshot_date?
}

IngestOutput = {
  source_file_id,
  source_occurrence_id,
  run_id,
  source_reused
}
```

The same idempotency key returns the same run. An explicit reprocess creates a new run referencing the same frozen content.

### 3.2 Parse

Parse handles the BOM, blank lines, repeated headers, quoted multi-line fields, short rows, long notes, and empty trailing fields. It persists atomic, idempotent batches and records a durable checkpoint after each batch.

```text
RawRecord = {
  id,
  run_id,
  source_line_start,
  source_line_end,
  kind: data | blank | header | repeated_header,
  fields: string[],
  field_count,
  parse_metadata
}
```

### 3.3 Normalise

Normalise creates the initial typed candidate revision. It owns transformations that require only the current record: parsing, trimming, status mappings, date and money conversion, tags, match keys, and currency conversion from stored FX rates.

Routine lossless mechanics are parse provenance. Deterministic representation changes are transformation events. A data-quality issue is created for lossy changes, semantic repairs, FX conversions that require explanation, ambiguity, unresolved input, or rule violations.

`TBD` is `deferred`; an empty field is `absent`; an unparseable value such as `many` is `unresolved`.

### 3.4 Classify

Classification is a barrier: every data record must have an initial candidate revision before cross-record rules run. Classify evaluates customer, product, order, referral, stock/status, refund, and duplicate relationships. Explicit deterministic rules may append repaired candidate revisions. Anything outside registered rules becomes human-review work.

Each issue code declares severity, applicability, and whether it has an automatic repair. Validation-only failures have no automatic repair. Field-level normalisation and cross-record classification are labelled separately in provenance and the UI.

`rules_version` is a deterministic content hash of the registry and relevant normalisation configuration. The application build revision is stored separately.

### 3.5 Load

Load runs after classification and stages the complete eligible set in one transaction.

| Verdict | Initial load behavior |
|---|---|
| `CLEAN` | Create a staged canonical revision |
| `AUTO_REPAIRED` | Create a staged canonical revision with repair lineage |
| `NEEDS_REVIEW` | Keep outside canonical tables and create a review item |
| `REJECTED` | Keep outside canonical tables and create a review item with rejection reasons |
| `DUPLICATE` | Retain both raw occurrences, keep one canonical identity, and create a review item for the later occurrence |

Dependencies are loaded in order. A clean order whose customer or product is unresolved is `blocked_by_dependency`, remains outside canonical tables, and receives an explanatory dependency record. Its own verdict and readiness remain separate concepts.

## 4. Duplicate and conflict rules

An exact duplicate has identical parsed raw fields before normalisation, excluding source line numbers. Quoting differences that parse to the same fields do not distinguish records; whitespace differences inside field values do.

| Event | Treatment |
|---|---|
| Same run, identical parsed raw fields | `DUPLICATE`; retain both and review the later occurrence |
| Same run, same business ID with different fields | Conflict review item |
| Earlier run has same identity and same derived values | Re-observation linked to the existing identity |
| Earlier run has same identity and different values | Conflict review item; never overwrite automatically |

## 5. Resumability and failure behavior

Parse and normalise use configurable atomic batches. Their writes have deterministic identities, making retries idempotent. A worker resumes after the last committed checkpoint.

Classification restarts for the whole run because it needs the complete candidate set. Results are keyed by candidate revision and rules version, so rerunning it creates no duplicate verdicts or issues.

Canonical loading is atomic. A failed load rolls back all staged canonical writes while leaving frozen sources, raw records, candidate revisions, issues, classifications, and checkpoints available for retry. `classified` with `load_failed` is a valid recoverable state.

Run processing states include stage-specific failure states and the last completed checkpoint. A failed parse batch is invisible; earlier committed batches remain inspectable as part of an incomplete run.

## 6. Stored state and ownership

| State | Sole owner | Mutation model |
|---|---|---|
| `source_file`, `source_occurrence` | ingest | Append-only |
| `run`, `pipeline_checkpoint` | pipeline orchestrator | Constrained stage transitions |
| `raw_record` | parse | Append-only |
| `candidate_revision` | candidate revision service | Append-only |
| `classification_result`, `data_quality_issue` | classify | Append-only per revision/rules version |
| `review_item` | review service | Created with stable reasons and source references |
| canonical identity/revision | canonical command service | Identity retained; revisions append-only |
| `fx_rate` | FX fixture importer | Immutable once referenced by a run |
| pipeline event log | event writer | Append-only |

`run.counts` is an immutable end-of-classification snapshot.

## 7. FX data

The first slice seeds a pinned ECB-history fixture with provenance and versioned rate rows. The pipeline never performs a network fetch. Orders use the stored rate for their order date; lifetime spend uses the run's fixed snapshot. Missing rates preserve the source amount, leave the GBP value unresolved, and create `FX_RATE_UNAVAILABLE`.

## 8. Operator interface

The interface is a human adapter over application services. It does not own data, classification rules, review reasons, or pipeline state. The primary user is a nontechnical operations reviewer who needs to understand what happened, what needs attention, and where each record sits in the pipeline.

The web application is desktop-first and responsive. Its product mode is **Operate**: reviewers inspect results and exceptions rather than configure the underlying rules.

### 8.1 Visual system

- Use Vercel's Geist design language: Geist Sans and Geist Mono, neutral backgrounds, crisp borders, disciplined spacing, and high-contrast controls.
- Use rounded cards and compact rounded controls with nested radii.
- Keep the interface moderately dense and evidence-led. Expose technical depth progressively.
- Reserve semantic colors for processing and review states.
- Pair every semantic color with text, an icon, a pattern, or accessible text; color is never the sole state indicator.
- Avoid decorative dashboard cards, gradients, glass effects, and motion that does not explain a state change.

### 8.2 Language and evidence

The presentation layer uses plain operational language while storage and technical evidence preserve raw internal values. Display mappings live in one typed label module with a readable fallback for newly introduced values.

| Internal value | Display label |
|---|---|
| `NEEDS_REVIEW` | Needs attention |
| `AUTO_REPAIRED` | Repaired automatically |
| `blocked_by_dependency` | Waiting for another record |
| `INVALID_INTEGER` | A number was expected |

Error copy explains what happened, its effect, and the available next step. Suspected causes are explicitly labelled as tentative. Review reasons contain registered issue IDs, affected field paths, factual summaries, source references, and dependency or conflict references.

### 8.3 Workspace overview

The landing page combines:

- outstanding review work;
- failed or blocked processing;
- an outcome breakdown for the active file scope;
- recent individual runs.

It must make the next operational action apparent without requiring the reviewer to understand the underlying data model.

### 8.4 Run page

Every file occurrence/run has a stable page showing:

- exact filename, run ID, record count, and absolute processing timestamp with timezone;
- outcome breakdown;
- processing-stage status;
- records and review items belonging to the run;
- selected-record details;
- expandable raw evidence, candidate revisions, transformations, issues, verdicts, provenance, and relationships.

### 8.5 Review queue

The review surface uses a persistent queue with a detail panel. Moving between items preserves queue and filter context. The detail panel explains the plain-language reason, raw and interpreted values, pipeline position, dependencies, provenance, and technical evidence.

Outstanding-review items show:

- a plain-language reason;
- business identifier;
- linked filename;
- source line where applicable;
- review state.

The filename links to `/runs/:runId?review=:reviewItemId`, opening the individual run while retaining the selected review item. Link styling remains visibly interactive without relying only on color.

### 8.6 File scope

All aggregate counts, charts, filters, and queue results inherit one explicit scope:

- **Current file:** the run currently open.
- **Selected files:** a reviewer-defined set chosen through a searchable multi-select.
- **All files:** every available run.

The file-selection panel appears only for **Selected files**. Selected filenames appear as removable chips, and the closed control reports the selected count. The visible page heading and chart label repeat the active scope.

### 8.7 Outcome breakdown

- Place a color-coded donut chart on the right of the main breakdown.
- State the file scope and total record count in the chart title.
- Reveal the exact label, count, and percentage for each segment on pointer hover and keyboard focus.
- Mirror all segments in a persistent text legend and an accessible non-chart summary.
- Reserve enough SVG view-box and container padding to prevent emphasized segments and tooltips from being clipped.
- Make each count and legend item open the review queue with the corresponding scope and filter.

### 8.8 Recent runs

Use a compact table rather than a collection of status pills. Its columns are:

- file;
- primary status shown as a semantic dot plus text;
- segmented outcome bar;
- absolute processed timestamp with timezone;
- row-navigation affordance.

A compact color key appears in the section header. Every outcome segment exposes its exact label and count on hover and focus and has an accessible text equivalent.

### 8.9 Pipeline-stage details

Pipeline position and review outcome are independent state axes.

- Completed and current stages are interactive only when details exist.
- Selecting a stage replaces the evidence panel with that stage's facts, result, and absolute timestamp.
- The current problematic stage is selected by default.
- Future stages are disabled, greyed out, and labelled as not reached.
- The normalisation interaction is labelled **Normalisation details**. It is never called “de-normalise,” because it displays evidence rather than reversing data.

For a normalisation problem, display:

- the exact source value;
- expected type or domain;
- interpreted field state;
- plain-language failure reason;
- issue code and candidate revision in expandable technical details;
- processing timestamp with timezone.

### 8.10 Controls and timestamps

- Use rounded shapes for interactive controls.
- Render timestamps as plain Geist-grey text rather than pills.
- Do not use relative timestamps such as “Just now” or “Today” in evidence or processing views.
- Display the absolute local date, time, and timezone; preserve the machine timestamp in technical details or a tooltip.
- Search and filter fields use light-grey surfaces, neutral borders, dark text, and visible focus rings.

### 8.11 Required interface states

Every relevant screen and component defines:

- initial loading with shape-matched skeletons;
- partial batch progress;
- empty workspace;
- empty filtered results;
- successful staged run;
- run in progress;
- recoverable stage failure;
- load failure after classification;
- unresolved review item;
- rejected structural record;
- duplicate;
- conflict;
- dependency-blocked record;
- stale data or refetch state;
- API unavailable;
- long filenames and overflowing values;
- hover, keyboard focus, active, and disabled control states.

### 8.12 Accessibility and interaction acceptance criteria

- Every flow is keyboard-operable with visible `:focus-visible` treatment.
- Selection and stage changes move or announce focus predictably.
- Tooltips are keyboard-reachable and contain no essential information unavailable elsewhere.
- Charts include text legends and accessible summaries.
- Controls use semantic HTML before ARIA.
- Interactive targets are at least 24 px visually or through an expanded hit area; mobile targets are at least 44 px.
- Motion honors `prefers-reduced-motion` and is limited to user-triggered state changes.
- Responsive layouts preserve filename, reason, and current status before secondary metadata.

### 8.13 Design and implementation method

The Vercel/Geist direction is fixed for this operational interface. The design workflow is:

1. research and reference lock with Refero;
2. surface and state shaping with Impeccable;
3. a plain-language pass with Humanizing UI Jargon;
4. an anti-template critique with Frontend Design;
5. interaction feedback with Emil Design Engineering;
6. purposeful motion only after workflows are stable, followed by motion review;
7. joint desktop and mobile validation;
8. accessibility, responsive, empty/error-state, and final-polish passes;
9. optional Figma transfer or implementation from an approved Figma source.

Awesome Designs may be used to audit the selected direction but does not replace it. Taste Skill is reserved for non-dashboard surfaces such as landing, editorial, portfolio, and marketing pages.

## 9. Repository layout

```text
apps/web/                 operator workspace, run, and review interface
services/api/             local application API adapters
services/pipeline/        ingest, parse, normalise, classify, load, rules, FX
services/application/     orchestration and state-owning command services
db/migrations/            Postgres schema
data/                     sample CSV, schema, pinned FX fixture
var/sources/              content-addressed local source bytes (runtime data)
docs/                     canonical architecture specification
```

## 10. Build order

1. Domain types, structured candidate values, rule registry, and deterministic IDs.
2. Filesystem ingest and immutable content-addressed source storage.
3. Resumable parse and normalise with checkpoints.
4. Barrier classification, issues, repairs, duplicates, and conflicts.
5. Atomic staged canonical load and dependency blocking.
6. Local API, workspace overview, run-detail view, and review queue.
7. Required UI states, accessibility behavior, and responsive behavior.
8. Golden fixture and failure/retry tests for the complete local loop.

The golden result must explain every physical source line and trace each staged or reviewed outcome through raw evidence, candidate revisions, rules, issues, and classification.
