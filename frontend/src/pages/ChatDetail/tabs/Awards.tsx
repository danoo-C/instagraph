import { useApi } from "@/api/useApi";
import type { components } from "@/api/schema";
import { EmptyState } from "@/components/EmptyState";
import { PersonBadge } from "@/components/PersonBadge";
import { formatAwardValue } from "@/features/tabs/awards";
import { useDrill } from "@/lib/drilldown";
import { TabState } from "@/features/tabs/ui";
import { cn } from "@/lib/utils";
import type { ChatTabProps } from "@/pages/ChatDetail/tabs";

type Award = components["schemas"]["Award"];

export default function AwardsTab({ chatId }: ChatTabProps) {
  const q = useApi("/api/chats/{chat_id}/awards", { path: { chat_id: Number(chatId) } });
  return (
    <TabState query={q}>
      {() => {
        const awards = q.data?.awards ?? [];
        if (!awards.length) return <EmptyState title="No awards for this chat yet" />;
        return (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {awards.map((a) => (
              <AwardCard key={a.key} award={a} />
            ))}
          </div>
        );
      }}
    </TabState>
  );
}

function AwardCard({ award: a }: { award: Award }) {
  const drill = useDrill();
  const clickable = Boolean(a.drilldown);
  const nobody = a.winners.length === 0;
  return (
    <section
      className={cn("card flex flex-col gap-3 p-4", nobody && "opacity-70")}
      aria-label={a.title}
    >
      <header className="flex items-start gap-3">
        <span className="text-4xl leading-none" aria-hidden="true">
          {a.emoji}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold">{a.title}</h3>
          <p className="text-xs text-muted">{a.rule}</p>
        </div>
      </header>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {nobody ? (
          <span className="text-sm text-muted">Nobody qualifies</span>
        ) : (
          a.winners.map((w) => (
            <PersonBadge
              key={w.person_id}
              name={w.name}
              id={w.person_id}
              color_index={w.color_index}
              size="md"
              showName
              className="font-medium"
            />
          ))
        )}
        {a.value != null && (
          <span className="ml-auto text-xl font-semibold tabular-nums">
            {formatAwardValue(a.value, a.unit)}
          </span>
        )}
      </div>
      {a.ranking.length > 0 && (
        <ol className="flex flex-col gap-1 border-t border-border pt-2 text-sm">
          {a.ranking.slice(0, 5).map((r, i) => (
            <li key={r.person.person_id} className="flex items-center gap-2">
              <span className="w-4 text-xs text-muted tabular-nums">{i + 1}.</span>
              <PersonBadge
                name={r.person.name}
                id={r.person.person_id}
                color_index={r.person.color_index}
                size="xs"
                showName
                className="min-w-0 flex-1"
              />
              <span className="tabular-nums text-muted">{formatAwardValue(r.value, a.unit)}</span>
            </li>
          ))}
        </ol>
      )}
      <p className="text-xs text-muted">
        {a.lower_is_better ? "Lower is better. " : ""}
        {a.eligibility}
      </p>
      {clickable && (
        <button type="button" className="btn btn-sm self-start" onClick={() => drill(a.drilldown)}>
          Show messages
        </button>
      )}
    </section>
  );
}
