import type { RunState, ScopeKind, Verdict } from "./api/contracts";
const scopes: Record<ScopeKind, string> = {
  current: "Current file",
  selected: "Selected files",
  all: "All files",
};
const verdicts: Record<Verdict, string> = {
  CLEAN: "Clean",
  AUTO_REPAIRED: "Repaired automatically",
  NEEDS_REVIEW: "Needs attention",
  REJECTED: "Rejected",
  DUPLICATE: "Duplicate",
};
const states: Record<RunState, string> = {
  created: "Created",
  ingested: "Uploaded",
  parsing: "Reading CSV",
  parsed: "CSV read",
  normalising: "Interpreting values",
  normalised: "Values interpreted",
  classifying: "Checking records",
  classified: "Records checked",
  loading: "Loading records",
  staged: "Processed",
};
const labels: Record<string, string> = {
  ...scopes,
  ...verdicts,
  ...states,
  pending: "Awaiting review",
  promoted: "Promoted to canonical data",
  approved: "Approved",
  rejected: "Rejected",
  acknowledged: "Acknowledged",
  blocked_by_dependency: "Waiting for another record",
  ready: "Ready",
  parse_failed: "CSV reading failed",
  normalise_failed: "Value interpretation failed",
  classify_failed: "Record checks failed",
  load_failed: "Loading failed",
  sku: "Product identifier",
  customer_id: "Customer identifier",
  order_id: "Order identifier",
  stock_qty: "Stock quantity",
  approve: "Approve",
  reject: "Reject",
  acknowledge: "Acknowledge",
  parse: "CSV reading",
  normalise: "Normalisation details",
  classify: "Record checks",
  load: "Canonical loading",
  known: "Known",
  absent: "Not supplied",
  deferred: "Deferred",
  unresolved: "Unresolved",
  eligible: "Ready",
  ineligible: "Not eligible",
  activate: "Activated",
  withdraw: "Withdrawn",
  record_ordinal: "Records saved",
  batch_number: "Batch",
  updated_at: "Checkpoint saved",
  raw_record: "Source record",
  candidate_revision: "Candidate revision",
  data_quality_issue: "Issue",
  transformation_event: "Transformation",
  classification_result: "Classification",
  dependency_record: "Dependency",
  canonical_revision: "Canonical revision",
  canonical_identity: "Canonical identity",
  canonical_promotion_event: "Promotion event",
  INVALID_INTEGER: "A number was expected",
};
export const displayLabel = (value: string): string =>
  labels[value] ??
  value
    .toLowerCase()
    .replace(/[_.-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
export type Tone = "green" | "amber" | "red" | "purple" | "grey";
export function statusTone(value: string): Tone {
  if (value.endsWith("_failed")) return "red";
  if (value === "blocked_by_dependency") return "purple";
  if (
    ["staged", "CLEAN", "AUTO_REPAIRED", "approved", "promoted"].includes(value)
  )
    return "green";
  if (
    [
      "NEEDS_REVIEW",
      "pending",
      "parsing",
      "normalising",
      "classifying",
      "loading",
    ].includes(value)
  )
    return "amber";
  return "grey";
}
export const verdictOrder: Verdict[] = [
  "CLEAN",
  "AUTO_REPAIRED",
  "NEEDS_REVIEW",
  "REJECTED",
  "DUPLICATE",
];
