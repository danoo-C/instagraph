import { useEffect, useRef, useState } from "react";
import { isApiError } from "@/api/errors";
import { getJob } from "@/features/import/api";
import { FINAL_STATUSES, type ImportJobState } from "@/features/import/model";

const STORAGE_KEY = "instagraph.importJob";

/** Remember the running job so the page can re-attach after navigation/reload. */
export function rememberJob(jobId: string | null): void {
  try {
    if (jobId) sessionStorage.setItem(STORAGE_KEY, jobId);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // storage unavailable: no re-attach
  }
}

export function rememberedJob(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/**
 * Live job state via Server-Sent Events (`/api/import/jobs/{id}/events`, §5.2).
 * Events: `progress`, then one final `done` | `error` | `cancelled`. If the stream
 * drops before a final event, the state is polled once and the stream re-opens.
 */
export function useImportJob(
  jobId: string | null,
  onFinished?: (state: ImportJobState) => void,
): ImportJobState | null {
  const [state, setState] = useState<ImportJobState | null>(null);
  const finishedRef = useRef(onFinished);
  useEffect(() => {
    finishedRef.current = onFinished;
  }, [onFinished]);

  useEffect(() => {
    if (!jobId) return;
    let closed = false;
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const finish = (s: ImportJobState) => {
      if (closed) return;
      closed = true;
      es?.close();
      setState(s);
      finishedRef.current?.(s);
    };

    const handle = (e: MessageEvent<string>) => {
      try {
        const s = JSON.parse(e.data) as ImportJobState;
        if (FINAL_STATUSES.has(s.status)) finish(s);
        else if (!closed) setState(s);
      } catch {
        // ignore malformed event
      }
    };

    const connect = () => {
      if (closed) return;
      es = new EventSource(`/api/import/jobs/${encodeURIComponent(jobId)}/events`);
      for (const name of ["progress", "done", "error", "cancelled", "message"])
        es.addEventListener(name, handle as EventListener);
      es.onerror = () => {
        if (closed) return;
        es?.close();
        getJob(jobId)
          .then((s) => {
            if (FINAL_STATUSES.has(s.status)) finish(s);
            else {
              setState(s);
              retry = setTimeout(connect, 1000);
            }
          })
          .catch((err: unknown) => {
            if (isApiError(err) && err.status === 404) {
              finish({
                job_id: jobId,
                kind: "path",
                status: "error",
                stage: "queued",
                done: 0,
                total: 0,
                message: "",
                messages: 0,
                error: {
                  code: "job_lost",
                  message: "The import job is no longer known to the server.",
                },
                created_at: new Date().toISOString(),
                seq: 0,
              });
            } else if (!closed) retry = setTimeout(connect, 2000);
          });
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      es?.close();
    };
  }, [jobId]);

  return jobId && state?.job_id === jobId ? state : null;
}
