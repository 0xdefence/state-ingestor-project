# Architecture: local CSV ingestion and review

**Status:** Canonical MVP specification — local actionable-review MVP implemented and verified on 18 September 2026
**Consolidated:** 16 September 2026
**Amended:** 17 September 2026
**Scope:** Local CSV → process → inspect exceptions → decide → promote approved revisions while preserving complete lineage

Deferred production and governance work is tracked in [`TODO.md`](../TODO.md).

## Document authority

This document is the single implementation specification for the current local ingestion-and-review slice. It incorporates the resolved architecture review and the approved UI requirements. Its requirements supersede earlier proposals, mockups, review notes, and conflicting descriptions of this slice.

The authority order is:

1. this document for current-slice behavior and acceptance criteria;
2. [`TODO.md`](../TODO.md) for explicitly deferred work;
3. the sample schema and fixtures as test inputs, not as independent product policy.

There are no open architecture or UI questions in the current specification. Items that require later product decisions remain outside this slice in `TODO.md`. Changes found during owner review must amend this document directly.

## 1. System boundary

The first slice proves the complete operator loop locally. A nontechnical operations reviewer uploads a CSV, processes it, inspects exceptions, records whole-record decisions, and sees approved revisions become current canonical state. Python application services own parsing, normalisation, classification, persistence, retry, decisions, and promotion. Postgres stores application state; immutable source bytes live in content-addressed local storage.

```text
browser or CLI CSV
      |
      v
local application API
      |
      v
ingest -> parse -> normalise -> classify -> automatic promotion
  |         |          |             |               |
  v         v          v             v               v
source   raw rows   candidate     verdicts,     canonical history
bytes                revisions     issues,       + current view
                                  review items
                                      |
                                      v
                              operator decision
                                      |
                                      v
                           promotion or no change
```

The pipeline has no dependency on HTTP or UI objects. The CLI and local API call the same application services.

## 2. Data representations

### Frozen source

The ingested file is stored byte-for-byte under its SHA-256 hash. `source_file` represents unique content. `source_occurrence` records each supplied filename, original locator, ingest time, and actor label against that content. `run_source_occurrence` links every occurrence to the run that represents it and identifies whether that occurrence initiated the run or was a duplicate submission. Reusing identical bytes does not destroy per-ingest metadata and does not create another run by default.

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

### Canonical data

Canonical identity uses an internal UUID. Business identifiers are governed attributes. Canonical revisions are separate from identity so history can coexist without overwriting earlier interpretations. Clean and registered auto-repaired records promote automatically. An approved exception appends a revision; a rebuildable `canonical_current` projection identifies the effective revision. Decisions, revisions, and promotion events are append-only.

## 3. Pipeline

### 3.1 Ingest

