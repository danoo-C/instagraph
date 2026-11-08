import { Suspense, useEffect, useRef } from "react";
import { useIsFetching } from "@tanstack/react-query";
import { ChartSkeleton } from "@/components/Skeleton";
import { printNow } from "@/features/print/print";
import { useT } from "@/i18n";
import { CHAT_TABS } from "@/pages/ChatDetail/tabs";

/** Tabs included in the printed chat report (§11), in order. */
const PRINT_TABS = ["summary", "timeline", "when", "dynamics", "words", "emojis"] as const;

const READY_POLL_MS = 400;
const SETTLE_MS = 1200;
const MAX_WAIT_MS = 20_000;

/**
 * Chat report for printing: the report tabs stacked, each starting on a new page
 * (`data-print-section`). Opens the print dialog once everything has loaded
 * (no pending queries, no skeletons) and calls `onDone` after printing.
 */
export function ChatPrintReport({
  chatId,
  onDone,
  autoPrint = true,
}: {
  chatId: string;
  onDone: () => void;
  autoPrint?: boolean;
}) {
  const t = useT();
  const fetching = useIsFetching();
  const fetchingRef = useRef(fetching);
  useEffect(() => {
    fetchingRef.current = fetching;
  }, [fetching]);
  const root = useRef<HTMLDivElement>(null);
  const tabs = PRINT_TABS.map((id) => CHAT_TABS.find((d) => d.id === id)).filter(
    (d): d is NonNullable<typeof d> => d != null,
  );

  useEffect(() => {
    if (!autoPrint) return;
    const started = Date.now();
    let readySince: number | null = null;
    let printed = false;
    const timer = window.setInterval(() => {
      const busy =
        fetchingRef.current > 0 || root.current?.querySelector('[aria-busy="true"]') != null;
      const now = Date.now();
      if (busy) readySince = null;
      else readySince ??= now;
      const settled = readySince != null && now - readySince >= SETTLE_MS;
      if (!printed && (settled || now - started > MAX_WAIT_MS)) {
        printed = true;
        window.clearInterval(timer);
        printNow();
      }
    }, READY_POLL_MS);
    const after = () => onDone();
    window.addEventListener("afterprint", after);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("afterprint", after);
    };
  }, [autoPrint, onDone]);

  return (
    <div ref={root} className="flex flex-col gap-8">
      <div
        className="flex items-center gap-3 rounded-md border border-border bg-surface-2 p-3 text-sm"
        data-print-hide
        role="status"
      >
        <span className="flex-1">Preparing the printable report…</span>
        <button type="button" className="btn btn-sm" onClick={printNow}>
          Print now
        </button>
        <button type="button" className="btn btn-sm" onClick={onDone}>
          Close
        </button>
      </div>
      {tabs.map((d) => {
        const Tab = d.component;
        return (
          <section key={d.id} data-print-section aria-label={t(d.label)}>
            <h2 className="mb-3 text-lg font-semibold">{t(d.label)}</h2>
            <Suspense fallback={<ChartSkeleton height={240} />}>
              <Tab chatId={chatId} />
            </Suspense>
          </section>
        );
      })}
    </div>
  );
}
