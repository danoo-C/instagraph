import { isApiError } from "@/api/errors";
import { AlertIcon } from "@/components/icons";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/** Clear error card for a failed request (§5.1). Shows the backend error message when available. */
export function ErrorCard({
  error,
  title,
  onRetry,
  className,
}: {
  error: unknown;
  title?: string;
  onRetry?: () => void;
  className?: string;
}) {
  const t = useT();
  const message = isApiError(error)
    ? error.isNetwork
      ? t("error.network")
      : error.message
    : error instanceof Error
      ? error.message
      : String(error ?? "");
  const code = isApiError(error) && !error.isNetwork ? error.code : null;
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-start gap-2 rounded-lg border border-danger/40 bg-danger/5 p-4 text-sm",
        className,
      )}
    >
      <div className="flex items-center gap-2 font-medium text-danger">
        <AlertIcon />
        {title ?? t("error.title")}
      </div>
      {message && <p className="text-fg">{message}</p>}
      {code && <p className="font-mono text-xs text-muted">{code}</p>}
      {onRetry && (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          {t("error.retry")}
        </button>
      )}
    </div>
  );
}
