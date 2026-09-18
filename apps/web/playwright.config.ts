import { defineConfig } from "@playwright/test";

// Playwright forces color in workers; avoid conflicting inherited terminal settings.
delete process.env.NO_COLOR;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    actionTimeout: 15_000,
    browserName: "chromium",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "../../.venv/bin/python ../../tests/support/browser_server.py",
    url: "http://127.0.0.1:5173",
    timeout: 60_000,
    reuseExistingServer: false,
    gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
  },
});
