import { expect, test } from "@playwright/test";
import {
  eligibleSavings,
  openApp,
  PRODUCTION_PRESET,
  shot,
  startRun,
  switchPersona,
  tokenFor,
  waitForStatus,
} from "./helpers";

test("critical workflow: request, change request, two countersigns, executed and verified", async ({
  page,
}) => {
  await openApp(page);
  const before = await eligibleSavings(page);
  expect(before).toBeGreaterThan(0);

  await startRun(page);
  await waitForStatus(page, "awaiting_approval");
  const rows = page.getByTestId("action-row");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThanOrEqual(3);
  await expect(page.getByTestId("approval-progress")).toHaveText("0 of 2 approvals");
  await expect(page.getByTestId("waiting-callout")).toContainText("Waiting for a countersign");
  // What the agent reviewed and left out is part of the change request, with reasons.
  await expect(page.getByTestId("not-proposed-row").filter({ hasText: "i-0c4f9e2a7b1d30005" })).toBeVisible();
  await page.getByTestId("run-panel").scrollIntoViewIfNeeded();
  await page.evaluate(() => document.getElementById("run-panel")?.scrollIntoView({ block: "start" }));
  await shot(page, "critical-01-plan");

  await switchPersona(page, "Alex");
  await page.getByTestId("countersign").click();
  await expect(page.getByTestId("approval-progress")).toHaveText("1 of 2 approvals");
  await expect(page.getByTestId("countersign-blocked")).toContainText("already countersigned");
  await expect(page.getByTestId("signer")).toHaveCount(1);

  await switchPersona(page, "Jordan");
  await page.getByTestId("countersign").click();
  await expect(page.getByTestId("approval-progress")).toHaveText("2 of 2 approvals");
  await expect(page.getByTestId("signer")).toHaveCount(2);
  await page.evaluate(() => document.getElementById("change-request")?.scrollIntoView({ block: "start" }));
  await shot(page, "critical-02-countersigned");

  await waitForStatus(page, "completed");
  const realized = page.getByTestId("realized-savings");
  await expect(realized).toContainText("realized");
  expect(Number(await realized.getAttribute("data-value"))).toBeGreaterThan(0);
  await expect(page.getByTestId("outcome").first()).toContainText("verified");
  await expect(page.getByTestId("report")).toContainText("verified");
  await expect.poll(() => eligibleSavings(page), { timeout: 30_000 }).toBeLessThan(before);
  await expect(page.getByTestId("kpi-changed")).toBeVisible();
  await page.getByTestId("execution").scrollIntoViewIfNeeded();
  await page.evaluate(() =>
    document.querySelector('[data-testid="execution"]')?.scrollIntoView({ block: "start" }),
  );
  await shot(page, "critical-03-completed");

  await page.getByRole("tab", { name: "Trace" }).click();
  const trace = page.getByTestId("trace");
  await expect(trace).toBeVisible();
  await expect(trace.locator('[data-op="execute_action"]').first()).toBeVisible();
  await page.evaluate(() =>
    document.querySelector('[data-testid="trace"]')?.scrollIntoView({ block: "start" }),
  );
  await shot(page, "trace");

  await page.getByRole("link", { name: "Estate" }).click();
  await expect(page.getByTestId("findings-table")).toBeVisible();
  await page.waitForTimeout(400);
  await shot(page, "estate");
  await page.getByTestId("finding-row").filter({ hasText: "i-0c4f9e2a7b1d30006" }).click();
  const drawer = page.getByTestId("resource-drawer");
  await expect(drawer).toContainText("instruction-like text");
  await expect(drawer).toContainText("never shown to the model");
  await page.waitForTimeout(400);
  await shot(page, "estate-resource");
});

