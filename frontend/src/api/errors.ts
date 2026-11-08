/** Error thrown by every API helper. Mirrors the backend shape `{ error: { code, message } }`. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** 409 — no data imported yet. */
  get isNoData(): boolean {
    return this.status === 409;
  }

  get isNetwork(): boolean {
    return this.status === 0;
  }
}

/** Build an ApiError from a response status + parsed JSON body (any shape). */
export function toApiError(status: number, body: unknown, fallback = "Request failed"): ApiError {
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    const err = b.error as { code?: unknown; message?: unknown } | undefined;
    if (err && typeof err === "object") {
      return new ApiError(
        status,
        typeof err.code === "string" ? err.code : `http_${status}`,
        typeof err.message === "string" ? err.message : fallback,
        body,
      );
    }
    // FastAPI default validation shape: { detail: [...] | "..." }
    if ("detail" in b) {
      const msg = typeof b.detail === "string" ? b.detail : "Invalid request";
      return new ApiError(
        status,
        status === 422 ? "validation_error" : `http_${status}`,
        msg,
        body,
      );
    }
  }
  return new ApiError(
    status,
    `http_${status}`,
    typeof body === "string" && body ? body : fallback,
    body,
  );
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}
