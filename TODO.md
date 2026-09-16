# Deferred architecture work

These items are outside the first local ingestion-and-review slice. They retain decisions already discussed without expanding the current implementation.

## Review resolution and governance

- Add append-only human candidate revisions and review states: `open`, `in_review`, `pending_approval`, reversible `outside_review`, terminal `resolved`, and terminal `dismissed`.
- Define legal state transitions and expose decision history.
- Add immutable `change_request` records for accept, reject/dismiss, logical delete, publication, and other governed actions.
- Keep the candidate revision as the sole source of proposed values. Change requests reference an exact revision plus preconditions; they do not copy the proposed row.
- Make `resolved` mean that a decision was authorized and applied successfully.
- Represent transient application failure separately from stale or superseded requests.
- Apply logical deletion only. Preserve raw evidence, revisions, classifications, decisions, and audit history.
- Use one action stream for direct administrator actions and submitted actions.
- Recheck review state, selected candidate revision, canonical version, dependencies, and run state atomically before applying a request.

## Publication lifecycle

- Add publication independently from processing status.
- Suggested publication states: `not_published`, `partially_published`, `published`, and `discarded`.
- Publish eligible staged records in an idempotent bulk action.
- Publish reviewed records individually after governed resolution.
- Prevent discarded runs from accepting later publication actions.
- Decide the exact policy for advancing duplicate or re-observation lineage after review.

## Identity, roles, and authorization

- Add application users and roles.
- Keep the database user record authoritative for the current role; use identity tokens to prove identity.
- Bootstrap the first administrator from an explicit allow-listed identity or deployment secret. Never grant ownership to the first arbitrary login.
- Enforce role-specific permissions in the Python application layer.
- Add server-side masking for restricted principals across records, issues, review items, actions, audit views, and exports.
- Reject search, filter, sort, group, and export operations on masked fields at the API boundary.
- Add authorization and masking tests for every endpoint and role.

## Production API and web workflows

- Add authenticated API adapters around the existing application services.
- Add upload initiation, review editing, resolution, publication, deletion, and administration commands.
- Build an Approvals screen with immutable before/after references and precondition failures.
- Build a live-data browser with provenance links.
- Expand the review queue from display to editable and governed workflows.
- Add user and role management.

## Remote object storage

- Implement a remote `SourceLocator` adapter while preserving the ingest contract.
- Support direct browser upload without allowing the browser to register application state.
- Keep the frozen content object separate from per-upload occurrence metadata.
- Define retention, deletion, recovery, and integrity verification for remote source objects.

## Scheduled FX updates

- Add a scheduled ECB importer after the pinned-history fixture is proven.
- Authenticate scheduled calls and record source/version provenance.
- Append corrected rate versions rather than rewriting rates already referenced by runs.
- Add operational visibility and manual retry for failed imports.

## Hosting and operations

- Select production hosting for the web application, Python API, Postgres, object storage, and scheduled work.
- Measure real file sizes and execution duration before choosing function or worker limits.
- Preserve existing batch checkpoints and stage contracts if processing moves to a separate worker.
- Add deployment configuration, secrets management, backups, monitoring, and recovery procedures.

## Expanded audit and retention

- Add a security audit log for identity changes, governed commands, decisions, publication, and logical deletion.
- Define retention and exceptional administrative purge policy.
- Define data export, subject-access, and deletion obligations when product requirements are known.
