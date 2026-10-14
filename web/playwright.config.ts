import { defineConfig } from "@playwright/test";

/** End-to-end against a running stack (compose web on 3010 by default). One worker: the flows share one agent. */
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 }, // a Node server sharing the host with image builds can take seconds to answer
  workers: 1,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "e2e-results.json" }]],
  use: {
    baseURL: process.env.WEB_URL ?? "http://localhost:3010",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
