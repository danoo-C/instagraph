import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { chromium, defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests (§13). The backend serves the *built* frontend (`npm run build` first,
 * `npm run e2e` does both) from a throw-away data directory, single process, on 127.0.0.1.
 */
const ROOT = path.resolve(import.meta.dirname, "..");
const BACKEND = path.join(ROOT, "backend");
const PORT = Number(process.env.E2E_PORT ?? 8765);

// Created once by the runner; workers inherit it through the environment.
process.env.E2E_DATA_DIR ??= fs.mkdtempSync(path.join(os.tmpdir(), "instagraph-e2e-"));

if (!fs.existsSync(path.join(import.meta.dirname, "dist", "index.html")))
  throw new Error("frontend/dist is missing: run `npm run build` (or `npm run e2e`) first.");

/**
 * Chromium binary: `E2E_CHROMIUM` if set; else Playwright's own (if installed); else the newest
 * headless shell / Chromium already in the Playwright cache (offline machines whose browsers
 * were installed by another Playwright version).
 */
function chromiumExecutable(): string | undefined {
  if (process.env.E2E_CHROMIUM) return process.env.E2E_CHROMIUM;
  if (fs.existsSync(chromium.executablePath())) return undefined;
  const cache =
    process.env.PLAYWRIGHT_BROWSERS_PATH ?? path.join(os.homedir(), ".cache", "ms-playwright");
  const candidates: string[] = [];
  for (const dir of fs.existsSync(cache) ? fs.readdirSync(cache).sort().reverse() : []) {
    if (dir.startsWith("chromium_headless_shell-"))
      candidates.push(
        path.join(cache, dir, "chrome-headless-shell-linux64", "chrome-headless-shell"),
      );
    else if (dir.startsWith("chromium-"))
      candidates.push(path.join(cache, dir, "chrome-linux64", "chrome"));
  }
  return candidates.find((c) => fs.existsSync(c));
}

export default defineConfig({
  testDir: "e2e",
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    locale: "en-US",
    timezoneId: "UTC",
    acceptDownloads: true,
    trace: "retain-on-failure",
    launchOptions: {
      executablePath: chromiumExecutable(),
      // Chromium's crash reporter / sandbox fail inside some containers.
      args: ["--disable-crash-reporter", "--no-sandbox"],
    },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `.venv/bin/uvicorn instagraph.main:app --host 127.0.0.1 --port ${PORT}`,
    cwd: BACKEND,
    url: `http://127.0.0.1:${PORT}/api/status`,
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: "ignore",
    stderr: "pipe",
    env: {
      INSTAGRAPH_DATA_DIR: process.env.E2E_DATA_DIR,
      INSTAGRAPH_FRONTEND_DIST: path.join(import.meta.dirname, "dist"),
      INSTAGRAPH_WORKERS: "2",
    },
  },
});
