# Derived Pipeline and Atomic Staging Implementation Plan

> **Historical plan — do not execute.** Tasks 1–7 are implemented; the [completed Task 7 review and scoped re-review](../../../.superpowers/sdd/2026-09-17-actionable-review-vertical-slice/preliminary-task7-review.md) addressed both original findings and passed. Task 8 is replaced by Task 1 of the delivered [actionable-review vertical slice](2026-09-17-actionable-review-vertical-slice.md), verified on 18 September 2026. This plan remains the historical delivery record; current behavior and verification commands live in [the canonical architecture](../../ARCHITECTURE.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert immutable raw evidence into typed candidates, apply pinned FX and registered cross-record rules, classify every data record, and atomically stage only eligible terminal revisions.

**Architecture:** Normalization handles one raw record at a time and creates the initial revision or rejected shell. Classification begins only after the run-wide candidate barrier, appends repairs through an ordered rule registry, and writes deterministic results. Load stages the complete eligible set in one transaction and leaves all derived evidence intact after rollback.

**Tech Stack:** Python 3.12, pytest, SQLAlchemy 2, Alembic, PostgreSQL 16, Decimal, standard-library date parsing

**Spec:** [`docs/superpowers/specs/2026-09-16-local-csv-ingestion-review-design.md`](../specs/2026-09-16-local-csv-ingestion-review-design.md)

## Global Constraints

- Complete the foundation plan before starting this plan.
- Follow the exact `NOR`, `FX`, `CLS`, and `LOD` tests in architecture section 11 using red-green-refactor.
- Raw records and existing candidate revisions are immutable; every repair appends a child revision.
- Use `Decimal` for money and rates; binary floats are forbidden in domain and persistence code.
- Unsupported forms become structured unresolved values and issues; never infer a repair from prose.
- Classification cannot begin until every data raw record has an initial candidate or rejected shell.
- Verdict and dependency readiness remain separate fields.
- Load is one transaction for the whole eligible set.
- Tests use the fixed clock, pinned FX fixture, and no network.

---

## File map

| Path | Responsibility |
|---|---|
| `services/domain/candidates.py` | Typed customer/product/order payloads and revision envelopes |
| `services/domain/issues.py` | Registered issue codes, severities, transformations, verdicts, readiness |
| `services/pipeline/normalise/` | Field-state, money, date, status, tag, text, and structural normalization |
| `services/pipeline/fx.py` | Snapshot lookup and cross-rate conversion |
| `services/pipeline/rules/` | Ordered registry, repairs, relations, invariants, duplicates, conflicts |
| `services/pipeline/classify.py` | Barrier validation and idempotent classification orchestration |
| `services/application/load.py` | Atomic staged canonical command |
| `services/infrastructure/db/` | Derived/canonical models, repositories, migrations |
| `data/fx/` | Pinned ECB fixture and manifest |
| `tests/unit/normalise/` | `NOR-01` through `NOR-16`, `FX-01` through `FX-05` |
| `tests/unit/classify/` | `CLS-01` through `CLS-16` |
| `tests/unit/load/` | `LOD-01` through `LOD-09` |
| `tests/integration/test_messy_sample_data.py` | Frozen 54-line golden graph |

### Task 1: Candidate revisions, issues, and derived persistence

**Files:**
- Create: `services/domain/candidates.py`
- Create: `services/domain/issues.py`
- Create: `db/migrations/versions/0002_candidates_and_classification.py`
- Modify: `services/infrastructure/db/models.py`
- Modify: `services/infrastructure/db/repositories.py`
- Test: `tests/unit/normalise/test_candidates.py`
- Test: `tests/integration/pipeline/test_derived_migration.py`

**Interfaces:**
- Consumes: `CandidateField`, typed IDs, raw records, and unit of work from plan 1
- Produces: `CustomerCandidate`, `ProductCandidate`, `OrderCandidate`, `RejectedCandidateShell`, `CandidateRevision`
- Produces: `DataQualityIssue`, `TransformationEvent`, `ClassificationResult`, `Verdict`, `Readiness`

- [ ] **Step 1: Write failing candidate-field and revision-invariant tests**

```python
def test_child_revision_requires_parent_and_next_number() -> None:
    with pytest.raises(ValueError, match="child revision must name its parent"):
        CandidateRevision(
            id=REVISION_ID,
            raw_record_id=RAW_ID,
            revision_number=2,
            parent_revision_id=None,
            origin="SKU_ZERO_PADDING",
            payload=PRODUCT_PAYLOAD,
            created_at=FIXED_TIME,
        )
```

- [ ] **Step 2: Run candidate tests and verify failure**

Run: `pytest -q tests/unit/normalise/test_candidates.py -k 'field_states or revision'`

Expected: FAIL because candidate types are absent.

- [ ] **Step 3: Implement frozen candidate and issue types**

Use a `CandidatePayload` union and a revision envelope:

```python
CandidatePayload = CustomerCandidate | ProductCandidate | OrderCandidate | RejectedCandidateShell

@dataclass(frozen=True, slots=True)
class CandidateRevision:
    id: UUID
    raw_record_id: UUID
    revision_number: int
    parent_revision_id: UUID | None
    origin: str
    payload: CandidatePayload
    created_at: datetime
```

- [ ] **Step 4: Add derived-state migration and repositories**

Create `candidate_revision`, `transformation_event`, `data_quality_issue`, `classification_result`, `dependency_record`, `duplicate_relation`, and `review_item`. Add unique constraints from design section 6.2 and JSONB payload checks for `entity_type`.

- [ ] **Step 5: Verify migration and candidate round trip**

Run: `pytest -q tests/unit/normalise/test_candidates.py tests/integration/pipeline/test_derived_migration.py`

Expected: PASS.

- [ ] **Step 6: Commit derived domain and schema**

```bash
git add services/domain/candidates.py services/domain/issues.py db/migrations/versions/0002_candidates_and_classification.py services/infrastructure/db tests/unit/normalise/test_candidates.py tests/integration/pipeline/test_derived_migration.py
git commit -m "feat: add candidate revision and issue model"
```

### Task 2: Deterministic value normalization

**Files:**
- Create: `services/pipeline/normalise/money.py`
- Create: `services/pipeline/normalise/dates.py`
- Create: `services/pipeline/normalise/text.py`
- Create: `services/pipeline/normalise/tags.py`
- Create: `services/pipeline/normalise/status.py`
- Test: `tests/unit/normalise/test_values.py`

**Interfaces:**
- Produces: `normalise_money(raw: str, field: FieldPath) -> NormalisedMoney`
- Produces: `normalise_date(raw: str, field: FieldPath) -> CandidateField[date | datetime]`
- Produces: `normalise_text`, `normalise_tags`, `normalise_status`, and `match_key`

- [ ] **Step 1: Write failing `NOR-02` through `NOR-11` parameterized tests**

```python
@pytest.mark.parametrize(("raw", "amount", "currency"), [
    ("19.99", Decimal("19.99"), "GBP"),
    ("$1,240.50", Decimal("1240.50"), "USD"),
    ("45,00", Decimal("45.00"), "GBP"),
    ("€2.345,00", Decimal("2345.00"), "EUR"),
    ("1.5e3", Decimal("1500.00"), "GBP"),
])
def test_money_formats(raw: str, amount: Decimal, currency: str) -> None:
    result = normalise_money(raw, FieldPath("customer.lifetime_spend"))
    assert (result.amount, result.currency) == (amount, currency)
```

- [ ] **Step 2: Run value tests and confirm the expected missing-module failure**

Run: `pytest -q tests/unit/normalise/test_values.py`

Expected: FAIL because normalization modules do not exist.

- [ ] **Step 3: Implement money and date registries**

Compile exact approved patterns from fixture-spec sections 5.2 and 5.3. Return structured unresolved values plus registered issues for unmatched non-null input. Treat `03/04/2023` as 4 March 2023.

- [ ] **Step 4: Implement status, tags, text, and match keys**

Apply Unicode NFKC, remove category `So` only for match keys, collapse whitespace, then casefold. Preserve display strings independently. Reject leading/trailing empty tag segments.

- [ ] **Step 5: Run all value tests and static checks**

Run: `pytest -q tests/unit/normalise/test_values.py && ruff check services/pipeline/normalise && pyright services/pipeline/normalise`

Expected: `NOR-02` through `NOR-11` PASS.

- [ ] **Step 6: Commit value normalization**

```bash
git add services/pipeline/normalise tests/unit/normalise/test_values.py
git commit -m "feat: normalize supported field values"
```

### Task 3: Candidate assembly and structural salvage

**Files:**
- Create: `services/pipeline/normalise/candidates.py`
- Create: `services/application/normalise.py`
- Modify: `tests/unit/normalise/test_candidates.py`
- Create: `tests/integration/pipeline/test_normalise_recovery.py`

**Interfaces:**
- Produces: `build_initial_candidate(raw: RawRecord, context: NormaliseContext) -> NormaliseResult`
- Produces: `normalise_run(run_id, batch_size, uow_factory, context) -> StageResult`

- [ ] **Step 1: Write failing `NOR-12` through `NOR-16` tests**

Assert valid record types select the matching payload type; an unknown discriminator creates a rejected shell; the seven-field order marks trailing fields absent; the twelve-field customer joins note fields with a registered lossy-repair issue; and routine BOM/header/quote mechanics never become quality issues.

- [ ] **Step 2: Verify candidate-assembly tests fail**

Run: `pytest -q tests/unit/normalise/test_candidates.py -k 'short_row or overflow_notes or routine_parse'`

Expected: FAIL because candidate assembly is absent.

- [ ] **Step 3: Implement typed assembly and explicit salvage registry**

Structural salvage is keyed by shape and entity type. Support only missing trailing fields, extra trailing empties, and last-column note rejoin. The shifted nine-field line 39 produces a rejected shell without realignment.

- [ ] **Step 4: Implement checkpointed normalization**

Use one transaction per batch for candidate revisions, transformations, issues, and checkpoint. Deterministic revision/issue IDs make retry idempotent.

- [ ] **Step 5: Test failure and retry equality**

Run: `pytest -q tests/unit/normalise tests/integration/pipeline/test_normalise_recovery.py`

Expected: all `NOR-01` through `NOR-16` PASS and retry graph equals uninterrupted graph.

- [ ] **Step 6: Commit candidate normalization**

```bash
git add services/pipeline/normalise/candidates.py services/application/normalise.py tests/unit/normalise tests/integration/pipeline/test_normalise_recovery.py
git commit -m "feat: build typed candidate revisions"
```

### Task 4: Pinned FX snapshots and conversion

**Files:**
- Create: `data/fx/ecb-history.csv`
- Create: `data/fx/manifest.json`
- Create: `services/pipeline/fx.py`
- Create: `services/infrastructure/fx_importer.py`
- Create: `db/migrations/versions/0003_fx_snapshots.py`
- Test: `tests/unit/normalise/test_fx.py`
- Test: `tests/integration/pipeline/test_fx_import.py`

**Interfaces:**
- Produces: `FxSnapshot`, `FxRate`, `FxRepository.rate_on_or_before(...)`
- Produces: `convert_to_gbp(amount, currency, requested_date, snapshot) -> FxConversion`

- [ ] **Step 1: Create a pinned fixture manifest test**

```python
def test_fx_manifest_matches_fixture() -> None:
    manifest = json.loads(MANIFEST.read_text())
    assert sha256(FIXTURE.read_bytes()).hexdigest() == manifest["sha256"]
    assert manifest["source"] == "ECB"
```

- [ ] **Step 2: Write failing `FX-01` through `FX-05` tests**

Cover cross-rate calculation, prior-business-day selection, order-date conversion, run-snapshot conversion, and missing-rate review behavior.

- [ ] **Step 3: Run FX tests and verify failure**

Run: `pytest -q tests/unit/normalise/test_fx.py tests/integration/pipeline/test_fx_import.py`

Expected: FAIL because fixture/importer/converter are absent.

- [ ] **Step 4: Add FX migration, importer, and converter**

Persist EUR reference rates as `Decimal`. Calculate source→GBP as `EUR/GBP ÷ EUR/source`. Store amount, source currency, rate, publication date, and `ECB` provenance on the candidate field transformation.

- [ ] **Step 5: Run FX tests**

Run: `pytest -q tests/unit/normalise/test_fx.py tests/integration/pipeline/test_fx_import.py`

Expected: all `FX-01` through `FX-05` PASS.

- [ ] **Step 6: Commit pinned FX support**

```bash
git add data/fx services/pipeline/fx.py services/infrastructure/fx_importer.py db/migrations/versions/0003_fx_snapshots.py tests/unit/normalise/test_fx.py tests/integration/pipeline/test_fx_import.py
git commit -m "feat: convert currencies from pinned ecb rates"
```

### Task 5: Rule registry, repairs, and relationships

**Files:**
- Create: `services/pipeline/rules/base.py`
- Create: `services/pipeline/rules/registry.py`
- Create: `services/pipeline/rules/repairs.py`
- Create: `services/pipeline/rules/relationships.py`
- Create: `services/pipeline/rules/invariants.py`
- Create: `services/pipeline/classify.py`
- Test: `tests/unit/classify/test_rules.py`

**Interfaces:**
- Produces: `Rule.apply(graph: RunCandidateGraph) -> tuple[RuleEffect, ...]`
- Produces: `RuleRegistry.rules_version -> str`
- Produces: `classify_run(run_id, registry, uow, clock) -> ClassificationSummary`

- [ ] **Step 1: Write failing `CLS-01` through `CLS-06` tests**

Assert the candidate barrier, content-deterministic rules hash, SKU repair, order SKU repair, line-total-before-FX ordering, and the prohibition on revisions from validation-only rules.

- [ ] **Step 2: Run the first classification slice and verify failure**

Run: `pytest -q tests/unit/classify/test_rules.py -k 'barrier or rules_version or sku or line_total or validation_only'`

Expected: FAIL because the rule registry is absent.

- [ ] **Step 3: Implement ordered registry and repair effects**

```python
@dataclass(frozen=True, slots=True)
class RuleDefinition:
    id: str
    version: int
    repairable: bool
    applies: Callable[[RunCandidateGraph], bool]
    evaluate: Callable[[RunCandidateGraph], tuple[RuleEffect, ...]]
```

Hash canonical JSON containing ordered rule IDs, versions, repairability, and relevant normalizer config.

- [ ] **Step 4: Implement relation and invariant rules**

Resolve product SKU, customer match key, referral ID, refund invariant, and product stock/status invariant. Create dependency records for unresolved references rather than changing field verdicts.

- [ ] **Step 5: Run `CLS-01` through `CLS-06`, `CLS-13` through `CLS-16`**

Run: `pytest -q tests/unit/classify/test_rules.py -k 'not duplicate and not conflict and not reobservation'`

Expected: selected cases PASS.

- [ ] **Step 6: Commit rule registry and relationships**

```bash
git add services/pipeline/rules services/pipeline/classify.py tests/unit/classify/test_rules.py
git commit -m "feat: classify repairs and relationships"
```

### Task 6: Duplicates, conflicts, and idempotent classification

**Files:**
- Create: `services/pipeline/rules/duplicates.py`
- Modify: `services/pipeline/classify.py`
- Modify: `services/infrastructure/db/repositories.py`
- Modify: `tests/unit/classify/test_rules.py`
- Create: `tests/integration/pipeline/test_classification_retry.py`

**Interfaces:**
- Consumes: parsed-field arrays and prior canonical observations
- Produces: duplicate relations, conflict review items, re-observation links, terminal classifications

- [ ] **Step 1: Write failing `CLS-07` through `CLS-12` tests**

Use exact parsed arrays to prove that source lines and quoting do not distinguish duplicates while value whitespace does.

- [ ] **Step 2: Verify duplicate/conflict tests fail**

Run: `pytest -q tests/unit/classify/test_rules.py -k 'duplicate or conflict or reobservation'`

Expected: FAIL because duplicate rules are absent.

- [ ] **Step 3: Implement same-run and earlier-run comparisons**

Compare exact tuple fields for same-run duplicates. Compare governed identity plus derived typed values for earlier-run re-observation. Never use normalized equality to label an exact duplicate.

- [ ] **Step 4: Make classification persistence idempotent**

Use deterministic result, issue, relation, and review IDs keyed by candidate revision and rules version. Insert with conflict verification: an existing deterministic row must equal the requested row or raise an invariant error.

- [ ] **Step 5: Run all classification and retry tests**

Run: `pytest -q tests/unit/classify/test_rules.py tests/integration/pipeline/test_classification_retry.py`

Expected: all `CLS-01` through `CLS-16` PASS; retry creates no duplicate objects.

- [ ] **Step 6: Commit duplicate and conflict classification**

```bash
git add services/pipeline/rules/duplicates.py services/pipeline/classify.py services/infrastructure/db/repositories.py tests/unit/classify/test_rules.py tests/integration/pipeline/test_classification_retry.py
git commit -m "feat: classify duplicates and conflicts"
```

### Task 7: Atomic canonical staging

**Files:**
- Create: `db/migrations/versions/0004_canonical_staging.py`
- Create: `services/application/load.py`
- Modify: `services/infrastructure/db/models.py`
- Modify: `services/infrastructure/db/repositories.py`
- Create: `tests/unit/load/test_stage.py`
- Create: `tests/unit/pipeline/test_recovery.py`
- Create: `tests/integration/pipeline/test_atomic_load.py`

**Interfaces:**
- Produces: `stage_run(run_id, uow_factory, clock, failure_injector=None) -> LoadResult`
- Produces tables: `canonical_identity`, `canonical_revision`, `canonical_business_key`

- [ ] **Step 1: Write failing `LOD-01` through `LOD-06` tests**

Assert exact verdict/readiness mapping and lineage to the terminal candidate revision.

- [ ] **Step 2: Run load selection tests and verify failure**

Run: `pytest -q tests/unit/load/test_stage.py`

Expected: FAIL because `stage_run` is absent.

- [ ] **Step 3: Implement canonical schema and eligible-set calculation**

Calculate the entire set before opening the write transaction. Select only `CLEAN` and terminal `AUTO_REPAIRED` results with readiness `eligible`.

- [ ] **Step 4: Write failing `LOD-07` through `LOD-09` tests**

Inject failure after the first canonical insert and assert zero canonical writes, preserved derived state, `classified` primary state, and `load_failed` failure fact.

- [ ] **Step 5: Implement one-transaction staging and external failure recording**

Rollback the failed staging unit of work completely. Open a separate unit of work to record `load_failed`. Retry reads existing classifications and never reruns prior stages.

- [ ] **Step 6: Run load and recovery suites**

Run: `pytest -q tests/unit/load tests/unit/pipeline/test_recovery.py tests/integration/pipeline/test_atomic_load.py`

Expected: all `LOD-01` through `LOD-09` PASS.

- [ ] **Step 7: Commit atomic staging**

```bash
git add db/migrations/versions/0004_canonical_staging.py services/application/load.py services/infrastructure/db tests/unit/load tests/unit/pipeline/test_recovery.py tests/integration/pipeline/test_atomic_load.py
git commit -m "feat: stage eligible canonical revisions atomically"
```

### Task 8: Golden pipeline and equality proof

**Files:**
- Create: `tests/integration/test_messy_sample_data.py`
- Create: `tests/support/graph_snapshot.py`
- Modify: `services/cli/main.py`
- Modify: `data/messy_sample_data.schema.md` only if verified output exposes an actual fixture-spec error

**Interfaces:**
- Consumes: complete ingest → parse → normalize → classify → load pipeline
- Produces: deterministic persisted-graph snapshot for uninterrupted and recovered runs

- [ ] **Step 1: Write the failing frozen-identity assertions**

Assert SHA-256, 5,953 bytes, 54 physical lines, 52 logical records, 48 data records, two headers, two blanks, entity counts, multiline spans, and malformed widths exactly as architecture section 11.9 specifies.

- [ ] **Step 2: Run the golden test and confirm it fails before full orchestration**

Run: `pytest -q tests/integration/test_messy_sample_data.py -x`

Expected: FAIL because complete orchestration/snapshot support is absent.

- [ ] **Step 3: Add complete process orchestration**

`process_run` dispatches only the next legal stage, loops until `staged`, and returns after a recoverable failure. It never implements stage logic itself.

- [ ] **Step 4: Implement persisted-graph canonicalization**

Serialize stable IDs, relations, payloads, issues, classifications, review items, and canonical revisions in sorted order. Exclude transaction IDs and wall-clock database metadata; keep the fixed domain timestamps.

- [ ] **Step 5: Test every batch-boundary interruption**

For each configured parse and normalize boundary plus classification and load, inject one failure, retry, and compare its graph snapshot byte-for-byte with uninterrupted execution.

- [ ] **Step 6: Run the complete pipeline gate**

Run: `pytest -q tests/unit/normalise tests/unit/classify tests/unit/load tests/unit/pipeline/test_recovery.py tests/integration/pipeline tests/integration/test_messy_sample_data.py`

Run: `ruff check services tests && pyright services`

Expected: all pipeline and golden tests PASS with no unexpected warnings.

- [ ] **Step 7: Commit the golden pipeline**

```bash
git add services/cli/main.py tests/integration/test_messy_sample_data.py tests/support/graph_snapshot.py
git commit -m "test: prove complete ingestion pipeline"
```
