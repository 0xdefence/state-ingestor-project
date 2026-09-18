# Product
<!-- impeccable:product-schema 1 -->

## Platform
web

## Users and purpose
Nontechnical operations reviewers upload and process local CSV imports, understand exceptions, record whole-record decisions, and promote approved revisions while preserving complete lineage.

## Capabilities and constraints
Python application services own parsing, normalisation, classification, persistence, retry, decisions, and promotion. Postgres stores application state and immutable source bytes are stored locally. Exact duplicate uploads reuse an existing run while preserving submission metadata. The browser is a human adapter over these services.

## Brand commitments
Vercel/Geist, neutral operational surfaces, rounded controls with nested radii, moderate density, textual outcome counts, and semantic colors paired with visible labels. Plain operational language and absolute timestamps with timezone.

## Accessibility
Desktop-first responsive web interface; keyboard-operable controls, visible focus, semantic HTML, readable outcomes independent of color.

## Evidence
Approved facts: docs/ARCHITECTURE.md and docs/superpowers/specs/2026-09-16-local-csv-ingestion-review-design.md. Revision-9 mockup supplies visual context; architecture governs conflicts.

## Assumptions
No additional product facts inferred. Initial workspace shows All files when no current run is specified; Current file requires an identified run.
