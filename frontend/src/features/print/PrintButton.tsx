import { printNow } from "@/features/print/print";
import { cn } from "@/lib/utils";

/**
 * "Print / PDF" button (§11). Charts are resized to the print width before the print
 * dialog opens (ECharts listens to window resize). Pair with `src/styles/print.css`.
 */
export function PrintButton({
  className,
  label = "Print / PDF",
  onPrint,
}: {
  className?: string;
  label?: string;
  /** Custom action instead of printing right away (e.g. switch to a print report view). */
  onPrint?: () => void;
}) {
  return (
    <button
      type="button"
      className={cn("btn btn-sm", className)}
      data-print-hide
      onClick={onPrint ?? printNow}
      title="Print this page or save it as PDF"
    >
      <svg
        viewBox="0 0 24 24"
        width="14"
        height="14"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        aria-hidden="true"
      >
        <path d="M6 9V3h12v6M6 18H4a1 1 0 0 1-1-1v-6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v6a1 1 0 0 1-1 1h-2" />
        <path d="M6 14h12v7H6z" />
      </svg>
      {label}
    </button>
  );
}
