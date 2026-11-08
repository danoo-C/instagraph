import { useMemo, useState } from "react";
import { useApi } from "@/api/useApi";
import { PersonBadge } from "@/components/PersonBadge";
import { Skeleton } from "@/components/Skeleton";
import { SearchSelect, type SearchOption } from "@/features/compare/SearchSelect";
import { RedactedNote, Section } from "@/features/settings/controls";
import { isRedactedValue, type AppSettings, type SettingsPatch } from "@/features/settings/diff";
import { formatNumber } from "@/lib/format";

export interface SectionProps {
  s: AppSettings;
  update: (patch: SettingsPatch) => void;
  redacted: boolean;
}

export function OwnerSection({ s, update, redacted }: SectionProps) {
  const people = useApi("/api/settings/people", undefined, { filters: false });
  const items = useMemo(() => people.data?.items ?? [], [people.data]);
  const detected = items.find((p) => p.person.is_owner);
  const chosen =
    s.owner_person_id != null
      ? items.find((p) => p.person.person_id === s.owner_person_id)
      : undefined;
  const current = chosen ?? detected;
  const options: SearchOption[] = items.map((p) => ({
    value: p.person.person_id,
    label: p.display_name,
    hint: `${formatNumber(p.message_count)} msgs`,
    icon: (
      <PersonBadge
        name={p.display_name}
        id={p.person.person_id}
        color_index={p.person.color_index}
        size="xs"
      />
    ),
  }));
  return (
    <Section
      id="identity"
      title="Owner identity"
      description="Who “me” is. Detected automatically from the export; override it if the detection picked the wrong person. Changing it recomputes all statistics."
    >
      {people.isPending ? (
        <Skeleton className="h-9 w-72" />
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <span className="inline-flex items-center gap-2 text-sm">
            {current && (
              <PersonBadge
                name={current.display_name}
                id={current.person.person_id}
                color_index={current.person.color_index}
                size="sm"
              />
            )}
            <span className="font-medium">{current?.display_name ?? "Unknown"}</span>
            <span className="text-xs text-muted">
              {s.owner_person_id != null || s.owner_name ? "(manual override)" : "(auto-detected)"}
            </span>
          </span>
          <SearchSelect
            className="w-64"
            label="Choose the owner"
            placeholder="Choose another person…"
            options={options}
            onSelect={(id) => {
              const p = items.find((x) => x.person.person_id === id);
              update({
                owner_person_id: id,
                // The name survives re-imports; not sent while names are anonymized.
                ...(redacted ? {} : { owner_name: p?.display_name ?? null }),
              });
            }}
          />
          {(s.owner_person_id != null || s.owner_name) && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => update({ owner_person_id: null, owner_name: null })}
            >
              Use auto-detected
            </button>
          )}
        </div>
      )}
      {redacted && isRedactedValue(s.owner_name) && <RedactedNote what="The saved owner name is" />}
    </Section>
  );
}

function Chips({ items, onRemove }: { items: string[]; onRemove: (i: number) => void }) {
  return (
    <ul className="flex flex-wrap gap-1.5">
      {items.map((n, i) => (
        <li
          key={`${n}-${i}`}
          className="inline-flex items-center gap-1 rounded-full bg-surface-2 py-0.5 pl-2 pr-1 text-xs"
        >
          {n}
          <button
            type="button"
            className="icon-btn h-4 w-4"
            aria-label={`Remove ${n}`}
            onClick={() => onRemove(i)}
          >
            ×
          </button>
        </li>
      ))}
    </ul>
  );
}

