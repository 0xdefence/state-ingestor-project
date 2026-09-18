import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import success from "../../test/responses/classification-success.json";
import failure from "../../test/responses/classification-failure.json";
import { fakeWorkflow, renderWorkflow } from "../../test/workflow";

// Captured from the PostgreSQL run-detail projection regression, including
// the complete evidence graph and timestamp response shape.
afterEach(() => vi.unstubAllGlobals());

test("persisted classification success exposes its factual stage evidence", async () => {
  fakeWorkflow((url) =>
    url.startsWith("/api/runs/") ? Response.json(success) : undefined,
  );
  const user = userEvent.setup();
  await renderWorkflow(`/runs/${success.run.id}`);
  const stage = await screen.findByRole("button", { name: /^Record checks/ });
  expect(stage).toBeEnabled();
  await user.click(stage);
  const evidence = screen.getByRole("region", { name: "Stage evidence" });
  expect(evidence).toHaveTextContent("Stage Completed");
  expect(evidence).toHaveTextContent("Clean");
  expect(
    within(evidence).getAllByText(/September 2026/).length,
  ).toBeGreaterThan(0);
});

test("reloaded classification failure names the failed stage and offers retry", async () => {
  fakeWorkflow((url) =>
    url.startsWith("/api/runs/") ? Response.json(failure) : undefined,
  );
  await renderWorkflow(`/runs/${failure.run.id}`);
  expect(
    await screen.findByRole("button", { name: "Retry processing" }),
  ).toBeEnabled();
  const stage = screen.getByRole("button", { name: /^Record checks/ });
  expect(stage).toBeEnabled();
  expect(stage).toHaveAttribute("aria-pressed", "true");
  expect(
    screen.getByRole("region", { name: "Stage evidence" }),
  ).toHaveTextContent("Stage Failed");
});
