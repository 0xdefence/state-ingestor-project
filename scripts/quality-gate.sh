#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/pytest -q
.venv/bin/ruff check services tests
.venv/bin/pyright services
(cd apps/web && bun run test)
(cd apps/web && bun run typecheck)
(cd apps/web && bun run build)
(cd apps/web && bunx playwright test e2e/operator-flow.spec.ts)
