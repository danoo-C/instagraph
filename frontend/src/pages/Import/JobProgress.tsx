import {
  IMPORT_STEPS,
  formatBytes,
  overallProgress,
  stepIndex,
  type ImportJobState,
} from "@/features/import/model";
import { formatNumber, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface UploadProgress {
  loaded: number;
  total: number;
}

function Bar({ value, label }: { value: number; label: string }) {
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(value * 100)}
      className="h-2.5 w-full overflow-hidden rounded-full bg-surface-2"
    >
      <div
        className="h-full rounded-full bg-accent transition-[width] duration-300"
        style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
      />
    </div>
  );
}

/** Live import progress: upload bar, then the four stages with a running counter (§5.2). */
export function JobProgress({
  upload,
  job,
  onCancel,
  cancelling,
}: {
  upload: UploadProgress | null;
  job: ImportJobState | null;
  onCancel: () => void;
  cancelling?: boolean;
}) {
  const current = job ? stepIndex(job.stage) : 0;
  const progress = job ? overallProgress(job) : 0;
  return (
    <section
      className="card flex flex-col gap-4 p-4"
      aria-labelledby="progress-title"
      aria-live="polite"
    >
      <div className="flex items-center gap-2">
        <h2 id="progress-title" className="font-semibold">
          {upload && !job ? "Uploading…" : "Importing…"}
        </h2>
        <button
          type="button"
          className="btn btn-sm ml-auto"
          onClick={onCancel}
          disabled={cancelling}
        >
          {cancelling ? "Cancelling…" : "Cancel"}
        </button>
      </div>

      {upload && !job && (
        <div className="flex flex-col gap-1">
          <Bar value={upload.total ? upload.loaded / upload.total : 0} label="Upload progress" />
          <p className="text-sm tabular-nums text-muted">
            {formatBytes(upload.loaded)} of {formatBytes(upload.total)}
            {upload.total > 0 && ` (${formatPercent(upload.loaded / upload.total, 0)})`}
            {upload.total > 0 && upload.loaded >= upload.total && " — processing upload…"}
          </p>
        </div>
      )}

      {(job || !upload) && (
        <>
          <Bar value={progress} label="Import progress" />
          <ol className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-4">
            {IMPORT_STEPS.map((label, i) => {
              const done = i < current;
              const active = i === current;
              return (
                <li
                  key={label}
                  className={cn(
                    "flex items-center gap-2 rounded-md border px-2 py-1.5",
                    done && "border-accent/40 text-fg",
                    active && "border-accent bg-accent/5 font-medium",
                    !done && !active && "border-border text-muted",
                  )}
                  aria-current={active ? "step" : undefined}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px]",
                      done ? "bg-accent text-accent-fg" : "border border-current",
                    )}
                  >
                    {done ? "✓" : i + 1}
                  </span>
                  {label}
                </li>
              );
            })}
          </ol>
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
            <span className="text-muted">{job?.message || "Starting…"}</span>
            {job && job.total > 0 && (job.stage === "parse" || job.stage === "locales") && (
              <span className="tabular-nums">
                {formatNumber(job.done)} / {formatNumber(job.total)} chats
              </span>
            )}
            <span className="ml-auto text-base font-semibold tabular-nums">
              {formatNumber(job?.messages ?? 0)} messages
            </span>
          </div>
        </>
      )}
    </section>
  );
}
