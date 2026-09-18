# Local CSV ingestion and review

The local MVP is implemented and verified. Operators can upload CSV files, process them, inspect immutable source evidence and interpreted values, record whole-record decisions, and reverse approvals while retaining canonical history. Identical file bytes reuse the existing run and preserve each submission.

## Local setup

Requirements: Python 3.12 or newer, `uv`, Bun, and local PostgreSQL. The checked-in Compose service uses PostgreSQL 16; the verification on 18 September 2026 used Python 3.13.12, Bun 1.3.5, Homebrew PostgreSQL 17.7, and Playwright Chromium 153.0.8010.12.

```sh
uv sync --frozen
(cd apps/web && bun install --frozen-lockfile)
# Start the supplied database if no local instance is already available:
docker compose up -d --wait postgres
(cd apps/web && bunx playwright install chromium)
./scripts/run-local.sh
```

Open <http://127.0.0.1:5173>. The launcher checks ports 8000 and 5173, applies every Alembic migration, starts FastAPI and Vite on loopback, and stops both child processes on exit. It refuses to silently choose different ports. The application imports the pinned local ECB fixture idempotently when opening its runtime; processing performs no external network calls.

Defaults are `DATABASE_URL=postgresql+psycopg://alexis:alexis@127.0.0.1:55432/alexis` and `SOURCE_ROOT=var/sources`. Set these variables before launch to select another local database or source directory. The launcher applies migrations to the selected database. For the default database, migrations can also be applied separately with `.venv/bin/alembic upgrade head`.

Set `APPLICATION_BUILD_REVISION` to a release or commit identity for new runs; the stable local default is `local-development`. API uploads and CLI ingest/reprocess persist it separately from the rules hash. Processing retries, idempotency replay, and exact-file reuse retain the run's original build identity.

Processing commands for the same run wait for PostgreSQL ownership before reading progress. A dedicated connection holds a transaction-scoped advisory lock across all stage commits and releases it on command exit or worker disconnect/crash. Each active command therefore uses one ownership connection in addition to its short-lived stage transaction; run the local API against PostgreSQL directly.

## Operator flow

1. Select `data/messy_sample_data.csv`, enter an operator name, and choose **Upload and process**. The run page shows **Processed**, evidence, outcome counts, and submission history.
2. Open the review queue and select a record. Arrow keys move between records; Enter opens the selected detail and focuses its heading. Expand source fields and compare them with interpreted values and registered issues.
3. Approve or reject an eligible exception. Rejected or duplicate records allow acknowledgement or rejection; dependency-blocked records wait for the referenced record. Every action records the operator; rejection requires a reason.
4. Inspect the saved decision and current canonical revision. To reverse approval, submit a rejection with a reason. The previous canonical revision remains in history and the current projection is withdrawn or restored to its predecessor.
5. Upload the same bytes again. Submission history increases while the same run is reused. If processing failed, **Retry processing** resumes the saved run from its durable boundary.

## Verification

Run the complete gate from the repository root with the application stopped so ports 8000 and 5173 are free:

```sh
./scripts/quality-gate.sh
```

It runs, in order, all Python tests, Ruff, strict Pyright, Vitest, TypeScript, the production build, and Playwright acceptance. The test database account needs `CREATEDB`: PostgreSQL tests and browser acceptance create and remove uniquely named disposable databases, and browser source storage is temporary. `TEST_POSTGRES_URL` selects the local test database connection; it defaults to the same local connection above. Tests do not reuse the operator application's database contents or source directory.

Focused commands:

```sh
.venv/bin/pytest -q tests/unit
.venv/bin/pytest -q tests/integration/test_messy_sample_data.py
(cd apps/web && bun run test)
(cd apps/web && bunx playwright test e2e/operator-flow.spec.ts)
```

On 18 September 2026 the complete gate passed: **615 Python tests, 63 web tests, 1 browser acceptance test**, Ruff, strict Pyright, TypeScript, and production build. Browser acceptance uses the real API, migrations, filesystem source storage, and PostgreSQL. Its test-only API fixture injects a second-batch normalisation failure and inspects persisted rows; those fixture endpoints are absent from the normal application. It verifies same-run retry, source/interpretation comparison, keyboard decisions, canonical promotion and reversal, exact-byte re-upload, and unchanged pipeline/canonical rows after reuse. Axe checks the workspace, failure, processed run, approved/reversed detail, and duplicate run. Screenshots are written under `apps/web/test-results/`; failing tests also retain traces.

## Scope and authority

[Architecture](docs/ARCHITECTURE.md) governs behavior; the [design specification](docs/superpowers/specs/2026-09-16-local-csv-ingestion-review-design.md) describes the delivered slice. The original sample accounts for 54 physical lines, 52 raw records, 48 classifications, and 17 initial canonical revisions.

Human field editing, bulk decisions, assignment, publication/export, authentication and roles, hosted deployment, remote storage, background workers, scheduled FX updates, and broader browser/device coverage remain deferred in [TODO.md](TODO.md). Browser acceptance currently covers Chromium on the local desktop setup; it is not a production deployment or security certification.
