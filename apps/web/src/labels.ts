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
  approved: "Approved",
  rejected: "Rejected",
  acknowledged: "Acknowledged",
  blocked_by_dependency: "Waiting for another record",
  ready: "Ready",
  parse_failed: "CSV reading failed",
  normalise_failed: "Value interpretation failed",
  classify_failed: "Record checks failed",
  load_failed: "Loading failed",
  INVALID_INTEGER: "A number was expected",
};
export const displayLabel = (value: string): string =>
  labels[value] ??
  value
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
export type Tone = "green" | "amber" | "red" | "purple" | "grey";
export function statusTone(value: string): Tone {
  if (value.endsWith("_failed")) return "red";
  if (value === "blocked_by_dependency") return "purple";
  if (["staged", "CLEAN", "AUTO_REPAIRED", "approved"].includes(value))
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
