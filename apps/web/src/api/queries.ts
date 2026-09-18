import { useQuery } from "@tanstack/react-query";
import { request } from "./client";
import type { FileScope, ReviewQueueView, WorkspaceView } from "./contracts";
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
export function useWorkspace(scope: FileScope) {
  return useQuery({
    queryKey: ["workspace", scope.kind, ...scope.run_ids],
    queryFn: ({ signal }) =>
      request<WorkspaceView>(`/workspace?${scopeParams(scope)}`, { signal }),
    enabled: scope.kind === "all" || scope.run_ids.length > 0,
  });
}
export function useReviews(scope: FileScope) {
  return useQuery({
    queryKey: ["reviews", scope.kind, ...scope.run_ids],
    queryFn: ({ signal }) =>
      request<ReviewQueueView>(`/reviews?${scopeParams(scope)}`, { signal }),
    enabled: scope.kind === "all" || scope.run_ids.length > 0,
  });
}
