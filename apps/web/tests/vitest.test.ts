// `bun test` uses Bun's own runner, so delegate the browser suite to Vitest/jsdom.
import { test, expect } from "bun:test";
import { spawnSync } from "node:child_process";
test("workspace component suite (Vitest)", () => {
  const result = spawnSync("node", ["node_modules/vitest/vitest.mjs", "run"], {
    stdio: "inherit",
  });
  expect(result.status).toBe(0);
}, 30_000);
