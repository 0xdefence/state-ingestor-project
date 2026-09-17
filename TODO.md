# Deferred architecture work

These items are outside the actionable-review MVP. The canonical scope lives in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Review expansion

- Add human editing through append-only candidate revisions; do not mutate source or existing revisions.
- Add per-issue decisions and mixed outcomes inside one record if product evidence shows they are needed.
- Add bulk decisions with explicit preview, partial-failure policy, and audit evidence.
- Add assignment, claimed/in-review presence, service-level targets, and escalation workflows for multiple operators.
- Add logical deletion and restoration as governed actions. Preserve raw evidence, revisions, classifications, decisions, and audit history.
- Decide whether acknowledgement requires a reason and whether every approval requires a note after observing operator use.

## Publication lifecycle

- Separate publication from canonical promotion.
- Define `not_published`, `partially_published`, `published`, and `discarded` behavior.
- Publish eligible canonical records idempotently and expose publication history.
- Decide policy for advancing duplicate or re-observation lineage after publication.

## Identity, roles, and authorization

- Add application users, authentication, and roles.
- Keep the database user record authoritative for the current role; use identity tokens to prove identity.
- Bootstrap the first administrator from an explicit allow-listed identity or deployment secret.
- Enforce role-specific permissions and server-side field masking in the application layer.
- Add authorization and masking tests for every endpoint and role.

## Product and interface expansion

- Add advanced outcome charts using the same accessible count projection as the MVP.
- Add saved filters, bulk selection, richer search, export, and configurable dashboard layouts.
- Add nonessential motion only after workflows are stable, followed by a reduced-motion review.
- Add dedicated mobile layouts and broader device/browser matrices; the MVP only prevents destructive overflow and preserves basic keyboard access.
- Add optional Figma transfer or implementation from an approved Figma source.
- Keep Taste Skill for non-dashboard surfaces such as marketing, editorial, portfolio, and public landing pages.

## Configurable business rules

- Add governed rule configuration, version approval, simulation, and rollback.
- Keep the MVP's registered value normalisation, currency conversion, and business rules in code with deterministic versions.
- Add operator-facing explanations for configuration changes before allowing runtime edits.

## Remote storage and hosted operation

- Implement a remote `SourceLocator` adapter while preserving the ingest contract.
- Support direct-to-object-storage browser upload without allowing the browser to register application state independently.
- Define retention, deletion, recovery, and integrity verification for remote source objects.
- Select production hosting for the web app, Python API, Postgres, object storage, and scheduled work; Vercel remains a possible web host, not an MVP dependency.
- Measure real file sizes and execution duration before moving synchronous processing to a background worker.
- Preserve batch checkpoints and stage contracts if processing moves to a queue or worker.
- Add deployment configuration, secrets management, backups, monitoring, and recovery procedures.

## Scheduled FX updates

- Add a scheduled ECB importer after the pinned-history fixture is proven.
- Authenticate scheduled calls and record source/version provenance.
- Append corrected rate versions rather than rewriting rates already referenced by runs.
- Add operational visibility and manual retry for failed imports.

## Expanded audit, retention, and compliance

- Add a security audit log for identity changes, governed commands, decisions, publication, and logical deletion.
- Define retention and exceptional administrative purge policy.
- Define data export, subject-access, and deletion obligations when product requirements are known.
