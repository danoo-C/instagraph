/** Pure helpers for the import screen (§5.2): stages, progress and error messages. */
import type { components } from "@/api/schema";

export type ImportJobState = components["schemas"]["ImportJobState"];
export type JobStatus = ImportJobState["status"];

export const FINAL_STATUSES: ReadonlySet<JobStatus> = new Set(["done", "error", "cancelled"]);

/** The four user-facing steps (§5.2). */
export const IMPORT_STEPS = [
  "Files found",
  "Threads parsed",
  "Messages parsed",
  "Stats prepared",
] as const;

/** Backend stage → index into IMPORT_STEPS (the step currently in progress). */
export function stepIndex(stage: string): number {
  switch (stage) {
    case "queued":
    case "upload":
    case "sample":
    case "discovery":
      return 0;
    case "locales":
    case "parse":
      return 1;
    case "merge":
      return 2;
    case "derive":
    case "persist":
      return 3;
    case "done":
      return IMPORT_STEPS.length;
    default:
      return 0;
  }
}

/** [start, end] of each backend stage on the overall 0..1 progress bar. */
const STAGE_SPAN: Record<string, [number, number]> = {
  queued: [0, 0.01],
  upload: [0, 0.02],
  sample: [0.01, 0.05],
  discovery: [0.05, 0.08],
  locales: [0.08, 0.12],
  parse: [0.12, 0.75],
  merge: [0.75, 0.88],
  derive: [0.88, 0.95],
  persist: [0.95, 1],
  done: [1, 1],
};

/** Overall progress 0..1 for a job state (interpolates `done/total` inside the stage). */
export function overallProgress(
  s: Pick<ImportJobState, "stage" | "done" | "total" | "status">,
): number {
  if (s.status === "done") return 1;
  const [a, b] = STAGE_SPAN[s.stage] ?? [0, 0];
  const frac = s.total > 0 ? Math.min(1, Math.max(0, s.done / s.total)) : 0;
  return a + (b - a) * frac;
}

const ERROR_TEXT: Record<string, { title: string; hint?: string }> = {
  path_not_found: {
    title: "Path not found",
    hint: "Check the spelling and that the folder or zip exists on this computer.",
  },
  path_not_absolute: {
    title: "Please enter an absolute path",
    hint: "For example /home/me/Downloads/instagram-export or C:\\Users\\me\\Downloads\\export.zip.",
  },
  path_not_readable: {
    title: "The path is not readable",
    hint: "The app does not have permission to read it. Check the file permissions.",
  },
  unsupported_path: {
    title: "Unsupported path",
    hint: "Enter a folder (unzipped export or a folder of zips) or a .zip file.",
  },
  html_export: {
    title: "This is an HTML export",
    hint: "Instagraph needs the JSON format. Request a new export and choose “Format: JSON” (see the guide below).",
  },
  unsafe_zip: {
    title: "The zip file looks unsafe or damaged",
    hint: "It contains unsafe paths or is corrupted. Download the export again from Instagram.",
  },
  import_running: {
    title: "An import is already running",
    hint: "Wait for it to finish (or cancel it) and try again.",
  },
  invalid_upload: { title: "Upload rejected", hint: "Only .zip files can be uploaded." },
  upload_interrupted: { title: "The upload was interrupted", hint: "Please try again." },
  no_threads: {
    title: "No messages found",
    hint: "Make sure the export contains Messages and was requested in JSON format.",
  },
  parse_failed: {
    title: "No chat could be read",
    hint: "The export may be damaged or incomplete.",
  },
  invalid_source: { title: "The export could not be read" },
  network: {
    title: "Cannot reach the Instagraph server",
    hint: "Is the backend still running?",
  },
  validation_error: { title: "Invalid input" },
};

/** Friendly title/hint for a backend error code (falls back to the raw message). */
export function describeImportError(code: string | undefined | null): {
  title: string;
  hint?: string;
} {
  return (code && ERROR_TEXT[code]) || { title: "Import failed" };
}

/** Bytes → "1.2 GB". */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

/** Keep only .zip files (case-insensitive). */
export function zipFilesOnly<T extends { name: string }>(
  files: readonly T[],
): { zips: T[]; rejected: T[] } {
  const zips: T[] = [];
  const rejected: T[] = [];
  for (const f of files) (f.name.toLowerCase().endsWith(".zip") ? zips : rejected).push(f);
  return { zips, rejected };
}