export function PeopleMergeSection({ s, update, redacted }: SectionProps) {
  const people = useApi("/api/settings/people", undefined, { filters: false });
  const items = people.data?.items ?? [];
  const [names, setNames] = useState<string[]>([]);
  const [displayName, setDisplayName] = useState<string>("");
  const merges = s.people_merges ?? [];
  const nameOptions: SearchOption<string>[] = [];
  for (const p of items) {
    for (const n of [p.display_name, ...p.aliases]) {
      if (!nameOptions.some((o) => o.value === n))
        nameOptions.push({
          value: n,
          label: n,
          hint:
            n === p.display_name
              ? `${formatNumber(p.message_count)} msgs`
              : `alias of ${p.display_name}`,
          disabled: names.includes(n),
        });
    }
  }
  const aliased = items.filter((p) => p.aliases.length > 0);

  return (
    <Section
      id="people"
      title="Merge / split people"
      description="Treat several names as one person (e.g. after a name change). Removing a rule splits them again. Changes recompute statistics."
    >
      {redacted ? (
        <>
          <p className="text-sm">{merges.length} merge rule(s) saved.</p>
          <RedactedNote what="People merge rules are" />
        </>
      ) : (
        <>
          {merges.length > 0 ? (
            <ul className="flex flex-col gap-2">
              {merges.map((m, i) => (
                <li
                  key={i}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-border px-3 py-2 text-sm"
                >
                  <span className="font-medium">
                    {m.display_name ?? m.names[m.names.length - 1]}
                  </span>
                  <span className="text-muted">←</span>
                  <span className="min-w-0 flex-1 text-muted">{m.names.join(", ")}</span>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => update({ people_merges: merges.filter((_, j) => j !== i) })}
                  >
                    Split
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">No manual merges.</p>
          )}
          <div className="flex flex-col gap-2 rounded-md border border-dashed border-border p-3">
            <span className="text-sm font-medium">New merge</span>
            <SearchSelect<string>
              className="w-72"
              label="Add a name to merge"
              placeholder="Add a name…"
              options={nameOptions}
              onSelect={(n) => setNames((prev) => (prev.includes(n) ? prev : [...prev, n]))}
            />
            {names.length > 0 && (
              <Chips items={names} onRemove={(i) => setNames(names.filter((_, j) => j !== i))} />
            )}
            {names.length >= 2 && (
              <label className="flex flex-wrap items-center gap-2 text-sm">
                Show as
                <select
                  className="input h-8"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                >
                  <option value="">Most recent name</option>
                  {names.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={names.length < 2}
                onClick={() => {
                  update({
                    people_merges: [...merges, { names, display_name: displayName || null }],
                  });
                  setNames([]);
                  setDisplayName("");
                }}
              >
                Add merge
              </button>
            </div>
          </div>
          {aliased.length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer text-muted">
                Names merged automatically ({aliased.length})
              </summary>
              <ul className="mt-2 flex flex-col gap-1">
                {aliased.map((p) => (
                  <li key={p.person.person_id}>
                    <span className="font-medium">{p.display_name}</span>
                    <span className="text-muted"> — also known as {p.aliases.join(", ")}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </Section>
  );
}

export function ThreadMergeSection({ s, update, redacted }: SectionProps) {
  const threads = useApi("/api/settings/threads", undefined, { filters: false });
  const items = threads.data?.items ?? [];
  const byFolder = new Map(items.filter((t) => t.folder).map((t) => [t.folder!, t]));
  const [kind, setKind] = useState<"merge" | "split">("merge");
  const [folders, setFolders] = useState<string[]>([]);
  const merges = s.thread_merges ?? [];
  const splits = s.thread_splits ?? [];
  const label = (f: string) => {
    const t = byFolder.get(f);
    return t ? `${t.title} (${f})` : f;
  };
  const options: SearchOption<string>[] = items
    .filter((t) => t.folder)
    .map((t) => ({
      value: t.folder!,
      label: t.title,
      hint: `${formatNumber(t.message_count)} msgs · ${t.folder}`,
      disabled: folders.includes(t.folder!),
    }));
  const rules = [
    ...merges.map((m, i) => ({ kind: "merge" as const, folders: m.folders, i })),
    ...splits.map((m, i) => ({ kind: "split" as const, folders: m.folders, i })),
  ];

  return (
    <Section
      id="threads"
      title="Merge / split chats"
      description="Join chat folders that belong together (e.g. a chat split across exports), or keep chats apart that were merged automatically. Changes recompute statistics."
    >
      {redacted ? (
        <>
          <p className="text-sm">
            {merges.length} merge and {splits.length} split rule(s) saved.
          </p>
          <RedactedNote what="Chat merge rules are" />
        </>
      ) : (
        <>
          {rules.length > 0 ? (
            <ul className="flex flex-col gap-2">
              {rules.map((r) => (
                <li
                  key={`${r.kind}-${r.i}`}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-border px-3 py-2 text-sm"
                >
                  <span className="rounded bg-surface-2 px-1.5 text-xs font-medium uppercase">
                    {r.kind === "merge" ? "Merged" : "Kept apart"}
                  </span>
                  <span className="min-w-0 flex-1">{r.folders.map(label).join(" + ")}</span>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() =>
                      r.kind === "merge"
                        ? update({ thread_merges: merges.filter((_, j) => j !== r.i) })
                        : update({ thread_splits: splits.filter((_, j) => j !== r.i) })
                    }
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">No manual chat rules.</p>
          )}
          <div className="flex flex-col gap-2 rounded-md border border-dashed border-border p-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium">New rule:</span>
              <select
                className="input h-8"
                value={kind}
                onChange={(e) => setKind(e.target.value as "merge" | "split")}
              >
                <option value="merge">Merge these chats</option>
                <option value="split">Keep these chats apart</option>
              </select>
            </div>
            <SearchSelect<string>
              className="w-80"
              label="Add a chat"
              placeholder="Add a chat…"
              options={options}
              onSelect={(f) => setFolders((prev) => (prev.includes(f) ? prev : [...prev, f]))}
            />
            {folders.length > 0 && (
              <Chips
                items={folders.map(label)}
                onRemove={(i) => setFolders(folders.filter((_, j) => j !== i))}
              />
            )}
            <div>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={folders.length < 2}
                onClick={() => {
                  if (kind === "merge") update({ thread_merges: [...merges, { folders }] });
                  else update({ thread_splits: [...splits, { folders }] });
                  setFolders([]);
                }}
              >
                Add rule
              </button>
            </div>
          </div>
        </>
      )}
    </Section>
  );
}
