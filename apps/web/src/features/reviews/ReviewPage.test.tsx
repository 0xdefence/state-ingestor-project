import { conflictDetail, statusDetail } from "../../test/workflowFixtures";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, test, expect, vi } from "vitest";
import axe from "axe-core";
import {
  fakeWorkflow,
  renderWorkflow,
  detail,
  item,
  apiFailure,
} from "../../test/workflow";
import { run } from "../../test/fixtures";
afterEach(() => vi.unstubAllGlobals());
test("queue keyboard selection focuses detail and keeps URL filter context", async () => {
  fakeWorkflow();
  const user = userEvent.setup();
  const { container, router } = await renderWorkflow(
    `/reviews?scope=current&run_id=${run.id}&state=pending&verdict=NEEDS_REVIEW`,
  );
  const queue = await screen.findByRole("region", { name: "Review queue" });
  queue.focus();
  await user.keyboard("{ArrowDown}{Enter}");
  expect(
    await screen.findByRole("heading", { name: "Review ORD-3001" }),
  ).toHaveFocus();
  expect(router.state.location.search).toContain("review=review-1");
  expect(router.state.location.search).toContain("state=pending");
  expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
  expect(
    (
      await axe.run(container, {
        rules: { "color-contrast": { enabled: false } },
      })
    ).violations,
  ).toEqual([]);
});
for (const verdict of ["REJECTED", "DUPLICATE"])
  test(`${verdict} only exposes acknowledgement and rejection`, async () => {
    fakeWorkflow((url) =>
      url === "/api/reviews/review-1"
        ? Response.json({
            ...detail,
            item: { ...item, verdict },
            allowed_outcomes: ["acknowledge", "reject"],
          })
        : undefined,
    );
    await renderWorkflow("/reviews?review=review-1");
    expect(
      await screen.findByRole("button", { name: "Acknowledge" }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument();
  });
test("blocked dependencies never offer approval", async () => {
  fakeWorkflow((url) =>
    url === "/api/reviews/review-1"
      ? Response.json({
          ...detail,
          item: {
            ...item,
            readiness: "blocked_by_dependency",
            current_readiness: "blocked_by_dependency",
            current_status: "blocked_by_dependency",
          },
          allowed_outcomes: ["approve", "reject"],
        })
      : undefined,
  );
  await renderWorkflow("/reviews?review=review-1");
  expect(await screen.findByText("Waiting for another record")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Approve" }),
  ).not.toBeInTheDocument();
});
test("decision validation is semantic and 409 preserves input while refreshing exact evidence", async () => {
  let stale = false;
  let submitted: Record<string, unknown> | undefined;
  fakeWorkflow((url, init) => {
    if (url.endsWith("/decisions")) {
      submitted = JSON.parse(String(init?.body));
      stale = true;
      return apiFailure(409);
    }
    if (url === "/api/reviews/review-1" && stale)
      return Response.json({
        ...detail,
        item: {
          ...item,
          candidate_revision_id: "candidate-2",
          decision_sequence: 1,
          latest_decision_id: "decision-1",
        },
      });
  });
  const user = userEvent.setup();
  const { container } = await renderWorkflow("/reviews?review=review-1");
  await user.click(await screen.findByRole("button", { name: "Reject" }));
  expect(screen.getByLabelText("Operator name")).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  expect(screen.getByLabelText("Reason")).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  await user.type(screen.getByLabelText("Operator name"), "Alex");
  await user.type(screen.getByLabelText("Reason"), "Checked the source");
  await user.click(screen.getByRole("button", { name: "Reject" }));
  expect(
    await screen.findByText(
      "This record changed before your decision was saved. Review the refreshed evidence and try again.",
    ),
  ).toBeVisible();
  expect(screen.getByLabelText("Operator name")).toHaveValue("Alex");
  expect(screen.getByLabelText("Reason")).toHaveValue("Checked the source");
  expect(submitted).toMatchObject({
    candidate_revision_id: "candidate-1",
    expected_sequence: 0,
    outcome: "reject",
    operator_name: "Alex",
    reason: "Checked the source",
    supersedes_decision_id: null,
  });
  expect(typeof submitted?.idempotency_key).toBe("string");
  await waitFor(() =>
    expect(screen.getByText(/Decision sequence: 1/)).toBeInTheDocument(),
  );
  const firstKey = submitted?.idempotency_key;
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled(),
  );
  await user.click(screen.getByRole("button", { name: "Reject" }));
  await waitFor(() =>
    expect(submitted).toMatchObject({
      candidate_revision_id: "candidate-2",
      expected_sequence: 1,
      supersedes_decision_id: "decision-1",
    }),
  );
  expect(submitted?.idempotency_key).not.toBe(firstKey);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled(),
  );

  expect(
    (
      await axe.run(container, {
        rules: { "color-contrast": { enabled: false } },
      })
    ).violations,
  ).toEqual([]);
});
test("successful save keeps selection, shows effective outcome and invalidates related projections", async () => {
  let saved = false;
  let finish!: (value: Response) => void;
  fakeWorkflow((url, init) => {
    if (url.endsWith("/decisions"))
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    if (url === "/api/reviews/review-1" && saved)
      return Response.json({
        ...detail,
        item: {
          ...item,
          effective_state: "approved",
          decision_sequence: 1,
          latest_decision_id: "decision-1",
        },
        allowed_outcomes: ["reject"],
        decisions: [
          {
            outcome_label: "Approve",
            decision: {
              id: "decision-1",
              sequence: 1,
              outcome: "approve",
              operator_name: "Alex",
              reason: null,
              decided_at: run.processed_at,
              supersedes_decision_id: null,
            },
          },
        ],
      });
  });
  const user = userEvent.setup();
  const { client, router } = await renderWorkflow(
    "/reviews?state=pending&review=review-1",
  );
  client.setQueryData(["run", "other"], {});
  client.setQueryData(["workspace", "selected"], {});
  await user.type(await screen.findByLabelText("Operator name"), "Alex");
  await user.click(screen.getByRole("button", { name: "Approve" }));
  expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  expect(screen.getByLabelText("Operator name")).toBeDisabled();
  saved = true;
  finish(
    Response.json({
      effective_state: "approved",
      canonical_revision: { id: "canonical-2", revision_number: 2 },
      decision: { id: "decision-1" },
    }),
  );
  expect(await screen.findByText(/Decision saved/)).toBeVisible();
  expect(router.state.location.search).toContain("review=review-1");
  await waitFor(() =>
    expect(screen.getByText(/Decision sequence: 1/)).toBeVisible(),
  );
  expect(client.getQueryState(["run", "other"])?.isInvalidated).toBe(true);
  expect(client.getQueryState(["workspace", "selected"])?.isInvalidated).toBe(
    true,
  );
  expect(
    screen.getByRole("heading", { name: "Decision history" }),
  ).toBeVisible();
});
for (const state of ["loading", "failure", "empty"])
  test(`review ${state} is truthful and accessible`, async () => {
    fakeWorkflow((url) =>
      url.startsWith("/api/reviews?")
        ? state === "loading"
          ? new Promise<Response>(() => {})
          : state === "failure"
            ? apiFailure()
            : Response.json({ items: [], scope: { kind: "all", run_ids: [] } })
        : undefined,
    );
    const { container } = await renderWorkflow("/reviews?state=pending");
    if (state === "loading")
      expect(screen.getByRole("status")).toHaveTextContent(
        "Loading review queue",
      );
    if (state === "failure")
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Evidence unavailable",
      );
    if (state === "empty")
      expect(
        await screen.findByText("No records match these filters."),
      ).toBeVisible();
    expect(
      (
        await axe.run(container, {
          rules: { "color-contrast": { enabled: false } },
        })
      ).violations,
    ).toEqual([]);
  });

