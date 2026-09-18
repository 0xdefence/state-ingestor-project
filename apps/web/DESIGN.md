# Operator workspace design

Geist Sans supplies headings, body text, controls, and tabular counts; Geist Mono is reserved for identifiers. Font files are bundled locally. White functional panels sit on #FAFAFA with #E5E5E5 borders; #F5F5F5 fields, #171717 text, and #666666 metadata. Panels use 14px radii, nested controls 8px. Semantic dots always include visible labels. Focus uses #0066CC.

The workspace places upload first, explicit scope next, then a wide attention list alongside a narrow outcome ledger. Recent individual runs occupy a compact semantic table below. Mobile stacks upload and attention sections; the labelled table scrolls horizontally to preserve filename, status, outcome and timestamp evidence. No charts, relative dates, gradients, or decorative dashboard cards.

The selected-file control uses a native disclosure with searchable checkboxes. Selected count and removable filenames remain outside the disclosure. Outcomes are links with inherited scope and verdict. Classification snapshots are distinguished from effective review state.

All visible workspace panels consume one scoped WorkspaceView, including its review rows and counts, from a single REPEATABLE READ snapshot. The all-run catalog only supplies file choices. Current file requires explicit run context; otherwise the page offers a no-current-file state and a Choose a file action.

Review rows show a factual business identifier from known candidate evidence, or an explicit unavailable label. Recent-run Processed times come from persisted load-completed events. Unfinished runs say Not processed yet; a completed historical run without a completion event says Completion time unavailable. Creation time is never relabelled as upload or completion time. Run and review destinations remain Task 5 boundaries.
