import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi } from "@/api/useApi";
import { EmptyState } from "@/components/EmptyState";
import { ErrorCard } from "@/components/ErrorCard";
import { Skeleton } from "@/components/Skeleton";
import { SearchSelect, type SearchOption } from "@/features/compare/SearchSelect";
import type { Schemas } from "@/features/people/apiTypes";
import { useDebounced } from "@/features/people/useDebounced";
import { StoryViewer } from "@/features/wrapped/StoryViewer";
import { buildSlides, slideAltText } from "@/features/wrapped/slides";
import type { Slide } from "@/features/wrapped/model";
import { paintSlide } from "@/features/wrapped/paint";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

type Wrapped = Schemas["WrappedResponse"];

function Thumb({ slide, onClick }: { slide: Slide; onClick: () => void }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (ref.current) paintSlide(ref.current, slide);
  }, [slide]);
  return (
    <button
      type="button"
      onClick={onClick}
      className="overflow-hidden rounded-xl border border-border transition-transform hover:scale-[1.02] focus-visible:scale-[1.02]"
      title={slide.kicker}
    >
      <canvas
        ref={ref}
        role="img"
        aria-label={slideAltText(slide)}
        className="block aspect-[9/16] w-full"
      />
    </button>
  );
}

function ChatPicker({
  chat,
  onChange,
}: {
  chat: number | null;
  onChange: (id: number | null) => void;
}) {
  const [text, setText] = useState("");
  const q = useDebounced(text.trim());
  const chats = useApi("/api/chats", {
    query: { q: q || undefined, sort: "messages", order: "desc", limit: 30 },
  });
  const threads = useApi("/api/settings/threads", undefined, {
    filters: false,
    enabled: chat != null,
  });
  const current =
    chat != null ? threads.data?.items.find((t) => t.thread_id === chat)?.title : null;
  const options: SearchOption[] = (chats.data?.items ?? []).map((c) => ({
    value: c.thread_id,
    label: c.title,
    hint: `${formatNumber(c.messages)} msgs`,
  }));
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div
        role="radiogroup"
        aria-label="Scope"
        className="inline-flex rounded-md border border-border p-0.5"
      >
        <button
          type="button"
          role="radio"
          aria-checked={chat == null}
          className={cn(
            "rounded px-3 py-1 text-sm font-medium",
            chat == null ? "bg-accent text-accent-fg" : "text-muted",
          )}
          onClick={() => onChange(null)}
        >
          All chats
        </button>
        <span
          className={cn(
            "rounded px-3 py-1 text-sm font-medium",
            chat != null ? "bg-accent text-accent-fg" : "text-muted",
          )}
          aria-hidden={chat == null}
        >
          {chat != null ? (current ?? `Chat #${chat}`) : "One chat"}
        </span>
      </div>
      <SearchSelect
        className="w-64"
        label="Pick a chat"
        placeholder="Pick a chat…"
        options={options}
        loading={chats.isFetching}
        onSearch={setText}
        onSelect={(id) => onChange(id)}
      />
    </div>
  );
}

export default function WrappedPage() {
  const [params, setParams] = useSearchParams();
  const chatRaw = params.get("chat");
  const chat = chatRaw && /^\d+$/.test(chatRaw) ? Number(chatRaw) : null;
  const yearRaw = params.get("year");
  const [playing, setPlaying] = useState<number | null>(null);

  const set = (patch: Record<string, string | null>) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(patch)) {
          if (v == null) next.delete(k);
          else next.set(k, v);
        }
        return next;
      },
      { replace: true },
    );

  // Wrapped is a "year in review": the global date range does not apply.
  const noRange = { start: undefined, end: undefined };
  const years = useApi("/api/wrapped/years", { query: { ...noRange, chat: chat ?? undefined } });
  const yearList = useMemo(
    () => (years.data?.years ?? []).filter((y) => y.messages > 0),
    [years.data],
  );
  const year =
    yearRaw && /^\d{4}$/.test(yearRaw)
      ? Number(yearRaw)
      : (yearList[yearList.length - 1]?.year ?? null);
  const wrapped = useApi<Wrapped>(
    "/api/wrapped",
    { query: { ...noRange, year: year ?? undefined, chat: chat ?? undefined } },
    { enabled: year != null, keepPrevious: false },
  );
  const settings = useApi("/api/settings", undefined, { filters: false });
  const anonymize = settings.data?.settings.anonymize ?? false;

  const slides = useMemo(
    () => (wrapped.data ? buildSlides(wrapped.data, { anonymize }) : []),
    [wrapped.data, anonymize],
  );
  const filenameBase = `wrapped-${year ?? ""}${wrapped.data?.chat_title ? `-${wrapped.data.chat_title}` : ""}`;

  return (
    <div className="page">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Wrapped</h1>
          <p className="text-sm text-muted">
            Your year in messages, story style. Every slide can be saved as a 1080×1920 PNG.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={slides.length === 0}
          onClick={() => setPlaying(0)}
        >
          ▶ Play story
        </button>
      </div>

      <section className="card flex flex-wrap items-center gap-4 p-4" data-print-hide>
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">Year</span>
          {years.isPending ? (
            <Skeleton className="h-9 w-64" />
          ) : (
            <div role="radiogroup" aria-label="Year" className="flex flex-wrap gap-1">
              {yearList.map((y) => (
                <button
                  key={y.year}
                  type="button"
                  role="radio"
                  aria-checked={y.year === year}
                  title={`${formatNumber(y.messages)} messages`}
                  className={cn("btn btn-sm", y.year === year && "btn-primary")}
                  onClick={() => set({ year: String(y.year) })}
                >
                  {y.year}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">Scope</span>
          <ChatPicker
            chat={chat}
            onChange={(id) => set({ chat: id == null ? null : String(id), year: null })}
          />
        </div>
      </section>

      {years.error ? (
        <ErrorCard error={years.error} onRetry={() => void years.refetch()} />
      ) : wrapped.error ? (
        <ErrorCard error={wrapped.error} onRetry={() => void wrapped.refetch()} />
      ) : !years.isPending && yearList.length === 0 ? (
        <EmptyState title="No messages" description="There is nothing to wrap up for this scope." />
      ) : wrapped.isPending || slides.length === 0 ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="aspect-[9/16] w-full" />
          ))}
        </div>
      ) : (
        <>
          {anonymize && (
            <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-sm text-muted">
              Anonymize mode is on: names are replaced and message text is hidden.
            </p>
          )}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
            {slides.map((s, i) => (
              <Thumb key={s.key} slide={s} onClick={() => setPlaying(i)} />
            ))}
          </div>
        </>
      )}

      {playing != null && slides.length > 0 && (
        <StoryViewer
          slides={slides}
          initialIndex={playing}
          filenameBase={filenameBase}
          onClose={() => setPlaying(null)}
        />
      )}
    </div>
  );
}