test("URL filters reach the API and a changed filter preserves the selected record", async () => {
  const requests: string[] = [];
  fakeWorkflow((url) => {
    requests.push(url);
    return undefined;
  });
  const user = userEvent.setup();
  const { router } = await renderWorkflow(
    `/reviews?scope=selected&run_id=${run.id}&state=pending&verdict=NEEDS_REVIEW&review=review-1`,
  );
  await screen.findByRole("heading", { name: "Review ORD-3001" });
  expect(requests).toContain(
    `/api/reviews?scope=selected&run_id=${run.id}&effective_state=pending&verdict=NEEDS_REVIEW`,
  );
  await user.selectOptions(screen.getByLabelText("Review state"), "approved");
  expect(router.state.location.search).toContain("review=review-1");
  await waitFor(() =>
    expect(requests).toContain(
      `/api/reviews?scope=selected&run_id=${run.id}&effective_state=approved&verdict=NEEDS_REVIEW`,
    ),
  );
});
test("an uncertain save retries the identical command key and preserves input", async () => {
  const commands: Record<string, unknown>[] = [];
  fakeWorkflow((url, init) => {
    if (url.endsWith("/decisions")) {
      commands.push(JSON.parse(String(init?.body)));
      return apiFailure();
    }
  });
  const user = userEvent.setup();
  await renderWorkflow("/reviews?review=review-1");
  await user.type(await screen.findByLabelText("Operator name"), "Alex");
  await user.type(screen.getByLabelText("Reason"), "Verified");
  await user.click(screen.getByRole("button", { name: "Approve" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Your input is preserved",
  );
  expect(screen.getByLabelText("Reason")).toHaveValue("Verified");
  await user.click(screen.getByRole("button", { name: "Approve" }));
  await waitFor(() => expect(commands).toHaveLength(2));
  expect(commands[1]).toEqual(commands[0]);
});
test("failed stale refresh keeps evidence and input but pauses decisions until retry succeeds", async () => {
  let refreshFails = false;
  fakeWorkflow((url) => {
    if (url.endsWith("/decisions")) {
      refreshFails = true;
      return apiFailure(409);
    }
    if (url === "/api/reviews/review-1" && refreshFails) return apiFailure();
  });
  const user = userEvent.setup();
  await renderWorkflow("/reviews?review=review-1");
  await user.type(await screen.findByLabelText("Operator name"), "Alex");
  await user.click(screen.getByRole("button", { name: "Approve" }));
  await screen.findByRole("button", { name: "Retry refreshing evidence" });
  expect(screen.getByLabelText("Operator name")).toHaveValue("Alex");
  expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  refreshFails = false;
  await user.click(
    screen.getByRole("button", { name: "Retry refreshing evidence" }),
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled(),
  );
});
test("reversal submits the displayed latest decision and sequence", async () => {
  let submitted: Record<string, unknown> | undefined;
  fakeWorkflow((url, init) => {
    if (url.endsWith("/decisions")) {
      submitted = JSON.parse(String(init?.body));
      return apiFailure();
    }
    if (url === "/api/reviews/review-1")
      return Response.json({
        ...detail,
        item: {
          ...item,
          effective_state: "approved",
          decision_sequence: 3,
          latest_decision_id: "decision-3",
        },
        allowed_outcomes: ["reject"],
      });
  });
  const user = userEvent.setup();
  await renderWorkflow("/reviews?review=review-1");
  await user.type(await screen.findByLabelText("Operator name"), "Alex");
  await user.type(screen.getByLabelText("Reason"), "Reversed after checking");
  await user.click(screen.getByRole("button", { name: "Reject" }));
  await waitFor(() =>
    expect(submitted).toMatchObject({
      supersedes_decision_id: "decision-3",
      expected_sequence: 3,
      outcome: "reject",
    }),
  );
});

test("current promotion status replaces historical dependency blocking", async () => {
  const promoted = {
    ...item,
    verdict: "CLEAN",
    readiness: "blocked_by_dependency",
    current_readiness: "ready",
    current_status: "promoted",
    canonical_effect: "current",
    canonical_revision_id: "order-canonical",
  };
  fakeWorkflow((url) =>
    url === "/api/reviews/review-1"
      ? Response.json({ ...detail, item: promoted, allowed_outcomes: [] })
      : url.startsWith("/api/reviews?")
        ? Response.json({
            scope: { kind: "all", run_ids: [] },
            items: [promoted],
          })
        : undefined,
  );
  await renderWorkflow("/reviews?review=review-1");
  expect(
    (await screen.findAllByText("Promoted to canonical data")).length,
  ).toBeGreaterThan(0);
  expect(
    screen.queryByText("Waiting for another record"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Approve" }),
  ).not.toBeInTheDocument();
});
test("reviewable ineligible classification shows current dependency blocking", async () => {
  const blocked = {
    ...item,
    readiness: "ineligible",
    current_readiness: "blocked_by_dependency",
    current_status: "blocked_by_dependency",
  };
  fakeWorkflow((url) =>
    url === "/api/reviews/review-1"
      ? Response.json({
          ...detail,
          item: blocked,
          allowed_outcomes: ["reject"],
        })
      : undefined,
  );
  await renderWorkflow("/reviews?review=review-1");
  expect(await screen.findByText("Waiting for another record")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Approve" }),
  ).not.toBeInTheDocument();
});
test("malformed shareable filters recover to valid state with an explanation", async () => {
  const requests: string[] = [];
  fakeWorkflow((url) => {
    requests.push(url);
    return undefined;
  });
  const { router } = await renderWorkflow(
    `/reviews?scope=current&run_id=${run.id}&run_id=22222222-2222-4222-8222-222222222222&state=bogus&verdict=WRONG&review=review-1`,
  );
  await screen.findByRole("heading", { name: "Review ORD-3001" });
  expect(
    screen.getByText("Some URL filters were invalid and have been adjusted."),
  ).toBeVisible();
  expect(new URLSearchParams(router.state.location.search).get("scope")).toBe(
    "selected",
  );
  expect(
    new URLSearchParams(router.state.location.search).getAll("run_id"),
  ).toEqual([run.id, "22222222-2222-4222-8222-222222222222"]);
  expect(router.state.location.search).not.toContain("bogus");
  expect(router.state.location.search).not.toContain("WRONG");
  expect(
    requests
      .filter((r) => r.startsWith("/api/reviews?"))
      .every(
        (r) =>
          !r.includes("scope=current") &&
          !r.includes("bogus") &&
          !r.includes("WRONG"),
      ),
  ).toBe(true);
});

test("conflict evidence compares usable current and prior values to the proposed product", async () => {
  fakeWorkflow((url) =>
    url === "/api/reviews/review-1" ? Response.json(conflictDetail) : undefined,
  );
  await renderWorkflow("/reviews?review=review-1");
  const current = await screen.findByRole("region", {
    name: "Current canonical values for SKU-2004",
  });
  expect(current).toHaveTextContent("Old Widget");
  expect(current).toHaveTextContent("New Widget");
  expect(current).toHaveTextContent("5");
  expect(current).toHaveTextContent("12");
  expect(current).toHaveTextContent("Proposed value");
  expect(
    screen.getByRole("region", { name: "Prior canonical values for SKU-2004" }),
  ).toHaveTextContent("Original Widget");
  expect(screen.getByText("Linked record: CUST-1001")).toBeVisible();
});
test("invalid status explains server-provided expected domain with exact source and field state", async () => {
  fakeWorkflow((url) =>
    url === "/api/reviews/review-1" ? Response.json(statusDetail) : undefined,
  );
  await renderWorkflow("/reviews?review=review-1");
  expect(
    await screen.findByText(
      "Expected value: One of: in_stock, discontinued, pending_review, backordered",
    ),
  ).toBeVisible();
  expect(screen.getAllByText("out_of_stock").at(-1)).toBeVisible();
  expect(screen.getAllByText("Unresolved")[0]).toBeVisible();
  expect(screen.getAllByText("Unsupported status").length).toBeGreaterThan(0);
});

test("unknown scope falls back to All files while retaining valid filter and selection", async () => {
  const requests: string[] = [];
  fakeWorkflow((url) => {
    requests.push(url);
    return undefined;
  });
  const { router } = await renderWorkflow(
    "/reviews?scope=mystery&state=pending&review=review-1",
  );
  await screen.findByRole("heading", { name: "Review ORD-3001" });
  expect(
    screen.getByText("Some URL filters were invalid and have been adjusted."),
  ).toBeVisible();
  expect(screen.getByLabelText("File scope")).toHaveValue("all");
  expect(screen.getByLabelText("Review state")).toHaveValue("pending");
  expect(router.state.location.search).toContain("review=review-1");
  expect(requests).toContain("/api/reviews?scope=all&effective_state=pending");
});
test("a valid shareable state does not claim its filters were invalid", async () => {
  fakeWorkflow();
  await renderWorkflow(
    `/reviews?scope=current&run_id=${run.id}&state=pending&review=review-1`,
  );
  await screen.findByRole("heading", { name: "Review ORD-3001" });
  expect(
    screen.queryByText("Some URL filters were invalid and have been adjusted."),
  ).not.toBeInTheDocument();
});
