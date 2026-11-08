import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

const BACKEND = path.resolve(import.meta.dirname, "..", "..", "backend");
const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);

/** Generate a small synthetic Instagram export zip with the backend's sample generator. */
function sampleZip(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "instagraph-e2e-sample-"));
  const out = path.join(dir, "export");
  execFileSync(
    path.join(BACKEND, ".venv", "bin", "python"),
    ["-m", "instagraph.sample", "--messages", "3000", "--seed", "3", "--out", out, "--zip"],
    { cwd: BACKEND, stdio: "pipe" },
  );
  const zip = `${out}.zip`;
  if (!fs.existsSync(zip)) throw new Error(`sample generator did not write ${zip}`);
  return zip;
}

/** Collects console errors, page errors, failed API calls and any non-local request. */
function watch(page: Page) {
  const problems: string[] = [];
  const external: string[] = [];
  void page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.protocol === "data:" || url.protocol === "blob:" || LOCAL_HOSTS.has(url.hostname))
      return route.continue();
    external.push(url.href);
    return route.abort("blockedbyclient");
  });
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`console: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("response", (r) => {
    const url = new URL(r.url());
    if (url.pathname.startsWith("/api/") && r.status() >= 500)
      problems.push(`HTTP ${r.status()} ${url.pathname}${url.search}`);
  });
  return { problems, external };
}

/** Wait until no skeleton / busy region is left on the page. */
async function settled(page: Page) {
  await page.waitForLoadState("networkidle");
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 30_000 });
}

const digits = (s: string | null) => (s ?? "").replace(/\D/g, "");

test("import → overview → chat tabs → CSV export", async ({ page, request }) => {
  const { problems, external } = watch(page);
  const zip = sampleZip();

  // ---- Import through the UI
  await page.goto("/import");
  await page.locator('input[type="file"]').setInputFiles(zip);
  const overviewResponse = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/overview" && r.ok(),
    { timeout: 150_000 },
  );
  await page.getByRole("button", { name: /^Upload and import/ }).click();
  await page.waitForURL(/\/overview/, { timeout: 150_000 });

  // ---- Overview: total-messages tile equals the API value (same query the page used)
  const pageQuery = new URL((await overviewResponse).url());
  const api = await request.get(`/api/overview${pageQuery.search}`);
  expect(api.ok()).toBe(true);
  const overview = (await api.json()) as {
    tiles: Array<{ key: string; label: string; value: unknown }>;
  };
  const total = overview.tiles.find((t) => t.key === "total_messages");
  expect(total, "total_messages tile in /api/overview").toBeTruthy();
  expect(Number(total!.value)).toBeGreaterThan(0);
  const tile = page.locator(".card", { hasText: total!.label }).first();
  await expect(tile).toBeVisible();
  await expect
    .poll(async () => digits(await tile.locator(".text-2xl").textContent()))
    .toBe(String(total!.value));
  await settled(page);

  // ---- Open a chat from the chats list
  await page
    .getByRole("link", { name: /^Chats$/ })
    .first()
    .click();
  await page.waitForURL(/\/chats(\?|$)/);
  const firstRow = page.locator("tbody tr[tabindex]").first();
  await expect(firstRow).toBeVisible();
  await firstRow.click();
  await page.waitForURL(/\/chats\/\d+\/summary/);
  await settled(page);

  // ---- Visit every tab: no error cards
  const tabs = page.getByRole("tab");
  const count = await tabs.count();
  expect(count).toBeGreaterThanOrEqual(10);
  const names = await tabs.allTextContents();
  for (const name of names) {
    await page.getByRole("tab", { name, exact: true }).click();
    await expect(page.getByRole("tab", { name, exact: true })).toHaveAttribute(
      "data-state",
      "active",
    );
    await settled(page);
    await expect(
      page.locator('[role="tabpanel"] [role="alert"]'),
      `error card on the ${name} tab`,
    ).toHaveCount(0);
  }

  // ---- CSV export from the first chart on the Summary tab
  await page.getByRole("tab", { name: names[0]!, exact: true }).click();
  await settled(page);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export CSV" }).first().click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/\.csv$/);
  const csv = fs.readFileSync(await file.path(), "utf8");
  expect(csv.split(/\r?\n/).length).toBeGreaterThan(1);

  // ---- Print report (§11): report tabs stacked, print dialog opened once loaded
  await page.evaluate(() => {
    (window as unknown as { __printed: number }).__printed = 0;
    window.print = () => {
      (window as unknown as { __printed: number }).__printed++;
    };
  });
  await page.getByRole("button", { name: "Print / PDF" }).click();
  await expect(page).toHaveURL(/[?&]print=1/);
  await expect(page.locator("section[data-print-section]")).toHaveCount(6);
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { __printed: number }).__printed), {
      timeout: 30_000,
    })
    .toBe(1);
  await expect(page.locator("section[data-print-section] [role='alert']")).toHaveCount(0);

  expect(external, "requests to non-local hosts").toEqual([]);
  expect(problems, "console errors / failed requests").toEqual([]);
});
