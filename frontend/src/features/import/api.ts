import { apiFetch } from "@/api/client";
import { ApiError, toApiError } from "@/api/errors";
import type { components } from "@/api/schema";

export type ImportJobRef = components["schemas"]["ImportJobRef"];

export interface UploadHandle {
  promise: Promise<ImportJobRef>;
  abort: () => void;
}

/**
 * Upload zips as multipart (`files` field, §5.2) with XMLHttpRequest so we get upload
 * progress events. The browser streams the files from disk (multi-GB works).
 */
export function uploadZips(
  files: readonly File[],
  onProgress: (loaded: number, total: number) => void,
): UploadHandle {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<ImportJobRef>((resolve, reject) => {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    xhr.open("POST", "/api/import/upload");
    xhr.responseType = "text";
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded, e.total);
    };
    xhr.onload = () => {
      let body: unknown;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = xhr.responseText || null;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as ImportJobRef);
      else reject(toApiError(xhr.status, body, "Upload failed"));
    };
    xhr.onerror = () => reject(new ApiError(0, "network", "Network error during upload"));
    xhr.onabort = () => reject(new ApiError(0, "cancelled", "Upload cancelled"));
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
}

export const importFromPath = (path: string) =>
  apiFetch<ImportJobRef>("POST", "/api/import/path", { body: { path } });

export const importSample = () =>
  apiFetch<ImportJobRef>("POST", "/api/import/sample", { body: {} });

export const cancelJob = (jobId: string) =>
  apiFetch("DELETE", "/api/import/jobs/{job_id}", { path: { job_id: jobId } });

export const getJob = (jobId: string) =>
  apiFetch<components["schemas"]["ImportJobState"]>("GET", "/api/import/jobs/{job_id}", {
    path: { job_id: jobId },
  });