Ingest reads a filesystem source, freezes and hashes its bytes, creates or reuses `source_file`, and always records a new `source_occurrence` unless the same idempotency key is being replayed. It then locks the source-file row and either opens the first run for that content or links the occurrence to the most recent run for that source. It does not parse CSV content.

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
  source_reused,
  run_reused,
  duplicate_upload,
  resume_from_checkpoint?
}
```

The same idempotency key returns the same occurrence and run without creating any new record. A later submission with identical bytes but a different filename or idempotency key creates a new occurrence, links it to the existing run, returns `duplicate_upload=true`, and performs no pipeline work that has already completed. If processing is requested and that run is incomplete or failed, the orchestrator resumes the same run from its durable boundary. An explicit `reprocess` command is the only way to create a new run for already-known content; that run references its predecessor and becomes the run reused by later duplicate submissions.

Duplicate detection is based on exact bytes, not filename. The same filename with different bytes creates a new source file and run. Source-file locking plus database uniqueness constraints serialize concurrent identical submissions so they cannot open two initial runs.

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

### 3.5 Load and automatic promotion

Load runs after classification and promotes the complete eligible set in one transaction.

The existing persisted terminal run state remains `staged`; the UI labels it **Processed**. The replacement migration adds activation events and `canonical_current` entries for eligible revisions already written by the loader, so the internal state name does not create a second lifecycle.

| Verdict | Initial load behavior |
|---|---|
| `CLEAN` | Create and activate a canonical revision |
| `AUTO_REPAIRED` | Create and activate a canonical revision with repair lineage |
| `NEEDS_REVIEW` | Keep outside canonical tables and create a review item |
| `REJECTED` | Keep outside canonical tables and create a review item with rejection reasons |
| `DUPLICATE` | Retain both raw occurrences, keep one canonical identity, and create a review item for the later occurrence |

Dependencies are loaded in order. A clean order whose customer or product is unresolved is `blocked_by_dependency`, remains outside canonical tables, and receives an explanatory dependency record. It appears as **Waiting for another record**, not as an approvable exception. Promoting the referenced dependency re-evaluates its blocked dependants in the same command flow and automatically promotes any that become eligible. Its own verdict and readiness remain separate concepts.

### 3.6 Review and decide

Review operates on a complete record, not on individual issues. The displayed evidence and submitted command name the exact terminal candidate revision. Legal actions depend on the classified outcome:

| Outcome | Legal actions | Result |
|---|---|---|
| `NEEDS_REVIEW` or changed-value conflict | Approve, reject | Approval promotes the candidate; rejection preserves existing canonical state |
| `REJECTED` | Acknowledge, reject | Records a reviewed outcome; candidate remains permanently outside canonical data |
| `DUPLICATE` | Acknowledge, reject | Records a reviewed outcome; later duplicate remains outside canonical data |

The operator name is always recorded. Rejection requires a reason. Approval, acknowledgement, and reversal may include one. Each command has an idempotency key and expected decision sequence.

The decision service locks the review item and rechecks its terminal revision, classification, dependency readiness, current decision sequence, and relevant canonical identity. It appends the decision and any canonical revision/promotion event in one transaction. Stale requests return `409 Conflict` and make no change.

Approving a new identity creates canonical revision 1. Approving changed values for an existing identity appends the next revision, preserves the previous revision, and makes the approved revision current. A reversal submits the next legal effective outcome and references the decision it supersedes: reversing approval records rejection plus withdrawal, while reversing rejection records approval plus activation. It then moves `canonical_current` back to the applicable prior revision or removes the current projection. No evidence or history is deleted.

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

Canonical loading is atomic. A failed load rolls back all canonical writes from that attempt while leaving frozen sources, raw records, candidate revisions, issues, classifications, and checkpoints available for retry. `classified` with `load_failed` is a valid recoverable state.

Run processing states include stage-specific failure states and the last completed checkpoint. A failed parse batch is invisible; earlier committed batches remain inspectable as part of an incomplete run.

## 6. Stored state and ownership

| State | Sole owner | Mutation model |
|---|---|---|
| `source_file`, `source_occurrence`, `run_source_occurrence` | ingest | Append-only |
| `run`, `pipeline_checkpoint` | pipeline orchestrator | Constrained stage transitions |
| `raw_record` | parse | Append-only |
| `candidate_revision` | candidate revision service | Append-only |
| `classification_result`, `data_quality_issue` | classify | Append-only per revision/rules version |
| `review_item` | review service | Created with stable reasons and source references |
| `review_decision` | review decision service | Append-only ordered decisions; later decisions supersede by reference |
| canonical identity/revision/business key | canonical command service | Identity retained; revisions append-only |
| `canonical_promotion_event` | canonical command service | Append-only activation and withdrawal history |
| `canonical_current` | canonical command service | Rebuildable projection updated transactionally from promotion events |
| `fx_rate` | FX fixture importer | Immutable once referenced by a run |
| pipeline event log | event writer | Append-only |

`run.counts` is an immutable end-of-classification snapshot.

The effective review state comes from the latest decision: `pending`, `approved`, `rejected`, or `acknowledged`. A record being inspected does not acquire a durable state merely because a browser has it open.

## 7. FX data

The first slice seeds a pinned ECB-history fixture with provenance and versioned rate rows. The pipeline never performs a network fetch. Orders use the stored rate for their order date; lifetime spend uses the run's fixed snapshot. Missing rates preserve the source amount, leave the GBP value unresolved, and create `FX_RATE_UNAVAILABLE`.

### 7.1 Local API contract

The FastAPI adapter exposes the complete local loop:

| Method and path | Purpose |
|---|---|
| `POST /api/uploads` | Accept multipart CSV content and record/reuse the frozen source occurrence and run |
| `POST /api/runs/{run_id}/process` | Advance legal stages synchronously until review-ready/complete or recoverably failed |
| `GET /api/workspace` | Return scoped attention, failure, outcome, and recent-run projections |
| `GET /api/runs/{run_id}` | Return run, occurrence, stage, record, decision, and evidence details |
| `GET /api/reviews` | Return a scoped/filtered review queue |
| `GET /api/reviews/{review_item_id}` | Return exact evidence, legal actions, decision history, and canonical effect |
| `POST /api/reviews/{review_item_id}/decisions` | Append an idempotent decision and atomically apply promotion or reversal |

Processing is synchronous for the local MVP; the UI polls the run projection while a request is active or after reconnecting. Upload and decision mutations require idempotency keys. Responses use one typed error envelope. Validation failures return `422`, missing resources `404`, and stale decision preconditions `409` with current references for refresh.

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

- CSV upload and processing controls;
- outstanding review work;
- failed or blocked processing;
- an outcome breakdown for the active file scope;
- recent individual runs.

It must make the next operational action apparent without requiring the reviewer to understand the underlying data model.

### 8.4 Run page

Every run has a stable page showing:

- exact filename, run ID, record count, and absolute processing timestamp with timezone;
- the number of times the exact content was submitted and an expandable occurrence list containing each filename, actor label, and absolute ingest timestamp;
- a plain-language duplicate-submission notice when more than one occurrence links to the run;
- outcome breakdown;
- processing-stage status;
- records and review items belonging to the run;
- selected-record details;
- expandable raw evidence, candidate revisions, transformations, issues, verdicts, provenance, and relationships.

### 8.5 Review queue

The review surface uses a persistent queue with a detail panel. Moving between items preserves queue and filter context. The detail panel explains the plain-language reason, raw and interpreted values, pipeline position, dependencies, prior canonical state where relevant, provenance, technical evidence, decision history, and legal actions.

Outstanding-review items show:

- a plain-language reason;
- business identifier;
- linked filename;
- source line where applicable;
- review state.

`NEEDS_REVIEW` and conflict items offer **Approve** and **Reject**. `REJECTED` and `DUPLICATE` items offer **Acknowledge** and **Reject**. The form records an operator name and requires a rejection reason. While a decision is saving, repeat submission is disabled. Success updates both the queue and canonical effect. A stale `409` retains entered text, refreshes the evidence, and explains that the item changed before the decision was saved.

The filename links to `/runs/:runId?review=:reviewItemId`, opening the individual run while retaining the selected review item. Link styling remains visibly interactive without relying only on color.

### 8.6 File scope

All aggregate counts, outcome breakdowns, filters, and queue results inherit one explicit scope:

- **Current file:** the run currently open.
- **Selected files:** a reviewer-defined set chosen through a searchable multi-select.
- **All files:** every available run.

The file-selection panel appears only for **Selected files**. Selected filenames appear as removable chips, and the closed control reports the selected count. The visible page heading and outcome label repeat the active scope.

### 8.7 Outcome breakdown

- State the file scope and total record count in the breakdown title.
- Show the exact label and count for every outcome, pairing semantic color with visible text.
- Make each count open the review queue with the corresponding scope and filter.
- Keep the data contract suitable for a later chart, but do not require a chart in the MVP.

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
- successfully processed run with automatic promotions;
- run in progress;
- recoverable stage failure;
- load failure after classification;
- unresolved review item;
- saving, saved, rejected, acknowledged, reversed, and stale-decision states;
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
- Outcome totals use accessible text and semantic links.
- Controls use semantic HTML before ARIA.
- Interactive targets are at least 24 px visually or through an expanded hit area.
- Essential processing and save feedback honors `prefers-reduced-motion`.
- Responsive layouts preserve filename, reason, and current status before secondary metadata.

### 8.13 Design and implementation method

The Vercel/Geist direction is fixed for this operational interface. The design workflow is:

1. surface and state shaping with Impeccable;
2. a plain-language pass with Humanizing UI Jargon;
3. an anti-template critique with Frontend Design;
4. interaction feedback with Emil Design Engineering;
5. keyboard, basic accessibility, responsive overflow, and empty/error-state validation.

Awesome Designs may be used to audit the selected direction but does not replace it. Taste Skill is reserved for non-dashboard surfaces such as landing, editorial, portfolio, and marketing pages.

### 8.14 Mockup artifacts

The approved visual baseline is [workspace and review layout, revision 9](../.superpowers/brainstorm/79491-1789562129/content/workspace-review-layout-v9.html). It illustrates the requirements in this section; this specification remains authoritative if a mockup and written requirement differ.

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

The first five foundation/derived stages are implemented through atomic canonical staging. The approved replacement work is:

1. close the existing pipeline with independent Task 7 review, orchestration, the frozen-sample proof, and one representative recovery proof;
2. add review decisions, canonical promotion/current projection, activate/backfill the eligible revisions produced by the existing loader, reversal, idempotency, and stale-write protection;
3. expose the seven product endpoints through one consolidated FastAPI adapter;
4. build the workspace upload/process and attention overview;
5. build run evidence and actionable review workflows;
6. prove the complete vertical slice end to end and run the repository quality gates.

The golden result must explain every physical source line and trace each staged or reviewed outcome through raw evidence, candidate revisions, rules, issues, and classification.

## 11. Required automated test contract

Implementation follows red-green-refactor. Existing named tests below remain the regression contract. New product work adds the focused tests in section 11.9 without multiplying every UI and domain combination into a separate matrix. Tests assert public behavior and persisted state rather than private calls. Core domain behavior uses real objects; fakes are permitted only at filesystem, clock, transaction-failure, and network boundaries.

The Python suite uses `pytest`. Web component tests use Vitest, Testing Library, `user-event`, and `axe-core`. Unit suites perform no network access and do not depend on test execution order. Time-sensitive tests use a fixed clock of `2026-09-16T12:00:00Z` and explicitly named timezones.

### 11.1 Ingest unit tests

File: `tests/unit/ingest/test_ingest.py`

| ID and test name | Required assertion |
|---|---|
| `ING-01 test_freezes_source_bytes_without_modification` | Given bytes containing a BOM, CRLF, and non-BMP text, stored bytes are byte-for-byte equal and `source_file.sha256` equals the SHA-256 of those exact bytes. |
| `ING-02 test_identical_content_records_each_occurrence_but_reuses_run` | Two ingests with identical bytes and different filenames create one `source_file`, two `source_occurrence` rows, two run-occurrence links, and one run. The second result sets `source_reused`, `run_reused`, and `duplicate_upload`; each occurrence retains its own metadata. |
| `ING-03 test_same_idempotency_key_returns_same_run` | Repeating the same ingest command and idempotency key returns the original run and creates no additional source occurrence. |
| `ING-04 test_explicit_reprocess_creates_new_run_for_frozen_source` | Reprocessing creates a different run ID, links it to its predecessor, and retains the same source-file ID and bytes. |
| `ING-05 test_same_filename_with_different_bytes_creates_new_source_and_run` | Filename equality never suppresses new content: different hashes create different source files and runs. |
| `ING-06 test_duplicate_completed_run_performs_no_pipeline_work` | Submitting bytes whose current run is staged returns that run and creates only the new occurrence/link; raw records, candidates, classifications, and canonical revisions remain unchanged. |
| `ING-07 test_duplicate_incomplete_run_resumes_when_processing_requested` | Submitting bytes whose current run failed after a checkpoint reuses the run and, when processing is requested, continues from that checkpoint without repeating committed batches. |
| `ING-08 test_concurrent_identical_ingests_create_one_run` | Two transactions ingesting identical bytes concurrently create one source file and one initial run, preserve both occurrences, and return the same run ID after source-row serialization. |
| `ING-09 test_duplicate_after_reprocess_reuses_latest_run` | After explicit reprocessing creates a successor run, a later duplicate submission links to and returns that latest run rather than the superseded predecessor. |
| `ING-10 test_ingest_does_not_interpret_csv` | Arbitrary malformed CSV bytes are frozen and a run is opened without producing raw records or parse errors during ingest. |

### 11.2 Parse unit tests

File: `tests/unit/pipeline/test_parse.py`

| ID and test name | Required assertion |
|---|---|
| `PAR-01 test_bom_header_is_recognised_without_changing_frozen_bytes` | A UTF-8 BOM header becomes one `header` raw record whose first field is `record_type`; frozen source bytes retain the BOM. |
| `PAR-02 test_blank_line_is_preserved` | A blank physical line creates one `blank` raw record with its exact inclusive source-line range. |
| `PAR-03 test_repeated_header_is_preserved_and_classified` | A header after data creates `kind=repeated_header`, is not treated as data, and retains its source line. |
| `PAR-04 test_quoted_multiline_field_is_one_logical_record` | A quoted value spanning physical lines 2–3 creates one data record with `source_line_start=2`, `source_line_end=3`, and an embedded newline in the field. |
| `PAR-05 test_short_row_preserves_actual_field_count` | A seven-field row stores exactly seven ordered fields and `field_count=7`; parse does not invent missing values. |
| `PAR-06 test_long_row_preserves_all_fields` | A twelve-field row stores all twelve fields in source order; parse does not silently discard overflow. |
| `PAR-07 test_trailing_empty_field_is_preserved` | A row with an extra trailing comma stores the final empty field and the corresponding field count. |
| `PAR-08 test_escaped_quote_decodes_without_quality_issue` | RFC 4180 doubled quotes decode to one literal quote and are recorded as parse provenance, with no data-quality issue. |
| `PAR-09 test_failed_batch_rolls_back_only_that_batch` | Injected failure in batch 2 leaves all batch-1 raw records and its checkpoint committed, with no rows or checkpoint from batch 2. |
| `PAR-10 test_retry_from_checkpoint_is_idempotent` | Retrying after `PAR-09` completes remaining records and creates no duplicate raw-record IDs. |

### 11.3 Normalisation unit tests

Files: `tests/unit/normalise/test_values.py` and `tests/unit/normalise/test_candidates.py`

| ID and test name | Required assertion |
|---|---|
| `NOR-01 test_candidate_field_states_are_structured` | Known, empty, `TBD`, and unparseable inputs produce respectively `known` with a typed value, `absent`, `deferred`, and `unresolved`; only `known` carries a value. |
| `NOR-02 test_money_formats` | Parameter cases parse exactly: `19.99→19.99 GBP`, `$1,240.50→1240.50 USD`, `45,00→45.00 GBP`, `€2.345,00→2345.00 EUR`, `1.5e3→1500.00 GBP`, and padded ` 275.00 →275.00 GBP`. Values use `Decimal`, never binary float. |
| `NOR-03 test_money_annotation_is_preserved` | `12.99 (10% off)` produces amount `12.99`, currency `GBP`, and annotation `10% off`. |
| `NOR-04 test_ambiguous_single_comma_uses_thousands_rule_and_records_issue` | `1,240` produces `1240.00` and a registered ambiguity issue retaining the raw value. |
| `NOR-05 test_null_and_deferred_tokens_are_contextual` | Empty, `-`, `N/A`, `NULL`, and `None` map to the configured absent representation in their permitted fields; `TBD` maps to deferred; `many` maps to unresolved and raises `INVALID_INTEGER`. |
| `NOR-06 test_supported_date_formats` | Parameter cases produce exactly: `2023-05-12→2023-05-12`, `01/22/2023→2023-01-22`, `15-Jan-2024→2024-01-15`, `2024/02/03 14:22:00→2024-02-03T14:22:00`, and padded ` 2023-08-01 →2023-08-01`. |
| `NOR-07 test_slash_date_is_month_day_year` | `03/04/2023` becomes 4 March 2023 without an ambiguity issue. |
| `NOR-08 test_customer_status_mapping` | `Active`, `ACTIVE`, and `Y` become `active`; `inactive` remains `inactive`; each non-identity mapping records transformation provenance. |
| `NOR-09 test_tags_normalise_to_ordered_list` | `vip|Newsletter` becomes `['vip', 'newsletter']`; empty and `N/A` become `[]`; surrounding empty segments are rejected rather than silently becoming tags. |
| `NOR-10 test_free_text_trims_edges_and_preserves_content` | Outer whitespace is removed while apostrophes, accents, emoji, ampersands, punctuation, and embedded newlines remain unchanged. |
| `NOR-11 test_match_key_is_not_stored_value` | `Sofia Rossi 🌟` retains its display value and produces match key `sofia rossi`; `  Wei Zhang  ` produces `wei zhang`. |
| `NOR-12 test_record_type_selects_typed_candidate` | Valid discriminators create only their matching `CustomerCandidate`, `ProductCandidate`, or `OrderCandidate` type. |
| `NOR-13 test_invalid_discriminator_creates_rejected_shell` | Missing or unknown discriminator creates a rejected shell linked to the raw record and never guesses an entity type. |
| `NOR-14 test_short_row_marks_missing_fields_absent` | The seven-field `ORD-3004` shape retains raw field count 7 while its candidate marks status, tags, and notes absent and raises the registered missing-status issue. |
| `NOR-15 test_overflow_notes_salvage_is_explicit` | The twelve-field `CUST-1005` shape creates a candidate note joined from fields 10 onward and records a lossy structural-repair issue with all source fields referenced. |
| `NOR-16 test_routine_parse_mechanics_do_not_create_quality_issues` | BOM removal, quote decoding, blank-line recognition, and header recognition produce provenance only. |

### 11.4 FX unit tests

File: `tests/unit/normalise/test_fx.py`

| ID and test name | Required assertion |
|---|---|
| `FX-01 test_cross_rate_uses_euro_reference_rates` | Given EUR/GBP `0.85` and EUR/USD `1.10`, USD→GBP equals `0.85 / 1.10` at configured decimal precision. |
| `FX-02 test_weekend_uses_latest_earlier_rate` | When no rate exists on the requested date, the most recent earlier publication is selected; a later rate is never used. |
| `FX-03 test_order_uses_order_date` | Foreign order unit price uses the rate effective for `order_ts`, and stores source amount, source currency, rate, rate date, source, and `FX_CONVERTED_AT_ORDER_DATE`. |
| `FX-04 test_lifetime_spend_uses_run_snapshot` | Foreign customer lifetime spend uses the latest rate before the fixed run date, not the signup date, and creates `FX_CONVERTED_AT_RUN_DATE`. |
| `FX-05 test_missing_rate_preserves_source_and_requires_review` | No eligible rate leaves GBP unresolved, preserves source amount/currency, creates `FX_RATE_UNAVAILABLE`, and makes the candidate ineligible for staging. |

### 11.5 Classification unit tests

File: `tests/unit/classify/test_rules.py`

| ID and test name | Required assertion |
|---|---|
| `CLS-01 test_classification_waits_for_all_initial_candidates` | A run with any data record lacking an initial candidate cannot enter classification and produces no partial classifications. |
| `CLS-02 test_rules_version_is_content_deterministic` | Reordered registry loading produces the same hash; changing a rule or relevant normalisation setting changes it. Build revision is stored separately. |
| `CLS-03 test_sku_zero_padding_repair_appends_revision` | `SKU-00204` produces an initial candidate retaining that interpretation and an `AUTO_REPAIRED` child revision with `SKU-2004`, parent link, rule reference, and source provenance. |
| `CLS-04 test_order_sku_repair_resolves_repaired_product` | An order referencing `SKU-00204` receives a repaired child revision referencing `SKU-2004` and resolves to the repaired product identity. |
| `CLS-05 test_line_total_repair_precedes_fx_conversion` | Order value `$39.98` with quantity `2` and product unit price `19.99` appends a repaired value `$19.99`; FX conversion then operates on `$19.99`, with both transformations ordered in provenance. |
| `CLS-06 test_validation_only_rule_never_appends_revision` | A rule registered without an automatic repair may create an issue and review item but cannot create a candidate revision. |
| `CLS-07 test_exact_duplicate_compares_parsed_fields` | Two same-run rows with identical parsed fields but different source lines classify the later occurrence `DUPLICATE`; both raw records remain and only one canonical identity is eligible. |
| `CLS-08 test_quoting_difference_can_still_be_duplicate` | CSV spellings that parse to identical field arrays are duplicates. |
| `CLS-09 test_value_whitespace_prevents_exact_duplicate` | Parsed field arrays differing by internal or surrounding value whitespace are not exact duplicates, even if later normalisation makes them equal. |
| `CLS-10 test_same_run_business_id_with_different_fields_is_conflict` | The later candidate gets conflict review; neither candidate overwrites the other. |
| `CLS-11 test_prior_run_same_identity_same_values_is_reobservation` | The new occurrence links to the existing identity as a re-observation and creates no new canonical revision. |
| `CLS-12 test_prior_run_same_identity_changed_values_is_conflict` | Changed derived values create conflict review and leave the existing canonical revision untouched. |
| `CLS-13 test_dependency_readiness_is_separate_from_verdict` | A field-clean order with an unresolved customer or product keeps its own verdict, gains readiness `blocked_by_dependency`, remains unstaged, and records the exact blocking reference. |
| `CLS-14 test_name_match_uses_match_key_and_retains_raw_name` | Order name `Sofia Rossi` resolves to customer `Sofia Rossi 🌟` through the match key while both original values remain inspectable. |
| `CLS-15 test_refund_invariant` | Negative quantity with `refunded` passes; negative quantity with another status and positive quantity with `refunded` create registered validation issues without automatic repair. |
| `CLS-16 test_product_stock_status_invariants` | `backordered/<0`, `discontinued/0`, and `in_stock/>0` pass; mismatched pairs create registered validation issues. |

### 11.6 Load and recovery unit tests

Files: `tests/unit/load/test_stage.py` and `tests/unit/pipeline/test_recovery.py`

| ID and test name | Required assertion |
|---|---|
| `LOD-01 test_clean_candidate_creates_staged_revision` | `CLEAN` creates one staged canonical revision linked to the exact candidate revision and raw evidence, with no review item. |
| `LOD-02 test_auto_repaired_candidate_stages_repaired_revision` | `AUTO_REPAIRED` stages the terminal repaired revision and retains the full parent chain and repair issues. |
| `LOD-03 test_needs_review_stays_outside_canonical_tables` | `NEEDS_REVIEW` creates a review item with stable reasons and creates no canonical identity or revision. |
| `LOD-04 test_rejected_record_stays_outside_canonical_tables` | `REJECTED` creates review work with structural reasons and source references and creates no canonical identity or revision. |
| `LOD-05 test_duplicate_creates_no_second_canonical_revision` | The later duplicate remains linked to its raw occurrence, creates review work, and creates no additional identity or revision. |
| `LOD-06 test_dependency_blocked_candidate_is_not_staged` | A candidate with eligible verdict but blocked readiness creates no staged revision. |
| `LOD-07 test_load_failure_rolls_back_entire_staged_set` | Failure after any staged insert leaves zero canonical writes from that load attempt while classifications, issues, candidates, and review items remain. Run state becomes `classified` plus `load_failed`. |
| `LOD-08 test_load_retry_is_idempotent` | Retrying `LOD-07` stages the eligible set exactly once and does not rerun ingest, parse, normalise, or classify. |
| `LOD-09 test_classification_retry_does_not_duplicate_results` | Reclassifying the same candidate revision under the same rules version produces one verdict and one instance of each issue identity. |

### 11.7 Application-query and API unit tests

Files: `tests/unit/application/test_queries.py` and `tests/unit/api/test_presenters.py`

| ID and test name | Required assertion |
|---|---|
| `API-01 test_workspace_current_file_scope` | Counts, outcome data, recent runs, and review results contain only the current run. |
| `API-02 test_workspace_selected_files_scope` | Results contain the exact selected run IDs, reject an empty selection, and report the selected count. |
| `API-03 test_workspace_all_files_scope` | Results aggregate all runs visible to the local application without applying a hidden current-file filter. |
| `API-04 test_scope_counts_use_one_classification_snapshot` | Every displayed total and segment derives from the same immutable `run.counts` snapshots and the segment sum equals the displayed total. |
| `API-05 test_run_detail_preserves_evidence_lineage` | The response relates source occurrence, raw record, candidate chain, transformations, issues, classification, readiness, review item, canonical revision, decisions, and promotion events without copying or losing identifiers. |
| `API-06 test_review_filter_combines_scope_and_state` | State filters operate inside the active file scope and return stable reason and source references. |
| `API-07 test_internal_values_have_plain_language_fallback` | Known internal values use the registered display map; an unknown snake-case value becomes a readable label while its raw value remains in technical evidence. |
| `API-08 test_timestamps_include_instant_and_timezone` | Presenter output contains an unambiguous machine instant and an absolute localized display value with timezone; it never emits relative time. |
| `API-09 test_run_detail_lists_all_source_occurrences` | A reused run returns every linked occurrence in ingest order, the occurrence count, and duplicate-upload relation while retaining the initiating occurrence. |

### 11.8 Web component unit tests

Files: `apps/web/src/**/*.test.tsx`

| ID and test name | Required assertion |
|---|---|
| `UI-01 workspace_prioritises_attention_failures_and_recent_runs` | The workspace renders all three sections with accessible headings and exposes the outstanding-review count first in document order. |
| `UI-02 scope_control_has_exact_options` | The control exposes exactly `Current file`, `Selected files`, and `All files`. |
| `UI-03 selected_file_picker_is_conditional` | The searchable multi-select is visible only for `Selected files`; changing to either other scope removes it and its selected-file filters from the query. |
| `UI-04 selected_files_are_removable_and_counted` | Selected filenames render as removable controls and the closed selector reports the exact count. |
| `UI-05 review_filename_preserves_selected_item` | Activating a filename navigates to `/runs/:runId?review=:reviewItemId` for that row. |
| `UI-06 stage_buttons_reflect_reachability` | Completed/current stages with details are enabled; future stages are disabled and labelled not reached. |
| `UI-07 selecting_stage_replaces_detail_panel` | Keyboard activation updates the panel heading, evidence, result, timestamp, and selected state without losing the review item. |
| `UI-08 normalisation_copy_does_not_imply_reversal` | The interface contains `Normalisation details` and does not render `de-normalise`. |
| `UI-09 timestamps_are_absolute_plain_text` | Evidence and recent-run timestamps include date, time, and timezone, contain no relative-time words, and are not rendered with status-pill semantics. |
| `UI-10 outcome_breakdown_is_textually_complete` | Every outcome has a visible label and count, semantic state is not conveyed by color alone, and each count links to its filtered review result. |
| `UI-11 semantic_status_is_not_color_only` | Every status color is accompanied by visible text and an accessible name. |
| `UI-12 empty_loading_failure_and_stale_states_are_distinct` | Each supplied state renders its specified message and next action without reusing a misleading success or empty state. |
| `UI-13 long_filename_does_not_hide_reason_or_status` | At narrow desktop and mobile container widths, filename truncation preserves accessible full text while reason and current status remain visible. |
| `UI-14 keyboard_flow_and_focus_are_predictable` | A keyboard-only user can change scope, select files, open a review item, change stage, and return to the queue with visible focus throughout. |
| `UI-15 reduced_motion_disables_nonessential_transitions` | With reduced motion enabled, state changes remain understandable and nonessential transitions have zero duration. |
| `UI-16 primary_screens_have_no_axe_violations` | Workspace, run page, and review queue fixtures produce zero automated axe violations in default, loading, empty, and failure states. |
| `UI-17 duplicate_submission_is_visible_on_run_page` | A run with multiple occurrences shows a plain-language exact-file notice, total submission count, and expandable filenames, actor labels, and absolute ingest timestamps. |

### 11.9 Decision, promotion, and vertical-slice tests

The replacement implementation plan must cover these behaviors with focused domain/integration/component tests:

| ID | Required assertion |
|---|---|
| `DEC-01` | Approving the current terminal `NEEDS_REVIEW` candidate appends one decision, one canonical revision, one activation event, and updates `canonical_current` in one transaction. |
| `DEC-02` | Approving changed values for an existing business identity appends the next canonical revision and preserves the prior revision and complete lineage. |
| `DEC-03` | Rejecting a reviewable candidate records operator and required reason and leaves canonical state unchanged. |
| `DEC-04` | Acknowledging or rejecting `REJECTED` and `DUPLICATE` records never creates a canonical identity or revision. |
| `DEC-05` | Replaying the same decision idempotency key and payload returns the original result; reusing it for a different payload is rejected. |
| `DEC-06` | A stale candidate revision or decision sequence returns conflict and creates no decision, revision, event, or projection change. |
| `DEC-07` | Reversing approval appends a superseding decision and event and restores the prior current revision, or removes the projection when none existed. |
| `API-10` | Multipart upload and process endpoints reuse the same application commands as the CLI and report duplicate occurrences accurately. |
| `API-11` | Review detail reports exact evidence, legal actions, decision history, and canonical effect; the decision endpoint uses one stable error envelope. |
| `UI-18` | The operator can upload/process, open an exception, inspect evidence, submit a legal decision, and see the updated queue and canonical result using the keyboard. |
| `E2E-01` | One local browser flow proves upload → process → inspect → decide → promote → reverse, including duplicate-upload and recoverable-retry checks. |

### 11.10 Golden fixture integration test

File: `tests/integration/test_messy_sample_data.py`

This is deliberately separate from the unit suite. The current `data/messy_sample_data.csv` is frozen by SHA-256 `e550ba421ebaac800d2e734c513f65d2bc4197f8b622a2fc82230974744110c4`. The integration test must fail immediately if that hash changes without an accompanying update to the expectations below.

The golden test asserts:

- 5,953 source bytes and 54 physical lines are preserved;
- CSV interpretation produces 52 logical raw records: 48 data records, one initial header, one repeated header, and two blank records;
- quoted multi-line records retain physical spans 15–16 and 36–37;
- non-ten-field data records retain their actual shapes at physical line 13 (7 fields), line 14 (12), line 24 (11), line 31 (11), and line 39 (9);
- every physical line belongs to exactly one stored raw-record span;
- the duplicate `ORD-3001` occurrence is retained and linked to the earlier occurrence;
- every data record ends in exactly one terminal classification for the active rules version;
- every staged or unstaged outcome is traceable through raw evidence, candidate revisions, transformations, issues, classification, readiness, and review work;
- rerunning the complete pipeline with the same run identity creates no duplicate persisted objects;
- failing and retrying each configured batch boundary produces the same final persisted graph as an uninterrupted run.

[`data/messy_sample_data.schema.md`](../data/messy_sample_data.schema.md) inventories the complete 54-line fixture, including every structural exception and the governed outcome of every semantic variant. This architecture remains authoritative for product behavior; any fixture change must update the CSV hash, schema inventory, and golden expectations together.

### 11.11 Required test commands

The verified repository gate is:

```sh
./scripts/quality-gate.sh
```

It runs all Python unit/integration tests, Ruff, strict Pyright, web component/accessibility tests, TypeScript, the production build, and the browser operator flow in a fixed order. On 18 September 2026 it passed with 615 Python tests, 63 web tests, and one Chromium acceptance test, with no unexpected warnings. The browser flow uses a freshly migrated disposable PostgreSQL database and real API/filesystem storage, injects one recoverable normalisation batch failure, proves retry, inspects source and interpreted evidence, approves and reverses a decision, and proves identical re-upload adds an occurrence without changing any pipeline/canonical rows.

Focused commands remain available:

```sh
.venv/bin/pytest -q tests/unit
.venv/bin/pytest -q tests/integration/test_messy_sample_data.py
(cd apps/web && bun run test)
(cd apps/web && bunx playwright test e2e/operator-flow.spec.ts)
```

`bun run test` invokes the configured Vitest DOM environment. Use `./scripts/run-local.sh` for migration and loopback API/web launch. Setup, environment variables, test database privileges, verified runtime versions, and operator instructions are in [`README.md`](../README.md). Tests use temporary databases and source storage. The acceptance fixture and its failure/snapshot endpoints are test-only; normal launch uses the product FastAPI factory.

The verified runtime was Python 3.13.12, Bun 1.3.5, PostgreSQL 17.7, and Chromium 153.0.8010.12. The supplied Compose configuration remains PostgreSQL 16; that container and additional browser/device environments were not exercised in this acceptance run. Deferred production and governance scope remains in [`TODO.md`](../TODO.md).
