import { render, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { router } from "../app/router";
import { workspace } from "./fixtures";
import { vi } from "vitest";
import { detail, item, runDetail } from "./workflowFixtures";
export { detail, item, runDetail } from "./workflowFixtures";
export function fakeWorkflow(
  overrides?: (
    url: string,
    init?: RequestInit,
  ) => Response | Promise<Response> | undefined,
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      const override = overrides?.(url, init);
      if (override) return override;
      if (url.startsWith("/api/workspace")) return Response.json(workspace);
      if (url.startsWith("/api/runs/")) return Response.json(runDetail);
      if (url.startsWith("/api/reviews?"))
        return Response.json({
          scope: { kind: "all", run_ids: [] },
          items: [item],
        });
      if (url === "/api/reviews/review-1") return Response.json(detail);
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
}
export async function renderWorkflow(url: string) {
  await act(() => router.navigate(url));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    ...render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    ),
    client,
    router,
  };
}
export const apiFailure = (status = 500) =>
  new Response(
    JSON.stringify({
      error: { code: "failure", message: "Evidence unavailable", details: {} },
    }),
    { status },
  );
