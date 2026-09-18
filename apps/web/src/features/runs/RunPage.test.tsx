import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, test, expect, vi } from "vitest";
import axe from "axe-core";
import {
  detail,
  fakeWorkflow,
  renderWorkflow,
  runDetail,
  apiFailure,
} from "../../test/workflow";
import { run } from "../../test/fixtures";
afterEach(() => vi.unstubAllGlobals());
test("run shows exact submission history, processing evidence and selected record lineage", async () => {
  fakeWorkflow();
  const user = userEvent.setup();
  const { container, router } = await renderWorkflow(
    `/runs/${run.id}?review=review-1`,
  );
  expect(
    await screen.findByRole("heading", { name: run.filename }),
  ).toBeVisible();
  expect(screen.getByText(run.id)).toBeVisible();
  expect(screen.getByText("2 data records")).toBeVisible();
  expect(
    screen.getAllByText("16 September 2026, 13:30 BST").length,
  ).toBeGreaterThan(0);
  await user.click(screen.getByText("2 exact submissions"));
  expect(screen.getByText("submitted-again.csv")).toBeVisible();
  expect(screen.getByText("Sam")).toBeVisible();
  await screen.findByRole("heading", { name: "Review ORD-3001" });
  expect(screen.getAllByText("many").at(-1)).toBeVisible();
  expect(
    screen.getByRole("heading", { name: "Interpreted values" }),
  ).toBeVisible();
  await user.click(
    screen.getByRole("button", { name: "Normalisation details" }),
  );
  expect(router.state.location.search).toContain("review=review-1");
  expect(
    screen.getByRole("region", { name: "Stage evidence" }),
  ).toHaveTextContent("Normalisation details");
  expect(screen.getAllByText("CUS-1001")[0]).toBeVisible();
  expect(
    screen.getByRole("heading", { name: "Canonical lineage" }),
  ).toBeVisible();
  expect(
    (
      await axe.run(container, {
        rules: { "color-contrast": { enabled: false } },
      })
    ).violations,
  ).toEqual([]);
});
test("unreached stages stay disabled and failed runs retain checkpoint progress", async () => {
  fakeWorkflow((url) =>
    url.startsWith("/api/runs/")
      ? Response.json({
          ...runDetail,
          run: {
            ...run,
            state: "parsing",
            stage_failure: "parse_failed",
            processed_at: null,
            counts: {},
          },
          events: [
            {
              stage: "parse",
              event_type: "stage_failed",
              occurred_at: run.created_at,
              facts: {},
            },
          ],
        })
      : undefined,
  );
  await renderWorkflow(`/runs/${run.id}`);
  await screen.findByRole("heading", { name: run.filename });
  expect(
    screen.getByRole("button", { name: /Normalisation details.*Not reached/ }),
  ).toBeDisabled();
  expect(screen.getByText("Not processed yet")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Retry processing" }),
  ).toBeEnabled();
  expect(
    screen.getByRole("region", { name: "Stage evidence" }),
  ).toHaveTextContent("18");
});
for (const state of ["loading", "failure", "empty"])
  test(`run ${state} is truthful and accessible`, async () => {
    fakeWorkflow((url) =>
      url.startsWith("/api/runs/")
        ? state === "loading"
          ? new Promise<Response>(() => {})
          : state === "failure"
            ? apiFailure()
            : Response.json({
                ...runDetail,
                run: { ...run, counts: {} },
                occurrences: [],
                events: [],
                checkpoints: [],
              })
        : url.startsWith("/api/reviews?")
          ? Response.json({ items: [], scope: { kind: "all", run_ids: [] } })
          : undefined,
    );
    const { container } = await renderWorkflow(`/runs/${run.id}`);
    if (state === "loading")
      expect(screen.getByRole("status")).toHaveTextContent("Loading run");
    if (state === "failure")
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Evidence unavailable",
      );
    if (state === "empty")
      expect(
        await screen.findByText("No records need review in this run."),
      ).toBeVisible();
    expect(
      (
        await axe.run(container, {
          rules: { "color-contrast": { enabled: false } },
        })
      ).violations,
    ).toEqual([]);
  });

