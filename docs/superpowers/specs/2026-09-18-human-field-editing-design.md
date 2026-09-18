# Human Field Editing Design Specification

**Status:** Approved design, pending implementation
**Date:** 18 September 2026
**Branch:** `feature/human-field-editing`
**Extends:** [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) (implementation amends it; see §14)
**Removes from backlog:** "Add human editing through append-only candidate revisions" in [`TODO.md`](../../../TODO.md)
**Visual baseline:** [edit popup, layout C](../../../.superpowers/brainstorm/86906-1789763248/content/edit-popup-detail.html)

## 1. Purpose

Operators can currently only approve, reject, or acknowledge a record as the pipeline interpreted it. When the interpretation is wrong, they have no way to correct it. This feature lets an operator append a corrected version of a record under review. The operator states why the correction was needed, the correction is re-checked against the rest of the run, and a human decision is still required before anything reaches canonical data.

Every edit is append-only, timestamped by the server, hashed into a chain, and tied to exactly one run. Later edits override earlier ones for display and decisions, but they never overwrite them.

## 2. Scope

In scope:

- appending edits to records currently in the review queue;
- three edit reasons with distinct effects on the re-check: **data wrong**, **business rule updated**, **graph wrong**;
- a named catalogue of business-rule checks that can be waived or cited;
- a single-record re-check against the rest of the run;
- a stable review item and decision history across edits;
- an edit popup (comparison-table layout), edit history, and field provenance in the web application;
- specific error codes for edit failures and for the existing processing workers.

Out of scope:

- a CLI command (this is a web application first);
- editing canonical, auto-promoted, or typeless (`RejectedCandidateShell`) records;
- editing the source or any existing revision;
- changing catalogue defaults for everyone (for example adding `Garden` to the category vocabulary globally). That remains the deferred configurable-rules work;
- per-issue decisions and bulk edits.

## 3. Terms

| Term | Meaning |
|---|---|
| **Edit** | One operator submission, stored as one `human_edit` row. |
| **Edited version** | The candidate revision with origin `HUMAN_EDIT` that an edit appends. |
| **Terminal version** | The highest-numbered candidate revision for a raw record. It may be a derived child of an edited version (see §7.4). |
| **Current edit** | The latest edit for a raw record. Earlier edits are **overridden**. |
| **Assessment** | One classification result linked to a review item through `review_assessment`. The latest assessment is current. |
| **Human-set field** | A field whose most recent transformation in the revision chain is `HUMAN_EDIT`. |
| **Citation** | A catalogue check named under reason *business rule updated*. It either waives the check or, for interpretation policies, records it. |
| **Dismissal** | A cross-record finding marked wrong under reason *graph wrong*. |

## 4. Data model

One migration, `0006_human_edits`, adds or changes the following. All new tables are append-only. The repositories expose no update or delete for them.

### 4.1 `human_edit`

| Column | Type | Rule |
|---|---|---|
| `id` | UUID v4 | primary key |
| `run_id` | UUID | not null; composite FK `(raw_record_id, run_id)` → `raw_record(id, run_id)` (the migration adds the unique constraint this FK needs). An edit can never name a run other than its record's run. |
| `raw_record_id` | UUID | not null |
| `review_item_id` | UUID | FK `review_item.id`, not null |
| `edit_number` | int | ≥ 1; unique with `raw_record_id` |
| `parent_revision_id` | UUID | FK `candidate_revision.id`; the terminal version when the edit was made |
| `candidate_revision_id` | UUID | FK `candidate_revision.id`; unique; the edited version |
| `reason` | text | `data_wrong` \| `business_rule_updated` \| `graph_wrong` |
| `note` | text null | operator free text, trimmed, at most 2,000 characters |
| `field_changes` | JSONB array | `[{field_path, input_text, absent, before, after}]`, sorted by `field_path`; `before` and `after` are encoded `CandidateField`s |
| `citations` | JSONB array | `[{check_id, field_path, accepted_value}]`; nonempty if and only if `reason = business_rule_updated` |
| `dismissals` | JSONB array | `[descriptor]` (§7.6); nonempty if and only if `reason = graph_wrong` |
| `rules_version` | text | the registry version used for the re-check |
| `operator_name` | text | validated like decision operators |
| `edited_at` | timestamptz | from the server clock, never from the client |
| `idempotency_key` | text | unique |
| `content_hash` | char(64) | §6.3 |
| `parent_hash` | char(64) null | the previous edit's `content_hash`; null only when `edit_number = 1` |

Check constraints enforce the reason/citation/dismissal pairing, `parent_hash IS NULL` ⇔ `edit_number = 1`, and `data_wrong` ⇒ `jsonb_array_length(field_changes) ≥ 1`.

### 4.2 `candidate_revision`

This table needs no schema change: the existing `ck_candidate_parent` already allows any non-`normalise` origin for revisions after 1. An edited version has `origin = 'HUMAN_EDIT'`. `CandidateRevision` domain validation is unchanged.