test("policy: a production change is never proposed", async ({ page }) => {
  await openApp(page);
  await startRun(page, { preset: PRODUCTION_PRESET });
  await waitForStatus(page, ["completed", "failed"]);
  await expect(page.getByTestId("run-status")).toHaveAttribute("data-status", "completed");
  await expect(page.getByTestId("action-row")).toHaveCount(0);
  await expect(page.getByTestId("change-request")).toHaveCount(0);
  await expect(page.getByTestId("report")).toContainText(/production/i);
  await expect(page.getByTestId("not-proposed-row").filter({ hasText: "i-0c4f9e2a7b1d30004" })).toBeVisible();
  await page.evaluate(() => document.getElementById("run-panel")?.scrollIntoView({ block: "start" }));
  await shot(page, "policy-production");
});

test("fault injection: a pricing outage is visible and the run stays safe", async ({ page }) => {
  await openApp(page);
  await startRun(page, { faults: ["pricing_unavailable"] });
  await waitForStatus(page, "awaiting_approval");
  await expect(page.getByTestId("degraded-banner")).toBeVisible();
  await expect(page.getByTestId("degraded-banner")).toContainText(/pric/i);
  await expect(page.getByTestId("caveats")).toContainText(/pric/i);
  await expect(page.getByTestId("event-degraded").first()).toBeAttached();
  await expect(page.getByTestId("countersign")).toHaveCount(0);
  await page.evaluate(() => document.getElementById("run-panel")?.scrollIntoView({ block: "start" }));
  await shot(page, "fault-pricing-outage");
});

test("rbac: a viewer cannot start a run and the requester cannot countersign", async ({ page }) => {
  await openApp(page);
  await switchPersona(page, "Riley");
  await expect(page.getByTestId("start-run")).toBeDisabled();
  await expect(page.getByTestId("start-blocked")).toContainText("viewer");
  await shot(page, "rbac-viewer");

  await switchPersona(page, "Sam");
  await expect(page.getByTestId("start-run")).toBeEnabled();
  const runId = await startRun(page);
  await waitForStatus(page, "awaiting_approval");
  await expect(page.getByTestId("countersign")).toHaveCount(0);
  await expect(page.getByTestId("countersign-blocked")).toContainText("separation of duties");
  await expect(page.getByTestId("waiting-callout")).toContainText("cannot approve");

  // The server enforces it too: the requester's own token cannot record a decision.
  const token = await tokenFor(page, "operator");
  const auth = { Authorization: `Bearer ${token}` };
  const run = await (await page.request.get(`/api/runs/${runId}`, { headers: auth })).json();
  const res = await page.request.post(`/api/runs/${runId}/decision`, {
    headers: auth,
    data: {
      plan_hash: run.plan.hash,
      decisions: run.plan.actions.map((a: { id: string }) => ({ action_id: a.id, decision: "approve" })),
    },
  });
  expect(res.status()).toBe(403);
  expect(res.headers()["content-type"]).toContain("application/problem+json");
});

test("evals page shows the criteria and the mutation matrix", async ({ page }) => {
  const res = await page.request.get("/api/evals/latest");
  test.skip(res.status() === 404, "no evaluation report is published yet");
  await openApp(page, "evals");
  await expect(page.getByTestId("honesty")).toBeVisible();
  await expect(page.getByTestId("criteria-table")).toBeVisible();
  await expect(page.getByTestId("criterion-row").first()).toBeVisible();
  await expect(page.getByTestId("mutation-matrix")).toBeVisible();
  await shot(page, "evals");
});

test("audit chain verifies after a run", async ({ page }) => {
  await openApp(page);
  await startRun(page, { preset: PRODUCTION_PRESET });
  await waitForStatus(page, "completed");
  await page.getByRole("link", { name: "Audit" }).click();
  await expect(page.getByTestId("audit-row").first()).toBeVisible();
  expect(await page.getByTestId("audit-row").count()).toBeGreaterThanOrEqual(2);
  await page.getByTestId("verify-chain").click();
  const result = page.getByTestId("verify-result");
  await expect(result).toHaveAttribute("data-valid", "true");
  await expect(result).toContainText("Chain valid");
  await expect(result).toContainText(/\d+ entries/);
  await shot(page, "audit");
});