test("clean records expose their own source evidence without unrelated review issues", async () => {
  fakeWorkflow();
  const user = userEvent.setup();
  const { router } = await renderWorkflow(`/runs/${run.id}`);
  await user.click(
    await screen.findByRole("button", { name: "Inspect line 10" }),
  );
  expect(
    screen.getByRole("heading", { name: "Record SKU-2001" }),
  ).toBeVisible();
  expect(router.state.location.search).toContain("record=raw-clean");
  expect(
    screen.queryByRole("button", { name: "Approve" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText("No registered issues for this record."),
  ).toBeVisible();
  await user.click(screen.getByText("All source fields (3)"));
  expect(screen.getByText("Clean source")).toBeVisible();
  expect(screen.queryByText("many")).not.toBeInTheDocument();
});

test("normalisation evidence explains the expected domain and keeps the issue code in technical detail", async () => {
  fakeWorkflow();
  const user = userEvent.setup();
  await renderWorkflow(`/runs/${run.id}?review=review-1`);
  await screen.findByRole("heading", { name: "Review ORD-3001" });
  expect(screen.getByText("Expected value: A whole number")).toBeVisible();
  await user.click(screen.getByText("Technical evidence"));
  await user.click(screen.getByText(/Issue ·/));
  expect(screen.getByText(/"INVALID_INTEGER"/)).toBeVisible();
});

test("canonical revision timestamp comes from the persisted staging instant", async () => {
  fakeWorkflow();
  await renderWorkflow(`/runs/${run.id}?review=review-1`);
  const revision = await screen.findByText("Revision 1 · Current");
  expect(revision.parentElement?.querySelector("time")).toHaveAttribute(
    "datetime",
    "2026-09-16T12:30:00Z",
  );
});

test("a cross-run review deep link never enables a decision in the wrong run context", async () => {
  fakeWorkflow((url) =>
    url === "/api/reviews/review-1"
      ? Response.json({
          ...detail,
          item: { ...detail.item, run_id: "other-run" },
        })
      : undefined,
  );
  await renderWorkflow(`/runs/${run.id}?review=review-1`);
  expect(
    await screen.findByText(
      "This review belongs to another file. Open its source run to inspect and decide.",
    ),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Approve" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Open the matching run" }),
  ).toHaveAttribute("href", "/runs/other-run?review=review-1");
});
test("switching between a clean record and a review keeps exactly one selected row", async () => {
  fakeWorkflow();
  const user = userEvent.setup();
  const { router } = await renderWorkflow(`/runs/${run.id}`);
  await user.click(
    await screen.findByRole("button", { name: "Inspect line 10" }),
  );
  await user.click(screen.getByRole("button", { name: "Inspect line 9" }));
  expect(router.state.location.search).not.toContain("record=");
  expect(
    screen.getByRole("button", { name: "Inspect line 10" }),
  ).toHaveAttribute("aria-pressed", "false");
  expect(
    screen.getByRole("button", { name: "Inspect line 9" }),
  ).toHaveAttribute("aria-pressed", "true");
});
test("duplicate submissions of an incomplete run do not claim processing was completed", async () => {
  fakeWorkflow((url) =>
    url.startsWith("/api/runs/")
      ? Response.json({
          ...runDetail,
          run: { ...run, state: "ingested", processed_at: null, counts: {} },
        })
      : undefined,
  );
  await renderWorkflow(`/runs/${run.id}`);
  expect(
    await screen.findByText(
      "These exact file contents were submitted more than once. The existing run was reused.",
    ),
  ).toBeVisible();
  expect(
    screen.queryByText(/Completed processing was reused/),
  ).not.toBeInTheDocument();
});