`TransformationCode` gains `HUMAN_EDIT`. Each changed field gets one `transformation_event` with `operation = 'HUMAN_EDIT'`, `before`, `after`, and the next sequence in the chain. The edited field keeps its original `source_refs` and gains the new transformation in `transformation_refs`. Its `issue_refs` are replaced by the issues raised when the operator's input is parsed.

### 4.3 `review_assessment`

| Column | Rule |
|---|---|
| `review_item_id` | FK, part of primary key |
| `sequence` | ≥ 1, part of primary key |
| `classification_id` | FK `classification_result.id`, unique |
| `human_edit_id` | FK `human_edit.id`, unique, null only for `sequence = 1` |
| `created_at` | timestamptz |

The migration backfills `(review_item.id, 1, review_item.classification_id, NULL, review_item.created_at)` for every existing review item. `review_item.classification_id` is kept and still means "the assessment that opened the item". Everything that needs the current classification reads the latest assessment.

### 4.4 `data_quality_issue.check_id`

This is a new nullable text column holding the catalogue id (§5) of the check that raised the issue. Normaliser parse issues keep `NULL`, and a `NULL` check id can never be waived.

The migration backfills existing rows from `(code, field_path)` using the §5 mapping. `INVALID_INTEGER` on `order.quantity` maps to `limit.order_quantity_nonzero` only when its summary is `Order quantity must be non-zero.` Otherwise it stays `NULL`.

### 4.5 `edit_attempt_failure`

Columns: `id`, `review_item_id`, `run_id`, `stage`, `code`, `reference` (UUID, unique), `error_type`, `operator_name`, `occurred_at`. It is written in its own transaction after an edit transaction rolls back (§9.2).

## 5. Check catalogue

Every validation check and interpretation policy gets a stable id. Issues raised by a check carry its id. The catalogue lives in code next to the rules, and its ids and kinds are content in the rules-version hash. The rules version therefore changes once with this feature, and existing runs keep the version they were classified under.

| Id | Kind | Fields | Issue code | Waiver scope |
|---|---|---|---|---|
| `vocab.product_category` | vocabulary | `product.category` | `INVALID_CATEGORY` | value |
| `vocab.customer_status` | vocabulary | `customer.status` | `INVALID_STATUS` | value |
| `vocab.product_status` | vocabulary | `product.status` | `INVALID_STATUS` | value |
| `vocab.order_status` | vocabulary | `order.status` | `INVALID_STATUS` | value |
| `vocab.customer_tags` | vocabulary | `customer.tags` | `UNKNOWN_TAG` | value (per tag) |
| `vocab.product_tags` | vocabulary | `product.tags` | `UNKNOWN_TAG` | value (per tag) |
| `vocab.order_tags` | vocabulary | `order.tags` | `UNKNOWN_TAG` | value (per tag) |
| `format.customer_id` | format | `customer.customer_id` | `INVALID_IDENTIFIER` | field |
| `format.sku` | format | `product.sku`, `order.sku` | `INVALID_IDENTIFIER` | field |
| `format.order_id` | format | `order.order_id` | `INVALID_IDENTIFIER` | field |
| `format.email` | format | `customer.email` | `INVALID_EMAIL` | field |
| `format.date_only` | format | `customer.signup_date`, `product.listed_date` | `INVALID_DATE` | field |
| `limit.required_field` | limit | the 18 configured required fields | `MISSING_REQUIRED_VALUE` | field |
| `limit.positive_amount` | limit | `product.unit_price`, `order.unit_price` | `INVALID_AMOUNT` | field |
| `limit.nonnegative_amount` | limit | `customer.lifetime_spend` | `INVALID_AMOUNT` | field |
| `limit.order_quantity_nonzero` | limit | `order.quantity` | `INVALID_INTEGER` | field |
| `limit.customer_quantity_empty` | limit | `customer.quantity` | `INVALID_STRUCTURE` | field |
| `invariant.refund` | invariant | `order.quantity` | `REFUND_CONFLICT` | field |
| `invariant.stock_status` | invariant | `product.stock_qty` | `STOCK_STATUS_CONFLICT` | field |
| `interpret.slash_date_mdy` | interpretation | date fields | none | citation only |
| `interpret.single_comma_thousands` | interpretation | money fields | none | citation only |
| `interpret.status_mapping` | interpretation | status fields | none | citation only |
| `interpret.sku_zero_padding` | interpretation | `product.sku`, `order.sku` | none | citation only |
| `interpret.line_total_repair` | interpretation | `order.unit_price` | none | citation only |
| `interpret.fx_rate_date` | interpretation | GBP-converted money fields | none | citation only |

The catalogue exposes, for each entity type, its applicable checks and fields. A citation is valid only if its `check_id` applies to the record's entity type and its `field_path` is one of that check's fields. A vocabulary citation must carry `accepted_value`, and every other kind must not.

## 6. Appending an edit

### 6.1 Command

```text
AppendEdit(
  review_item_id,
  expected_candidate_revision_id,
  reason, note?,
  fields: [{field_path, input_text} | {field_path, absent: true}],
  citations: [{check_id, field_path, accepted_value?}],
  dismissals: [descriptor],
  operator_name, idempotency_key
)
```

