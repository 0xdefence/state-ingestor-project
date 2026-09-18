import { expect, test, type Page } from "@playwright/test";
import type axe from "axe-core";
import { resolve } from "node:path";
import type {
  ReviewDetailView,
  ReviewQueueView,
  RunDetail,
} from "../src/api/contracts";

async function accessibility(page: Page) {
  await page.addScriptTag({
    path: resolve("node_modules/axe-core/axe.min.js"),
  });
  const violations = await page.evaluate(async () => {
    const result = await (window as unknown as { axe: typeof axe }).axe.run(
      document,
      {
        runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
      },
    );
    return result.violations.map(({ id, nodes }) => ({
      id,
      targets: nodes.map((n) => n.target),
    }));
  });
  expect(violations).toEqual([]);
}

// Real CSV -> recoverable failure -> retry -> review -> promotion -> reversal -> reuse.
// Missing checkpoint recovery, wrong decision effects, or duplicate writes fail this test.
test("operator completes and reverses a decision, recovering processing and reusing exact bytes", async ({
  page,
  request,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  expect(
    (
      await request.post("http://127.0.0.1:8000/__test__/fail-next-process")
    ).ok(),
  ).toBeTruthy();
  await page.goto("/");
  await accessibility(page);
  await page
    .getByLabel("CSV file")
    .setInputFiles(resolve("../../data/messy_sample_data.csv"));
  await page.getByLabel("Operator name").fill("Alex");
  await page.getByRole("button", { name: "Upload and process" }).focus();
  const upload = page.waitForResponse(
    (r) => r.url().endsWith("/api/uploads") && r.request().method() === "POST",
  );
  await page.keyboard.press("Enter");
  const uploaded = await (await upload).json();
  const runId: string = uploaded.run_id;
  expect(uploaded.run_reused).toBe(false);
  await expect(
    page.getByRole("alert").filter({ hasText: "The upload is saved" }),
  ).toBeVisible();
  const failed: RunDetail = await (
    await request.get(`/api/runs/${runId}`)
  ).json();
  expect(failed.run.state).toBe("normalising");
  expect(failed.run.stage_failure).toBe("normalise_failed");
  const partial = await (
    await request.get("http://127.0.0.1:8000/__test__/snapshot")
  ).json();
  expect(partial.raw_record).toHaveLength(52);
  // The first ten raw records include the header and nine data records.
  expect(partial.candidate_revision).toHaveLength(9);
  expect(partial.canonical_revision).toHaveLength(0);
  await accessibility(page);
  await page.getByRole("button", { name: "Retry processing" }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
  await expect(
    page.getByText("Processed", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText("1 exact submission", { exact: true }),
  ).toBeVisible();
  const processed: RunDetail = await (
    await request.get(`/api/runs/${runId}`)
  ).json();
  expect(processed.run.state).toBe("staged");
  const complete = await (
    await request.get("http://127.0.0.1:8000/__test__/snapshot")
  ).json();
  expect(complete.raw_record).toEqual(partial.raw_record);
  expect(complete.candidate_revision).toEqual(
    expect.arrayContaining(partial.candidate_revision),
  );
  expect(complete.classification_result).toHaveLength(48);
  expect(complete.canonical_revision).toHaveLength(17);
  expect(complete.canonical_current).toHaveLength(17);
  await accessibility(page);

  const queue: ReviewQueueView = await (
    await request.get(
      `/api/reviews?scope=current&run_id=${runId}&verdict=NEEDS_REVIEW`,
    )
  ).json();
  const item = queue.items.find(
    (item) => item.business_identifier === "CUST-1009",
  );
  expect(item).toBeDefined();
  await page.goto(
    `/reviews?scope=current&run_id=${runId}&verdict=NEEDS_REVIEW`,
  );
  const queueRegion = page.getByRole("region", { name: "Review queue" });
  await queueRegion.focus();
  for (
    let index = 0;
    index <= queue.items.findIndex((row) => row.id === item!.id);
    index++
  )
    await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Review CUST-1009", exact: true }),
  ).toBeFocused();
  await page
    .getByText("All source fields (10)", { exact: true })
    .press("Enter");
  expect(
    await page
      .locator(".source-fields li")
      .nth(2)
      .locator(".exact-value")
      .textContent(),
  ).toBe("李明");
  const nameField = page
    .locator(".interpreted-fields > div")
    .filter({ has: page.locator("dt", { hasText: /^Name$/ }) });
  await expect(nameField.locator(".exact-value").first()).toHaveText("李明");
  await expect(nameField).toContainText("Exact source value");
  const statusField = page
    .locator(".interpreted-fields > div")
    .filter({ has: page.locator("dt", { hasText: /^Status$/ }) });
  await expect(statusField.locator(".exact-value").first()).toHaveText(
    "active",
  );
  await expect(
    statusField.locator(".source-comparison .exact-value"),
  ).toHaveText("ACTIVE");
  await page.getByLabel("Operator name").fill("Alex");
  await page
    .getByRole("textbox", { name: "Reason", exact: true })
    .fill("Verified against frozen source");
  await page.getByRole("button", { name: "Approve", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("status").filter({ hasText: "Decision saved. Approved." }),
  ).toBeVisible();
  await expect(
    page.getByText("Revision 1 · Current", { exact: true }),
  ).toBeVisible();
  const approved: ReviewDetailView = await (
    await request.get(`/api/reviews/${item!.id}`)
  ).json();
  expect(approved.item.effective_state).toBe("approved");
  expect(approved.item.decision_sequence).toBe(1);
  const canonical = approved.evidence.nodes.find(
    (n) =>
      n.kind === "canonical_revision" &&
      n.attributes.candidate_revision_id === item!.candidate_revision_id,
  )!;
  expect(canonical).toBeDefined();
  expect(
    approved.evidence.nodes.find(
      (n) =>
        n.kind === "canonical_identity" &&
        n.id === canonical.attributes.identity_id,
    )?.attributes.current_revision_id,
  ).toBe(canonical.id);
  await accessibility(page);
  await page.screenshot({
    path: testInfo.outputPath("approved-review.png"),
    fullPage: true,
  });

  await page
    .getByRole("textbox", { name: "Reason", exact: true })
    .fill("Reversing approval after further source review");
  await page.getByRole("button", { name: "Reject", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("status").filter({ hasText: "Decision saved. Rejected." }),
  ).toBeVisible();
  await expect(
    page.getByText("Revision 1 · Historical", { exact: true }),
  ).toBeVisible();
  const reversed: ReviewDetailView = await (
    await request.get(`/api/reviews/${item!.id}`)
  ).json();
  expect(reversed.item.effective_state).toBe("rejected");
  expect(reversed.item.decision_sequence).toBe(2);
  expect(reversed.decisions).toHaveLength(2);
  expect(
    reversed.evidence.nodes.find(
      (n) =>
        n.kind === "canonical_identity" &&
        n.id === canonical.attributes.identity_id,
    )?.attributes.current_revision_id,
  ).toBeNull();
  expect(
    reversed.evidence.nodes
      .filter((n) => n.kind === "canonical_promotion_event")
      .map((n) => n.attributes.action),
  ).toEqual(expect.arrayContaining(["activate", "withdraw"]));
  await accessibility(page);

  const beforeDuplicate = await (
    await request.get("http://127.0.0.1:8000/__test__/snapshot")
  ).json();
  await page.goto("/");
  await page
    .getByLabel("CSV file")
    .setInputFiles(resolve("../../data/messy_sample_data.csv"));
  await page.getByLabel("Operator name").fill("Alex duplicate submission");
  const duplicateResponse = page.waitForResponse(
    (r) => r.url().endsWith("/api/uploads") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Upload and process" }).press("Enter");
  const duplicate = await (await duplicateResponse).json();
  expect(duplicate.run_id).toBe(runId);
  expect(duplicate.duplicate_upload).toBe(true);
  expect(duplicate.source_occurrence_id).not.toBe(
    uploaded.source_occurrence_id,
  );
  await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`));
  await expect(
    page.getByText("2 exact submissions", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/These exact file contents were submitted more than once/),
  ).toBeVisible();
  await page.getByText("2 exact submissions", { exact: true }).press("Enter");
  await expect(
    page.getByText("Alex duplicate submission", { exact: true }),
  ).toBeVisible();
  expect(
    await (await request.get("http://127.0.0.1:8000/__test__/snapshot")).json(),
  ).toEqual(beforeDuplicate);
  const final: RunDetail = await (
    await request.get(`/api/runs/${runId}`)
  ).json();
  expect(final.occurrences).toHaveLength(2);
  await accessibility(page);
  await page.screenshot({
    path: testInfo.outputPath("duplicate-run.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
