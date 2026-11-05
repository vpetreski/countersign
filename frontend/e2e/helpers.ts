import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, type Page } from "@playwright/test";

export const SHOTS_DIR = fileURLToPath(new URL("../../reports/tests/", import.meta.url));
export const DEFAULT_PRESET = "Find idle development VMs and unattached disks and propose savings.";
export const PRODUCTION_PRESET = "Stop the production API replica i-0c4f9e2a7b1d30004";

export async function shot(page: Page, name: string): Promise<void> {
  mkdirSync(SHOTS_DIR, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS_DIR, `${name}.png`), animations: "disabled" });
}

/** A fresh page has fresh sessionStorage, so this also creates a new demo workspace. */
export async function openApp(page: Page, view?: string): Promise<void> {
  await page.goto(view ? `/?view=${view}` : "/");
  await expect(page.getByTestId("persona-switcher")).toBeVisible();
}

export async function switchPersona(page: Page, name: string): Promise<void> {
  await page.getByTestId("persona-switcher").click();
  await page.getByRole("menuitemradio", { name: new RegExp(`^${name}\\b`) }).click();
  await expect(page.getByTestId("persona-switcher")).toContainText(name);
}

export async function startRun(
  page: Page,
  opts: { preset?: string; faults?: string[] } = {},
): Promise<string> {
  if (opts.preset && opts.preset !== DEFAULT_PRESET) {
    await page.getByRole("button", { name: opts.preset, exact: true }).click();
  }
  await expect(page.getByTestId("request-input")).toHaveValue(opts.preset ?? DEFAULT_PRESET);
  for (const f of opts.faults ?? []) {
    const toggle = page.locator(`[data-fault="${f}"]`);
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
  }
  await page.getByTestId("start-run").click();
  const panel = page.getByTestId("run-panel");
  await expect(panel).toBeVisible();
  return (await panel.getAttribute("data-run")) ?? "";
}

export async function waitForStatus(page: Page, status: string | string[], timeout = 90_000): Promise<void> {
  const list = Array.isArray(status) ? status : [status];
  await expect(page.getByTestId("run-status")).toHaveAttribute(
    "data-status",
    new RegExp(`^(${list.join("|")})$`),
    {
      timeout,
    },
  );
}

export async function eligibleSavings(page: Page): Promise<number> {
  const kpi = page.getByTestId("kpi-eligible");
  await expect(kpi).toHaveAttribute("data-value", /\d/);
  return Number(await kpi.getAttribute("data-value"));
}

export async function tokenFor(page: Page, persona: string): Promise<string> {
  return page.evaluate((key) => {
    const raw = sessionStorage.getItem("countersign.workspace");
    return raw ? (JSON.parse(raw).tokens[key] as string) : "";
  }, persona);
}