The command lives in `services/application/edits.py`. Like `decide_review`, it has no knowledge of HTTP.

### 6.2 Transaction

One transaction, in this order. Any failure rolls back every write.

1. **Idempotency.** Lock by idempotency key. If an edit with this key exists, return it unchanged when the command matches the stored one. Otherwise raise `edit_idempotency_conflict`.
2. **Lock order.** Lock promotions (the same first lock `decide_review` takes, so edits and decisions can't deadlock), then lock the review item.
3. **Editability.** The record is editable only when all of these hold:
   - the review item's current assessment verdict is not `REJECTED` (typeless shells stay read-only);
   - the current effective state is `pending` or `rejected`;
   - no canonical revision is current for this record's own candidate chain.

   Otherwise raise `edit_not_editable`, with a reason code of `approved`, `acknowledged`, or `typeless`.
4. **Staleness.** `expected_candidate_revision_id` must equal the terminal version. Otherwise raise `edit_stale`, with the current revision id and edit number.
5. **Validate input.**
   - Every `field_path` must be an editable field of the entity: payload fields excluding `entity_type`, `*_annotation`, `name_match_key`, `customer_match_key`, and `*_gbp`. Otherwise raise `edit_field_not_editable`.
   - Citations and dismissals must match the reason and the catalogue (`edit_reason_mismatch`, `edit_citation_invalid`).
   - Every dismissal must exist on the current assessment (`edit_dismissal_invalid`).
6. **Parse.** Run each entered value through the same normaliser the CSV path uses for that field, with the run's normalise context. `absent: true` produces `ABSENT`. Unparseable input produces `UNRESOLVED` plus the normaliser's usual issue, and is not an error.
7. **No-op check.** If every parsed field equals the terminal version's field (typed equality) and the citations and dismissals equal the current edit's, raise `edit_no_change`.
8. **Append.**
   - Build the edited version as revision `terminal.revision_number + 1`, with the parent set to the terminal version, `origin = HUMAN_EDIT`, and a payload equal to the terminal payload with the parsed fields replaced.
   - Write the version, its `HUMAN_EDIT` transformations, the parse issues, and the `human_edit` row. `edited_at = clock.now()`; `edit_number` is the previous edit's number + 1; `parent_hash` is the previous edit's `content_hash`.
9. **Re-check** (§7). Write any derived child versions, their issues and transformations, the classification result, dependency records, and the `review_assessment` row with sequence = previous + 1.
10. **Commit.** Return the edit, the edited and terminal version ids, and the new assessment (verdict, readiness, remaining issues, legal actions).

### 6.3 Content hash

`content_hash` = lowercase hex SHA-256 of the UTF-8 canonical JSON (`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`) of:

```text
{ run_id, raw_record_id, review_item_id, edit_number,
  parent_revision_id, parent_hash, candidate_revision_id,
  payload,            # edited version payload, via the existing derived codec
  reason, note, field_changes, citations, dismissals,
  rules_version, operator_name,
  edited_at }         # ISO-8601 UTC with microseconds
```

Because `parent_hash` chains edits, altering any stored edit breaks the hash of every edit after it. A verification function recomputes the chain for a record and reports the first mismatching edit number.

### 6.4 Override, not overwrite

A second edit appends revision N+2 whose parent is the terminal version at that time. Earlier edits and versions are never modified. The current edit is the one with the highest `edit_number`. Its citations and dismissals alone govern the re-check. Earlier edits' citations and dismissals no longer apply, and the popup pre-fills them so the operator carries them forward deliberately.

## 7. Re-check

### 7.1 Graph construction

Load the run's raw records and **all persisted candidate revisions** into a `RunCandidateGraph`, including every record's derived and edited revisions. The edited record's terminal version is its new edited version.

The barrier check still applies: every data record must have revision 1. The re-check never re-derives other records from revision 1.

### 7.2 Effect isolation

Run the default registry through a new `apply_effects(graph, rule, only_raw_record_id=...)` mode. It discards every effect whose target is not the edited record's terminal version.

The only persisted writes are for the edited record: child versions, issues, transformations, dependencies, and the classification result. No row belonging to another record is inserted or changed. Same-run duplicate relations are the one exception. They are read from the persisted `duplicate_relation` rows rather than recomputed, because they compare raw fields that an edit can't change.

### 7.3 Context from other records

Other records' verdicts and readiness come from their current assessments, not from recomputation. The dependency fixed point in `classify_graph` seeds `ready` from those stored results and evaluates only the edited record. It becomes ready only when every resolved dependency target is itself ready, or is already current canonical.

### 7.4 Repairs and derived values

- `SKU_ZERO_PADDING` and `LINE_TOTAL_REPAIRED` skip any field that is human-set.
- `FX_BINDING` runs as it does in the pipeline. Whenever the record's money amount (`unit_price` or `lifetime_spend`) is known, it appends exactly one `FX_BINDING` child with GBP recomputed from the edited amount, currency, and date. The terminal version is then `edited version + 1`. When the amount isn't known, no child is appended and the edited version is terminal.
- The edited version itself stores `*_gbp` as `ABSENT`, so a stale conversion copied from the parent is never shown as the edited version's value.
- Match keys are recomputed from edited names by the normaliser in §6.2 step 6.

### 7.5 Reason: business rule updated

Before verdicts are derived, an issue is dropped when a citation matches its `check_id` and `field_path`. For vocabulary checks, the rejected value must also equal `accepted_value`. For tags, the waiver covers exactly the listed tags, and an issue listing any other unknown tag is kept.

Interpretation citations never drop anything. They are stored and displayed. Issues with `check_id = NULL` (parse failures) are never dropped.

### 7.6 Reason: graph wrong

Dismissal descriptors are stable across versions, because they reference raw records, runs, and canonical identities rather than issue ids:

| Finding | Descriptor | Effect when dismissed |
|---|---|---|
| Same-run exact duplicate | `{kind: "duplicate", earlier_raw_record_id}` | The relation no longer makes this record `DUPLICATE`. The relation row itself is kept. |
| Business-ID conflict | `{kind: "conflict", scope: "same_run" \| "earlier_run", counterpart_id}` | The `BUSINESS_KEY_CONFLICT` issue for that counterpart is dropped. |
| Resolved dependency match | `{kind: "dependency_match", dependency: "customer" \| "product" \| "referral", target_raw_record_id}` | That target is excluded. The dependency becomes `UNRESOLVED` (or resolves to a different unique match), which can only block, never unblock. |

Unresolved or ambiguous dependencies can't be dismissed. The fix is to edit the reference field.

### 7.7 Verdict and result

Verdicts are derived by the existing `classify_graph` precedence, applied to the edited record after waivers and dismissals. The classification id is `deterministic_id(terminal.id, rules_version, fx_snapshot_id)`, as today. A new revision id guarantees a new result.

Re-observation: if the edited values exactly equal an earlier run's current canonical revision for the same business key, readiness becomes `ineligible`, as for any re-observation.

## 8. Review state and decisions

- **Effective state** is the latest decision's state **only if that decision's `candidate_revision_id` equals the current terminal version**. Otherwise it is `pending`. After an edit, a previously rejected record is therefore pending again.
- **Legal outcomes** become `legal_outcomes(verdict, edited: bool)`, where `edited` means the current assessment has a non-null `human_edit_id`. When `edited` is true, `CLEAN`, `AUTO_REPAIRED`, and `NEEDS_REVIEW` allow approve/reject, and `DUPLICATE` allows acknowledge/reject. Unedited behaviour is unchanged.
- `decide_review` reads the classification from the current assessment. Its existing staleness check against the terminal version rejects decisions made on a superseded version. "A superseding decision must change the outcome" applies only when the previous decision was on the same candidate revision.
- Approval promotes the terminal version and runs the existing dependant cascade.
- Queue and workspace attention counts use the new effective-state rule. The run's `counts` snapshot is unchanged.

## 9. API and errors

### 9.1 Endpoints

| Method and path | Purpose |
|---|---|
| `GET /api/reviews/{id}/edit-context` | `editable` plus the not-editable reason; current revision id; edit number; editable fields with current value, state, source text, and setting edit; applicable catalogue checks (fired first); dismissible findings with descriptors; current edit's citations and dismissals for pre-fill |
| `POST /api/reviews/{id}/edits/preview` | Runs §6.2 steps 3 to 9 in a transaction that is always rolled back. Returns parsed values, remaining issues, and the predicted verdict and readiness. Writes nothing, including failure rows. |
| `POST /api/reviews/{id}/edits` | Runs §6.2. Returns the edit, versions, and new assessment: `201` for a new edit, `200` for an idempotent replay (the convention `POST /api/uploads` uses). |

Review detail gains `edits` (newest first, each with `overridden_by_edit_number`), `assessment_sequence`, edit-aware legal actions, and `human_edit` evidence nodes linked from field provenance. Run detail and queue rows gain `edit_count`. The decision endpoint is unchanged.

### 9.2 Error codes

All errors use the existing envelope `{error: {code, message, details}}`. Messages are plain language, and database or driver text never crosses HTTP.

| Code | HTTP | When | `details` |
|---|---|---|---|
| `edit_not_editable` | 409 | §6.2 step 3 | `reason` |
| `edit_stale` | 409 | step 4 | `current_candidate_revision_id`, `current_edit_number` |
| `edit_idempotency_conflict` | 409 | step 1 | none |
| `edit_no_change` | 422 | step 7 | none |
| `edit_reason_mismatch` | 422 | citations/dismissals missing or given for the wrong reason | `reason` |
| `edit_field_not_editable` | 422 | step 5 | `field_path` |
| `edit_citation_invalid` | 422 | step 5 | `check_id`, `field_path` |
| `edit_dismissal_invalid` | 422 | step 5 | `descriptor` |
| `edit_parse_failed` | 500 | a normaliser raised (ordinary bad input never does) | `stage`, `retryable: true`, `reference` |
| `edit_recheck_failed` | 500 | §7 raised | `stage`, `retryable: true`, `reference` |
| `edit_persist_failed` | 500 | a write or commit failed | `stage`, `retryable: true`, `reference` |
| `edit_lock_timeout` | 503 | lock not acquired within the configured timeout (default 5 s) | `retryable: true`, `reference` |
| `processing_parse_failed` | 500 | process/retry: parse stage failed | `stage`, `run_id`, `retryable: true`, `reference` |
| `processing_normalise_failed` | 500 | normalise stage failed | as above |
| `processing_classify_failed` | 500 | classify stage failed | as above |
| `processing_load_failed` | 500 | load stage failed | as above |
| `processing_invalid_state` | 422 | run cannot be processed from its state | `state` |
| `database_unavailable` | 503 | database unreachable on any endpoint | `retryable: true`, `reference` |

For each 500 or 503 with a `reference`, the server logs one entry with that reference, the code, the stage, and the exception type. For edit worker failures, a separate transaction also records an `edit_attempt_failure` row when the database is reachable. The review detail shows the latest one if it is newer than the current edit.

Processing stage failures are already persisted as `stage_failure` and failure events. The mapping adds the code and reference to the response only.

## 10. Interface

The approved visual baseline is [layout C](../../../.superpowers/brainstorm/86906-1789763248/content/edit-popup-detail.html).

- **Entry points.**
  - **Edit record** in the record detail on the review page and on the run page.
  - A per-field edit button in field evidence, which opens the popup focused on that row.
  - When the record isn't editable, the button is disabled and shows the plain-language reason.
- **Popup.**
  - Built on a native `<dialog>`. It contains:
    - a header with identifier, filename, source line, version, and state;
    - the reason as a segmented control;
    - a comparison table (field · source text · current · new value) with a live interpreted value or issue under each new value;
    - "Show all fields";
    - the catalogue checks list (business rule) or findings list (graph), fired items first;
    - an **After append** result;
    - operator and note fields;
    - a footer stating that appending adds a version and changes nothing earlier.
  - Changed rows are highlighted and labelled "Changed". Below 640 px the table becomes one card per field.
- **Behaviour.**
  - Focus stays inside the dialog and returns to its opener on close. Escape and Cancel confirm before discarding changes.
  - Preview requests are debounced (300 ms) and cancel superseded requests. The operator name is remembered.
  - Append is disabled while saving. On success the dialog closes, a live region announces "Edit #N appended · {absolute timestamp with timezone}", and detail, queue, and decision form refresh.
  - `edit_stale` keeps input, refreshes context, and explains what changed.
  - Worker failures show the error box with code and reference. Retryable ones offer **Retry append** with the same idempotency key.
- **History.**
  - The **Edits** timeline (newest first) shows, per edit: number, `Current` or `Overridden by edit #N`, reason label, operator, absolute timestamp, before → after, citations or dismissals in words, note, and short hashes (the full hash is in details).
  - Field evidence labels human-set values "✎ Edit #N · {operator}".
- **Language.** `labels.ts` adds `data_wrong` → "Data is wrong", `business_rule_updated` → "Business rule updated", `graph_wrong` → "Record links are wrong", `HUMAN_EDIT` → "Edited by operator", a label for every catalogue id (for example `vocab.product_category` → "Category vocabulary"), and a message and next step for every error code in §9.2.
- **Accessibility.** Everything is keyboard-operable, color is never the only signal, targets are at least 24 px, `prefers-reduced-motion` is honored, and there are no axe violations.

## 11. Behavioural test contract

Tests establish behaviour. They are not smoke tests. Each test below pins exact inputs and asserts exact outputs, persisted rows, and unchanged state, including what must **not** happen. Tests are written before the implementation (red, then green). The clock is fixed at `2026-09-18T13:07:31.123456Z` and the display timezone is `Europe/London`. Core domain behaviour uses real objects; fakes are allowed only at the filesystem, clock, and injected-failure boundaries.

### 11.1 Domain and application (unit)

File: `tests/unit/edits/test_append.py`, `test_hash.py`, `test_recheck.py`, `test_catalogue.py`

| ID and test name | Required assertion |
|---|---|
| `EDT-01 test_edit_appends_next_revision_without_touching_parent` | Given an order with chain 1 `normalise` → 2 `SKU_ZERO_PADDING` → 3 `FX_BINDING` and a known unit price, a `data_wrong` edit setting `order.quantity` to `"3"` creates revision 4 (`origin = HUMAN_EDIT`, parent = revision 3, `quantity = KNOWN 3`, `unit_price_gbp = ABSENT`) with exactly one `HUMAN_EDIT` transformation (before `KNOWN 2`, after `KNOWN 3`, sequence = previous max + 1). It also creates revision 5 (`FX_BINDING`, parent = revision 4), which is terminal. Revisions 1 to 3 compare equal to their pre-edit snapshots. |
| `EDT-02 test_input_uses_csv_normalisers` | Parameter cases on the matching fields produce exactly the CSV results: `"$1,240.50"` → `KNOWN Money(1240.50, USD)`; `"03/04/2023"` → `KNOWN 2023-03-04`; `"TBD"` → `DEFERRED`; `{absent: true}` → `ABSENT`; `"many"` on `order.quantity` → `UNRESOLVED` with one `INVALID_INTEGER` issue whose `check_id` is `NULL`. None of them raise. |
| `EDT-03 test_non_editable_fields_are_refused` | `order.customer_match_key`, `order.unit_price_gbp`, `order.entity_type`, and `order.nonexistent` each raise `edit_field_not_editable` naming that path, and no rows are written. |
| `EDT-04 test_reason_pairing_is_enforced` | `data_wrong` with a citation, `business_rule_updated` without citations, `graph_wrong` without dismissals, and `graph_wrong` with a citation each raise `edit_reason_mismatch`. `data_wrong` with no field changes raises it too. `business_rule_updated` with a citation and no field changes is accepted. |
| `EDT-05 test_no_change_is_refused` | Re-submitting `"19.99"` for a field whose current value is `KNOWN Money(19.99, GBP)` with the same citations and dismissals raises `edit_no_change`. Changing only a citation is accepted. |
| `EDT-06 test_hash_is_deterministic_and_chained` | The same inputs produce the same 64-character hash. Changing any one of note, operator, `edited_at` (by 1 µs), a citation, or one field value each changes it. Edit 2's `parent_hash` equals edit 1's `content_hash`, and edit 1's `parent_hash` is null. |
| `EDT-07 test_hash_chain_verification_detects_tampering` | For three edits, verification passes. After mutating edit 2's stored note in the test store, verification reports first mismatch = edit 2 and does not report edit 1. |
| `EDT-08 test_reappend_overrides_without_overwriting` | For an order with chain 1 `normalise` → 2 `FX_BINDING`, two edits to `order.customer_name_raw` (`"Sofia Rosi"`, then `"Sofia Rossi"`) leave exactly six revisions: 3 `HUMAN_EDIT`, 4 `FX_BINDING`, 5 `HUMAN_EDIT` (parent = 4), and 6 `FX_BINDING` (terminal). Both edits are stored unchanged, `overridden_by_edit_number(edit 1) = 2`, edit 2 is current, and the terminal `customer_name_raw` is `"Sofia Rossi"`. |
| `EDT-09 test_override_replaces_waivers` | Edit 1 cites `vocab.product_category` accepting `Garden`. Edit 2 (`data_wrong`, stock changed, no citations) re-raises `INVALID_CATEGORY` for `Garden` on the new assessment. |
| `EDT-10 test_vocabulary_waiver_is_value_specific` | Citing `vocab.product_category` with `accepted_value = "Garden"` drops the issue for `Garden`. The same citation on a record whose category is `Gardening` keeps `INVALID_CATEGORY`. For tags `["gift", "promo"]`, accepting only `gift` keeps an `UNKNOWN_TAG` issue that names `promo`. |
| `EDT-11 test_field_waiver_is_check_and_field_specific` | Citing `limit.order_quantity_nonzero` on `order.quantity` drops the zero-quantity issue but not `REFUND_CONFLICT` on the same field. Citing `invariant.refund` drops only the refund issue. |
| `EDT-12 test_parse_failures_are_never_waivable` | No catalogue id maps to a `NULL`-check issue. An `UNRESOLVED` quantity keeps its `INVALID_INTEGER` issue under any citation set, and the verdict is `NEEDS_REVIEW`. |
| `EDT-13 test_interpretation_citation_records_without_dropping` | Citing `interpret.slash_date_mdy` with date input `"2023-04-03"` stores the citation, drops no issue, and leaves `ordered_at = 2023-04-03`. |
| `EDT-14 test_citation_must_apply_to_entity_and_field` | `vocab.order_status` on a product, `format.sku` on `order.order_id`, and a vocabulary citation without `accepted_value` each raise `edit_citation_invalid`. |
| `EDT-15 test_human_set_fields_are_not_repaired` | An edit setting `order.sku` to `"SKU-00204"` produces no `SKU_ZERO_PADDING` child, and the terminal SKU is `SKU-00204` with its `INVALID_IDENTIFIER` issue unless `format.sku` is cited. An edit that doesn't touch `sku` keeps the earlier repaired value `SKU-2004`. |
| `EDT-16 test_fx_is_recomputed_from_edited_values` | Editing `order.unit_price` from `"$19.99"` to `"$25.00"`, with the fixed rate snapshot, yields a terminal `unit_price_gbp` equal to `25.00 × rate`, rounded per the FX config, and an `FX_CONVERTED` transformation on the child version. |
| `EDT-17 test_recheck_writes_only_the_edited_record` | With a run of 5 records, a snapshot of every row for the other 4 (revisions, issues, transformations, classifications, dependencies, review items, assessments) is identical before and after an edit. |
| `EDT-18 test_duplicate_dismissal` | A later exact duplicate edited with `graph_wrong` and dismissal `{duplicate, earlier_raw_record_id}` is no longer `DUPLICATE`. The `duplicate_relation` row still exists, and legal actions become approve/reject. Without the dismissal, the verdict stays `DUPLICATE` with acknowledge/reject. |
| `EDT-19 test_conflict_dismissal` | A same-run business-ID conflict is dropped only for the dismissed counterpart. A second conflict with a different counterpart remains. |
| `EDT-20 test_dependency_dismissal_can_only_block` | An order whose customer resolved to record X, with that match dismissed and no other match, gets dependency state `UNRESOLVED`, readiness `blocked_by_dependency`, and an `UNRESOLVED_REFERENCE` issue. Dismissing an unresolved dependency raises `edit_dismissal_invalid`. |
| `EDT-21 test_repointing_by_reference_edit` | Editing `order.customer_name_raw` from `"S. Rossi"` (unresolved) to `"Sofia Rossi"` resolves the customer dependency to the record whose match key is `sofia rossi`. Readiness follows that target's stored readiness. |
| `EDT-22 test_context_readiness_comes_from_stored_assessments` | If the target customer's stored assessment is `NEEDS_REVIEW`, the edited order is `blocked_by_dependency` even though a fresh recomputation of the customer would be clean. |
| `EDT-23 test_effective_state_resets_after_edit` | A record rejected on revision 1 is `pending` after an edit. `legal_outcomes(CLEAN, edited=True)` = (approve, reject), and `legal_outcomes(CLEAN, edited=False)` = (). |
| `EDT-24 test_rejecting_again_after_edit_is_legal` | After reject (revision 1) → edit → reject (revision 2), the second rejection is accepted. On one revision, reject → reject still raises `IllegalDecisionError`. |

### 11.2 Persistence and concurrency (PostgreSQL integration)

File: `tests/integration/edits/test_edits_postgres.py`, `test_edit_migration.py`

| ID and test name | Required assertion |
|---|---|
| `EDT-30 test_edit_run_must_match_record_run` | A direct insert of `human_edit` whose `run_id` differs from the record's run violates the composite FK. |
| `EDT-31 test_reprocess_does_not_inherit_edits` | After an edit in run A, `reprocess` creates run B. B's records have only pipeline revisions, zero `human_edit` rows, and assessments with `human_edit_id IS NULL`. |
| `EDT-32 test_duplicate_upload_sees_edits` | Uploading the same bytes again reuses run A, and review detail returns the existing edits. |
| `EDT-33 test_decision_on_superseded_version_is_stale` | A decision submitted with revision N after an edit created N+1 raises `StaleDecisionError`, and no decision, canonical, or event row is written. |
| `EDT-34 test_approving_edited_version_promotes_and_unblocks` | Approving an edited customer creates canonical revision 1 from the terminal version, and a dependent order blocked on it is promoted in the same transaction (one activation event each). |
| `EDT-35 test_approved_record_not_editable_until_reversed` | Editing an approved record raises `edit_not_editable` with reason `approved`. After a reversal (rejection), the edit succeeds and the record is pending. |
| `EDT-36 test_idempotent_replay` | Replaying the same key and payload returns the same edit id and hash and writes nothing. The same key with a different note raises `edit_idempotency_conflict`. |
| `EDT-37 test_concurrent_edits_serialise` | Two concurrent edits expecting the same revision produce one edit and one `edit_stale`. An edit racing a decision produces exactly one success. |
| `EDT-38 test_each_worker_failure_rolls_back_and_records` | Injected failures in parse, re-check, and persist each return their code with a UUID `reference` and write zero edit, revision, issue, classification, or assessment rows. Exactly one `edit_attempt_failure` row with that reference and stage is written. |
| `EDT-39 test_preview_writes_nothing` | A preview returns the same verdict and issues a subsequent append produces, and row counts for every table are unchanged after preview, including on failure. |
| `EDT-40 test_migration_backfills_assessments_and_check_ids` | After upgrading a database processed at `0005`, every review item has exactly one assessment (sequence 1, `human_edit_id NULL`). Domain issues have the mapped `check_id`. A parse `INVALID_INTEGER` stays `NULL` while the non-zero-quantity one maps to `limit.order_quantity_nonzero`. Downgrade restores `0005`. |
| `ERR-01 test_processing_failures_return_stage_codes` | An injected failure in each of parse, normalise, classify, and load returns `processing_{stage}_failed` with `run_id` and a `reference`, and the run's persisted `stage_failure` is unchanged from today's behaviour. |
| `ERR-02 test_database_unavailable` | With the database connection refused, the API returns `503 database_unavailable` with a reference and no driver text in the body. |

The golden fixture test (`tests/integration/test_messy_sample_data.py`) is updated for the new rules version only. Every other expectation must remain identical.

### 11.3 API (integration)

File: `tests/integration/api/test_edit_api.py`

| ID and test name | Required assertion |
|---|---|
| `API-12 test_edit_context_shape` | For a `NEEDS_REVIEW` product, `edit-context` lists editable fields (excluding derived ones), the fired check first, and the dismissible findings with descriptors. An approved item returns `editable: false` with reason `approved`. |
| `API-13 test_append_response_and_detail` | `POST /edits` returns 201 with edit number 1, `edited_at` equal to the fixed clock, and a 64-character hash. The next `GET` review detail lists the edit as `Current`, has `assessment_sequence = 2`, and has field provenance pointing at the edit. |
| `API-14 test_every_edit_error_code` | Each error in §9.2 is produced through HTTP with its exact status, code, and `details` keys, and no response body contains SQL or driver text. |

### 11.4 Web (Vitest, Testing Library, axe)

File: `apps/web/src/features/edits/EditDialog.test.tsx`, `EditHistory.test.tsx`

| ID and test name | Required assertion |
|---|---|
| `UI-19 edit_dialog_reason_changes_sections` | Choosing "Business rule updated" shows the checks list and hides findings. "Record links are wrong" shows findings and hides checks. "Data is wrong" shows neither. Append is disabled until the reason's requirement is met. |
| `UI-20 edit_dialog_shows_source_current_and_preview` | Each row shows exact source text and current value. Typing `03/04/2023` shows "4 March 2023" after the debounced preview, and the row is labelled "Changed". |
| `UI-21 edit_dialog_keyboard_and_focus` | Opening moves focus into the dialog, Tab stays inside it, Escape with changes asks for confirmation, and closing returns focus to the opener. The flow completes by keyboard alone. |
| `UI-22 edit_dialog_error_states` | `edit_stale` keeps typed values and shows the refresh explanation. `edit_recheck_failed` shows code and reference plus **Retry append**, and the retry reuses the same idempotency key. `edit_not_editable` renders its specific message. |
| `UI-23 edit_history_current_and_overridden` | With two edits, #2 shows "Current" and #1 shows "Overridden by edit #2" as text. Both show absolute timestamps with timezone, no relative words, and short plus full hash. |
| `UI-24 edit_button_disabled_with_reason` | For an approved record the button is disabled and shows "Approved records must be reversed before editing". |
| `UI-25 edit_surfaces_have_no_axe_violations` | Dialog (each reason, error, narrow width) and history produce zero axe violations. |

### 11.5 Browser acceptance (Playwright)

File: `apps/web/e2e/operator-flow.spec.ts` (extended)

| ID | Required assertion |
|---|---|
| `E2E-02` | Against the real API and PostgreSQL: open a `NEEDS_REVIEW` record, append a `data_wrong` edit, re-append with `business_rule_updated` citing a vocabulary check, approve, and confirm the canonical revision's values equal edit 2's. The history shows #1 overridden and #2 current. A stale second tab receives `edit_stale` without writing. Axe checks the open dialog. |

## 12. Changed components

| Area | Files |
|---|---|
| Domain | `services/domain/edits.py` (new: `HumanEdit`, `EditReason`, `Citation`, `Dismissal`, hash), `services/domain/issues.py` (`HUMAN_EDIT`, `check_id`), `services/domain/decisions.py` (`legal_outcomes`, effective state) |
| Rules | `services/pipeline/rules/catalogue.py` (new), `domains.py` and `invariants.py` (emit `check_id`), `base.py` (`only_raw_record_id`), `repairs.py` (skip human-set fields), `registry.py` (catalogue in hash) |
| Application | `services/application/edits.py` (new: append, preview, verify chain), `recheck.py` (new), `decisions.py`, read ports and query services |
| Infrastructure | `services/infrastructure/db/models.py`, `edit_repository.py` (new), `read_repository.py`, `uow.py` |
| Migration | `db/migrations/versions/0006_human_edits.py` |
| API | `services/api/routes/edits.py` (new), `errors.py`, `models.py`, `presenters.py`, `routes/runs.py` (processing codes) |
| Web | `apps/web/src/features/edits/` (new: `EditDialog`, `EditTable`, `CheckList`, `FindingList`, `EditHistory`), `api/contracts.ts`, `api/queries.ts`, `labels.ts`, `features/reviews/ReviewDetail.tsx`, `features/runs/EvidencePanel.tsx` |

## 13. Decisions recorded during design

| Question | Decision |
|---|---|
| Outcome after append | Re-check, then a human decision is required. An edit never promotes by itself. |
| Business rule reason | Cite named catalogue checks. Vocabulary waivers are value-specific; format, limit, and invariant waivers are field-specific; interpretation policies are citation only. All four kinds are citable. |
| Graph reason | Edit reference fields and dismiss specific findings (duplicate, conflict, resolved match). A dismissal can only block. |
| Editable records | Review items only, pending or rejected. |
| Re-check approach | Single record, isolated effects, other records from stored assessments. |
| Run scoping | An edit is tied to its record's run. It is visible to duplicate uploads of that run and not inherited by reprocessed runs. Once approved it is canonical and shared, like any approval. |
| Popup layout | C, the comparison table. |
| Error codes | Specific codes for edit and processing worker failures, each with a reference. |
| CLI | None. |

## 14. Documentation updates on delivery

- Amend `docs/ARCHITECTURE.md`:
  - §2 candidate revisions (`HUMAN_EDIT` origin);
  - §3.6 review and decide (effective state, legal outcomes);
  - §6 ownership table (`human_edit`, `review_assessment`, `edit_attempt_failure`);
  - §7.1 API table;
  - §8 interface;
  - §11 test contract (reference this spec's IDs).
- Remove the human-editing line from `TODO.md`. Keep configurable rules deferred, noting that stored citations are its evidence base.
- Update `README.md`: operator flow step for editing, and the quality-gate counts after the run.
