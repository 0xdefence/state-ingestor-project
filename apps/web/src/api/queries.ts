import { useQuery } from "@tanstack/react-query";
import { request } from "./client";
import type {
  FileScope,
  WorkspaceView,
  RunDetail,
  ReviewQueueView,
  ReviewDetailView,
  DecisionCommand,
  DecisionResult,
} from "./contracts";
export function scopeParams(scope: FileScope) {
  const params = new URLSearchParams({ scope: scope.kind });
  if (scope.kind !== "all")
    scope.run_ids.forEach((id) => params.append("run_id", id));
  return params;
}
export function reviewHref(
  scope: FileScope,
  filters: Record<string, string> = {},
) {
  const params = scopeParams(scope);
  Object.entries(filters).forEach(([key, value]) => params.set(key, value));
  return `/reviews?${params}`;
}
export function useWorkspace(scope: FileScope, enabled = true) {
  return useQuery({
    queryKey: ["workspace", scope.kind, ...scope.run_ids],
    queryFn: ({ signal }) =>
      request<WorkspaceView>(`/workspace?${scopeParams(scope)}`, { signal }),
    enabled: enabled && (scope.kind === "all" || scope.run_ids.length > 0),
  });
}

export function useRun(runId: string) {
  return useQuery({
    queryKey: ["run", runId],
    queryFn: ({ signal }) =>
      request<RunDetail>(`/runs/${encodeURIComponent(runId)}`, { signal }),
    enabled: !!runId,
    refetchInterval: (query) =>
      query.state.data &&
      query.state.data.run.state !== "staged" &&
      !query.state.data.run.stage_failure
        ? 1500
        : false,
  });
}
export function useReviews(scope: FileScope, state = "", verdict = "") {
  const params = scopeParams(scope);
  if (state) params.set("effective_state", state);
  if (verdict) params.set("verdict", verdict);
  return useQuery({
    queryKey: ["reviews", scope.kind, ...scope.run_ids, state, verdict],
    queryFn: ({ signal }) =>
      request<ReviewQueueView>(`/reviews?${params}`, {
        signal,
      }),
    enabled: scope.kind === "all" || scope.run_ids.length > 0,
  });
}
export function useReview(id: string | null) {
  return useQuery({
    queryKey: ["review", id],
    queryFn: ({ signal }) =>
      request<ReviewDetailView>(`/reviews/${encodeURIComponent(id!)}`, {
        signal,
      }),
    enabled: !!id,
  });
}
export function saveDecision(id: string, command: DecisionCommand) {
  return request<DecisionResult>(
    `/reviews/${encodeURIComponent(id)}/decisions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command),
    },
  );
}
