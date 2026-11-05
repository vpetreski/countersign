import { defineConfig, devices } from "@playwright/test";

// Two projects. `local` (the default) starts a fresh, seeded, keyless backend on :8000 and the built UI on
// :5173. `live` runs the same specs against a deployment: BASE_URL=https://... pnpm e2e --project=live
const argv = process.argv.join(" ");
const live = /--project[= ]live\b/.test(argv);
if (live && !process.env.BASE_URL) {
  throw new Error("the live project needs BASE_URL, for example BASE_URL=https://example.vercel.app");
}

const viewport = { width: 1440, height: 900 };

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { trace: "retain-on-failure", screenshot: "only-on-failure", viewport },
  projects: [
    {
      name: "local",
      use: { ...devices["Desktop Chrome"], viewport, baseURL: "http://127.0.0.1:5173" },
    },
    {
      name: "live",
      // Without BASE_URL there is nothing to run against, so the live project is empty by default.
      testIgnore: process.env.BASE_URL ? [] : ["**/*"],
      use: { ...devices["Desktop Chrome"], viewport, baseURL: process.env.BASE_URL },
    },
  ],
  webServer: live
    ? undefined
    : [
        {
          command: "sh ./e2e/start-backend.sh",
          url: "http://127.0.0.1:8000/api/health/ready",
          reuseExistingServer: false,
          timeout: 180_000,
          stdout: "ignore",
          stderr: "pipe",
        },
        {
          command: "vite build && vite preview --host 127.0.0.1 --port 5173 --strictPort",
          url: "http://127.0.0.1:5173",
          reuseExistingServer: !process.env.CI,
          timeout: 180_000,
        },
      ],
});
