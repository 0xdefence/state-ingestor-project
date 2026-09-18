export type ScopeKind = "current" | "selected" | "all";
export interface FileScope {
  kind: ScopeKind;
  run_ids: string[];
}
export type Verdict =
  "CLEAN" | "AUTO_REPAIRED" | "NEEDS_REVIEW" | "REJECTED" | "DUPLICATE";
export type RunState =
  | "created"
  | "ingested"
  | "parsing"
  | "parsed"
  | "normalising"
  | "normalised"
  | "classifying"
  | "classified"
  | "loading"
  | "staged";
export interface AbsoluteInstant {
  instant: string;
  display: string;
  timezone: string;
}
export interface RunView {
  id: string;
  source_file_id: string;
  state: RunState;
  state_label: string;
  stage_failure: string | null;
  created_at: AbsoluteInstant;
  processed_at: AbsoluteInstant | null;
  counts: Record<string, number>;
  filename: string | null;
  predecessor_run_id: string | null;
  reprocess_sequence: number;
  source_sha256: string;
  source_byte_size: number;
  fx_snapshot_id: string | null;
  requested_fx_snapshot_date: string | null;
  rules_version: string | null;
  build_revision: string | null;
}
export interface ReviewRow {
  id: string;
  run_id: string;
  raw_record_id: string;
  classification_id: string;
  candidate_revision_id: string;
  entity_type: string | null;
  business_identifier: string | null;
  verdict: Verdict;
  verdict_label: string;
  readiness: string;
  readiness_label: string;
  current_readiness: string;
  canonical_effect: string;
  canonical_revision_id: string | null;
  current_status: string;
  current_status_label: string;
  effective_state: string;
  effective_state_label: string;
  decision_sequence: number;
  latest_decision_id: string | null;
  source_line_start: number;
  source_line_end: number;
  reason_summaries: string[];
}
export interface WorkspaceView {
  scope: FileScope;
  runs: RunView[];
  review_counts: Record<string, number>;
  review_items: ReviewRow[];
}
export interface ReviewQueueView {
  scope: FileScope;
  items: ReviewRow[];
}
export interface UploadResult {
  source_file_id: string;
  source_occurrence_id: string;
  run_id: string;
  source_reused: boolean;
  run_reused: boolean;
  duplicate_upload: boolean;
  resume_from_checkpoint: Record<string, unknown> | null;
}
export interface ProcessResult {
  run_id: string;
  state: RunState;
  parse: { run_id: string; record_count: number };
  normalise: { run_id: string; record_count: number };
  classification_counts: Record<string, number>;
  promoted_count: number;
}
export interface RunDetail {
  run: RunView;
  checkpoints: Record<string, EvidenceValue>[];
  events: Record<string, EvidenceValue>[];
  occurrences: Record<string, EvidenceValue>[];
  records: RunRecord[];
  evidence: EvidenceView;
}

export type EvidenceValue =
  | null
  | string
  | number
  | boolean
  | EvidenceValue[]
  | { [key: string]: EvidenceValue };
export interface EvidenceNode {
  kind: string;
  id: string;
  attributes: Record<string, EvidenceValue>;
}
export interface EvidenceView {
  nodes: EvidenceNode[];
}
export interface RunRecord {
  id: string;
  kind: string;
  source_line_start: number;
  source_line_end: number;
  candidate_revision_id: string | null;
  classification_id: string | null;
  business_identifier: string | null;
  verdict: Verdict | null;
  review_item_id: string | null;
}
export type DecisionOutcome = "approve" | "reject" | "acknowledge";
export interface Decision {
  id: string;
  review_item_id: string;
  candidate_revision_id: string;
  sequence: number;
  outcome: DecisionOutcome;
  operator_name: string;
  reason: string | null;
  idempotency_key: string;
  supersedes_decision_id: string | null;
  decided_at: AbsoluteInstant;
}
export interface ReviewDetailView {
  item: ReviewRow;
  evidence: EvidenceView;
  decisions: { decision: Decision; outcome_label: string }[];
  allowed_outcomes: DecisionOutcome[];
}
export interface DecisionCommand {
  candidate_revision_id: string;
  expected_sequence: number;
  outcome: DecisionOutcome;
  operator_name: string;
  reason: string | null;
  idempotency_key: string;
  supersedes_decision_id: string | null;
}
export interface DecisionResult {
  decision: Decision;
  effective_state: string;
  replayed: boolean;
  canonical_revision: { id: string; revision_number: number } | null;
}
