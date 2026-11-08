import { useCallback, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, isApiError } from "@/api/errors";
import { queryClient } from "@/api/queryClient";
import { useApi } from "@/api/useApi";
import { AlertIcon } from "@/components/icons";
import {
  cancelJob,
  importFromPath,
  importSample,
  uploadZips,
  type ImportJobRef,
} from "@/features/import/api";
import { describeImportError, type ImportJobState } from "@/features/import/model";
import { rememberJob, rememberedJob, useImportJob } from "@/features/import/useImportJob";
import { formatDate, formatNumber } from "@/lib/format";
import { ExportGuide } from "@/pages/Import/ExportGuide";
import { JobProgress, type UploadProgress } from "@/pages/Import/JobProgress";
import { PathPanel } from "@/pages/Import/PathPanel";
import { UploadPanel } from "@/pages/Import/UploadPanel";

function ImportErrorBox({ error, onDismiss }: { error: ApiError; onDismiss: () => void }) {
  const { title, hint } = describeImportError(error.code);
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-lg border border-danger/40 bg-danger/5 p-4 text-sm"
    >
      <AlertIcon className="mt-0.5 shrink-0 text-danger" />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="font-medium text-danger">{title}</p>
        {error.message && <p className="break-words">{error.message}</p>}
        {hint && <p className="text-muted">{hint}</p>}
        <p className="font-mono text-xs text-muted">{error.code}</p>
      </div>
      <button type="button" className="btn btn-sm" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  );
}

/** Import screen (§5.2). */
export default function ImportPage() {
  const navigate = useNavigate();
  const status = useApi("/api/status", undefined, { filters: false });
  const [jobId, setJobId] = useState<string | null>(() => rememberedJob());
  const [upload, setUpload] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const abortUpload = useRef<(() => void) | null>(null);

  const onFinished = useCallback(
    (s: ImportJobState) => {
      rememberJob(null);
      setJobId(null);
      setCancelling(false);
      if (s.status === "done") {
        void queryClient.invalidateQueries();
        navigate("/overview");
      } else if (s.status === "cancelled") {
        setNotice("Import cancelled. Your previous data (if any) was kept.");
      } else {
        setError(
          new ApiError(400, s.error?.code ?? "import_failed", s.error?.message ?? "Import failed"),
        );
      }
    },
    [navigate],
  );
  const job = useImportJob(jobId, onFinished);

  const begin = (ref: ImportJobRef) => {
    rememberJob(ref.job_id);
    setJobId(ref.job_id);
  };
  const fail = (e: unknown) => {
    if (isApiError(e) && e.code === "cancelled") setNotice("Upload cancelled.");
    else setError(isApiError(e) ? e : new ApiError(0, "network", String(e)));
  };
  const reset = () => {
    setError(null);
    setNotice(null);
  };

  const startUpload = (files: File[]) => {
    reset();
    setUpload({ loaded: 0, total: files.reduce((a, f) => a + f.size, 0) });
    const h = uploadZips(files, (loaded, total) => setUpload({ loaded, total }));
    abortUpload.current = h.abort;
    h.promise.then(begin, fail).finally(() => {
      abortUpload.current = null;
      setUpload(null);
      setCancelling(false);
    });
  };

  const startRequest = (p: Promise<ImportJobRef>) => {
    reset();
    setStarting(true);
    p.then(begin, fail).finally(() => setStarting(false));
  };

  const cancel = () => {
    setCancelling(true);
    if (abortUpload.current) abortUpload.current();
    else if (jobId)
      cancelJob(jobId).catch((e: unknown) => {
        setCancelling(false);
        fail(e);
      });
  };

  const busy = Boolean(upload || jobId);
  const st = status.data;
  const hasData = Boolean(st?.has_data);

  return (
    <div className="page max-w-4xl">
      <h1 className="page-title">{hasData ? "Re-import" : "Import your Instagram messages"}</h1>
      <p className="text-sm text-muted">
        Your data stays on this computer. Instagraph reads your Instagram data export locally and
        never uploads it anywhere.
      </p>

      {hasData && st?.counts && !busy && (
        <div className="card flex flex-col gap-2 p-4 text-sm sm:flex-row sm:items-center">
          <p className="flex-1">
            You already imported <strong>{formatNumber(st.counts.messages)}</strong> messages in{" "}
            <strong>{formatNumber(st.counts.threads)}</strong> chats
            {st.first_ts && st.last_ts && (
              <>
                {" "}
                ({formatDate(st.first_ts)} – {formatDate(st.last_ts)})
              </>
            )}
            . Importing again <strong>replaces</strong> this data.
          </p>
          <div className="flex gap-2">
            <Link to="/import/report" className="btn btn-sm">
              Import report
            </Link>
            <Link to="/overview" className="btn btn-sm btn-primary">
              Go to Overview
            </Link>
          </div>
        </div>
      )}

      {error && <ImportErrorBox error={error} onDismiss={() => setError(null)} />}
      {notice && (
        <p role="status" className="rounded-md border border-border bg-surface-2 p-3 text-sm">
          {notice}
        </p>
      )}

      {busy ? (
        <JobProgress upload={upload} job={job} onCancel={cancel} cancelling={cancelling} />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <UploadPanel disabled={starting} onUpload={startUpload} />
            <PathPanel
              disabled={starting}
              onSubmit={(path) => startRequest(importFromPath(path))}
            />
          </div>
          <section className="card flex flex-col gap-2 p-4 sm:flex-row sm:items-center">
            <div className="flex-1">
              <h2 className="font-semibold">Just looking?</h2>
              <p className="text-sm text-muted">
                Generate a fake dataset to explore the app (demo and testing).
                {hasData && " This replaces your current data."}
              </p>
            </div>
            <button
              type="button"
              className="btn"
              disabled={starting}
              onClick={() => startRequest(importSample())}
            >
              Load sample data
            </button>
          </section>
        </>
      )}

      <ExportGuide />
    </div>
  );
}
