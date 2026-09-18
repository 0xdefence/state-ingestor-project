import { screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, afterEach, test, expect, vi } from "vitest";
import axe from "axe-core";
import { renderWorkspace } from "../../test/render";
import {
  run,
  secondRun,
  review,
  workspace,
  uploaded,
} from "../../test/fixtures";
let requests: { url: string; init?: RequestInit }[];
let uploadResponse: object;
let failUpload = false;
let failProcess = false;
let failRead = false;
let empty = false;
let pending = false;
beforeEach(() => {
  requests = [];
  uploadResponse = uploaded;
  failUpload = false;
  failProcess = false;
  failRead = false;
  empty = false;
  pending = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      const error = (message: string) =>
        new Response(
          JSON.stringify({
            error: { code: "internal_error", message, details: {} },
          }),
          { status: 500 },
        );
      if (url === "/api/uploads")
        return failUpload
          ? error("File exceeds the configured upload limit")
          : Response.json(uploadResponse);
      if (url.endsWith("/process"))
        return failProcess
          ? error("An unexpected error occurred")
          : Response.json({ run_id: run.id, state: "staged" });
      if (pending) return new Promise<Response>(() => {});
      if (failRead) return error("Requested record was not found");
      const query = new URL(url, "http://local").searchParams;
      const runs = empty
        ? []
        : query.get("scope") === "all"
          ? workspace.runs
          : workspace.runs.filter((r) => query.getAll("run_id").includes(r.id));
      if (url.startsWith("/api/workspace"))
        return Response.json({ ...workspace, runs });
      if (url.startsWith("/api/reviews"))
        return Response.json({
          scope: workspace.scope,
          items: empty ? [] : runs.some((r) => r.id === run.id) ? [review] : [],
        });
      return Response.json({
        run,
        events: [],
        checkpoints: [],
        occurrences: [],
      });
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());
async function fillUpload() {
  const user = userEvent.setup();
  await user.upload(
    screen.getByLabelText("CSV file"),
    new File(["CUSTOMER,1"], "sample.csv", { type: "text/csv" }),
  );
  await user.type(screen.getByLabelText("Operator name"), " Alex ");
  return user;
}
test("prioritises upload, outstanding work, failures, and recent runs", async () => {
  renderWorkspace();
  expect(
    await screen.findByRole("heading", { name: "Workspace" }),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Upload and process" }),
  ).toBeEnabled();
  expect(
    screen.getByRole("heading", { name: "Outstanding review" }),
  ).toBeVisible();
  expect(screen.getByRole("heading", { name: "Recent runs" })).toBeVisible();
  expect(
    await screen.findByText("A number was expected for stock quantity"),
  ).toBeVisible();
  expect(screen.getAllByText("Loading failed").length).toBeGreaterThan(0);
  expect(
    screen.getByRole("link", { name: "Review 1 outstanding record" }),
  ).toHaveAttribute("href", "/reviews?scope=all&effective_state=pending");
});
test("validates one CSV and a nonempty operator before sending", async () => {
  renderWorkspace();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Upload and process" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Choose one CSV file");
  fireEvent.change(screen.getByLabelText("CSV file"), {
    target: { files: [new File(["bad"], "bad.txt")] },
  });
  await user.type(screen.getByLabelText("Operator name"), "Alex");
  await user.click(screen.getByRole("button", { name: "Upload and process" }));
  expect(screen.getByRole("alert")).toHaveTextContent(".csv");
  fireEvent.change(screen.getByLabelText("CSV file"), {
    target: { files: [new File(["a"], "a.csv"), new File(["b"], "b.csv")] },
  });
  await user.click(screen.getByRole("button", { name: "Upload and process" }));
  expect(screen.getByRole("alert")).toHaveTextContent("one CSV");
  await user.upload(
    screen.getByLabelText("CSV file"),
    new File(["a"], "a.csv"),
  );
  await user.clear(screen.getByLabelText("Operator name"));
  await user.type(screen.getByLabelText("Operator name"), "  ");
  await user.click(screen.getByRole("button", { name: "Upload and process" }));
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Enter your operator name",
  );
  expect(requests.filter((r) => r.init?.method === "POST")).toHaveLength(0);
});
test("uploads multipart, processes, and navigates to the returned run", async () => {
  renderWorkspace();
  const user = await fillUpload();
  await user.click(screen.getByRole("button", { name: "Upload and process" }));
  expect(
    await screen.findByRole("heading", { name: "Run destination" }),
  ).toBeVisible();
  const form = requests.find((r) => r.url === "/api/uploads")?.init
    ?.body as FormData;
  expect(form.get("operator_name")).toBe("Alex");
  expect(form.get("idempotency_key")).toEqual(expect.any(String));
  expect((form.get("file") as File).name).toBe("sample.csv");
  expect(
    requests.some(
      (r) =>
        r.url === `/api/runs/${run.id}/process` && r.init?.method === "POST",
    ),
  ).toBe(true);
  expect(screen.getByText(`/runs/${run.id}`)).toBeVisible();
});
test("preserves an upload attempt key on retry and reports exact upload error", async () => {
  failUpload = true;
  renderWorkspace();
  const user = await fillUpload();
  await user.click(screen.getByRole("button", { name: "Upload and process" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "File exceeds the configured upload limit",
  );
  failUpload = false;
  await user.click(screen.getByRole("button", { name: "Upload and process" }));
  await screen.findByRole("heading", { name: "Run destination" });
  const forms = requests
    .filter((r) => r.url === "/api/uploads")
    .map((r) => r.init?.body as FormData);
  expect(forms[0].get("idempotency_key")).toBe(forms[1].get("idempotency_key"));
});
test("retries processing without uploading a second occurrence", async () => {
  failProcess = true;
  renderWorkspace();
  const user = await fillUpload();
  await user.click(screen.getByRole("button", { name: "Upload and process" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "An unexpected error occurred",
  );
  expect(
    screen.getByRole("link", { name: "Open uploaded run" }),
  ).toHaveAttribute("href", `/runs/${run.id}`);
  failProcess = false;
  await user.click(screen.getByRole("button", { name: "Retry processing" }));
  await screen.findByRole("heading", { name: "Run destination" });
  expect(requests.filter((r) => r.url === "/api/uploads")).toHaveLength(1);
});
test("identifies duplicate reuse after navigation", async () => {
  uploadResponse = {
    ...uploaded,
    duplicate_upload: true,
    source_reused: true,
    run_reused: true,
  };
  renderWorkspace();
  const user = await fillUpload();
  await user.click(screen.getByRole("button", { name: "Upload and process" }));
  expect(
    await screen.findByText(
      new RegExp(`exact file was already uploaded.*${run.id}`),
    ),
  ).toBeVisible();
});
test("selected scope is searchable, removable, keyboard accessible and inherited by outcome links", async () => {
  renderWorkspace();
  const user = userEvent.setup();
  await screen.findByText("A number was expected for stock quantity");
  await user.selectOptions(screen.getByLabelText("File scope"), "selected");
  expect(screen.getByText("0 selected files")).toBeVisible();
  await user.click(screen.getByText("Choose files"));
  await user.type(screen.getByLabelText("Search files"), "customers");
  const choice = screen.getByRole("checkbox", {
    name: "customers-september.csv",
  });
  choice.focus();
  await user.keyboard(" ");
  expect(await screen.findByText("1 selected file")).toBeVisible();
  expect(
    screen.getByRole("link", { name: "3 Needs attention" }),
  ).toHaveAttribute(
    "href",
    `/reviews?scope=selected&run_id=${run.id}&verdict=NEEDS_REVIEW`,
  );
  await user.click(
    screen.getByRole("button", { name: "Remove customers-september.csv" }),
  );
  expect(screen.getByText("0 selected files")).toBeVisible();
  expect(
    screen.queryByText("A number was expected for stock quantity"),
  ).not.toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("File scope"), "all");
  expect(screen.queryByLabelText("Search files")).not.toBeInTheDocument();
});
test("current file inherits its run id and does not show the multi-select", async () => {
  renderWorkspace(`/?scope=current&run_id=${secondRun.id}`);
  await screen.findByText("No outstanding review in this scope.");
  expect(screen.queryByLabelText("Search files")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "2 Clean" })).toHaveAttribute(
    "href",
    `/reviews?scope=current&run_id=${secondRun.id}&verdict=CLEAN`,
  );
});
test("renders long filenames, labelled status and absolute times", async () => {
  const original = run.filename;
  run.filename =
    "customer-import-with-a-very-long-filename-".repeat(5) + ".csv";
  renderWorkspace();
  const table = await screen.findByRole("table");
  await waitFor(() =>
    expect(within(table).getAllByText(run.filename).length).toBeGreaterThan(0),
  );
  expect(within(table).getByText("Processed")).toBeVisible();
  expect(
    within(table).getAllByText("16 September 2026, 13:00 BST"),
  ).toHaveLength(2);
  expect(table.textContent).not.toMatch(/today|yesterday|just now|ago/i);
  run.filename = original;
});
for (const state of ["default", "loading", "empty", "failure"] as const)
  test(`${state} fixture has accessible landmarks, controls, and feedback`, async () => {
    pending = state === "loading";
    empty = state === "empty";
    failRead = state === "failure";
    const { container } = renderWorkspace();
    if (state === "default")
      await screen.findByText("A number was expected for stock quantity");
    if (state === "empty") await screen.findByText("No files uploaded yet.");
    if (state === "failure") await screen.findByRole("alert");
    if (state === "loading")
      expect(screen.getByRole("status")).toHaveTextContent("Loading workspace");
    const result = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });

test("disables repeat submission while uploading", async () => {
  const originalFetch = globalThis.fetch;
  let finish!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | URL | Request, init?: RequestInit) =>
      String(input) === "/api/uploads"
        ? new Promise<Response>((resolve) => {
            finish = resolve;
          })
        : originalFetch(input, init),
    ),
  );
  renderWorkspace();
  const user = await fillUpload();
  await user.click(screen.getByRole("button", { name: "Upload and process" }));
  expect(screen.getByRole("button", { name: "Uploading…" })).toBeDisabled();
  expect(screen.getByLabelText("Operator name")).toBeDisabled();
  finish(Response.json(uploaded));
  await screen.findByRole("heading", { name: "Run destination" });
});
test("loading and failure never claim the workspace has no uploaded files", async () => {
  pending = true;
  renderWorkspace();
  expect(screen.queryByText("No files uploaded yet.")).not.toBeInTheDocument();
});
test("removing the last selected file does not link to an invalid empty selected query", async () => {
  renderWorkspace("/?scope=selected");
  await screen.findByText("0 selected files");
  await waitFor(() =>
    expect(screen.queryByText("Loading workspace…")).not.toBeInTheDocument(),
  );
  expect(
    screen.queryByRole("link", { name: "0 Clean" }),
  ).not.toBeInTheDocument();
});

test("recent runs show newest uploads first even when the API returns oldest first", async () => {
  const original = secondRun.created_at;
  secondRun.created_at = {
    instant: "2026-09-17T12:00:00Z",
    display: "17 September 2026, 13:00 BST",
    timezone: "Europe/London",
  };
  try {
    renderWorkspace();
    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent(
      "orders-september.csv",
    );
  } finally {
    secondRun.created_at = original;
  }
});
