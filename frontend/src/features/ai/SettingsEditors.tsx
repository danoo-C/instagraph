import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { apiFetch } from "@/api/client";
import { queryClient } from "@/api/queryClient";
import { useApi } from "@/api/useApi";
import type { components } from "@/api/schema";
import { CloseIcon } from "@/components/icons";
import { Field } from "@/features/settings/controls";
import type { SectionProps } from "@/features/settings/identity";
import { ScopePicker } from "@/features/ai/ScopePicker";

type PeopleList = components["schemas"]["PeopleListResponse"];

/** Default allowed chats for new conversations; saved right away (by chat id). */
export function DefaultChatsField({ chatIds }: { chatIds: number[] }) {
  const [open, setOpen] = useState(false);
  return (
    <Field
      label="Default chats for new conversations"
      hint="None by default. Each conversation can change its own chats."
    >
      <div className="flex items-center gap-2">
        <span className="text-sm">
          {chatIds.length} chat{chatIds.length === 1 ? "" : "s"}
        </span>
        <button type="button" className="btn btn-sm" onClick={() => setOpen(true)}>
          Choose…
        </button>
      </div>
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50" />
          <Dialog.Content className="card fixed inset-x-2 top-8 z-50 mx-auto flex max-w-xl flex-col gap-3 p-4">
            <div className="flex items-start justify-between gap-2">
              <Dialog.Title className="text-base font-semibold">Default chats</Dialog.Title>
              <Dialog.Close className="icon-btn" aria-label="Close">
                <CloseIcon />
              </Dialog.Close>
            </div>
            <Dialog.Description className="sr-only">
              Chats new AI conversations may read by default.
            </Dialog.Description>
            {open && <DefaultChatsBody initial={chatIds} onDone={() => setOpen(false)} />}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </Field>
  );
}

function DefaultChatsBody({ initial, onDone }: { initial: number[]; onDone: () => void }) {
  const [ids, setIds] = useState(initial);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await apiFetch("PUT", "/api/ai/default-scope", { body: { chat_ids: ids } });
      await queryClient.invalidateQueries();
      onDone();
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <ScopePicker selected={ids} onChange={setIds} />
      <div className="flex justify-end gap-2">
        <Dialog.Close className="btn">Cancel</Dialog.Close>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy}
          onClick={() => void save()}
        >
          Save
        </button>
      </div>
    </>
  );
}

/**
 * Extra names to hide (nicknames, short forms) per person, and people whose names
 * shouldn't be matched inside message text (names that are also common words).
 * Keys are display names. Saved with the Settings save bar.
 */
export function NicknamesField({ s, update }: Pick<SectionProps, "s" | "update">) {
  const people = useApi<PeopleList>(
    "/api/people",
    { query: { limit: 500, sort: "exchanged", include_requests: true } },
    { filters: false },
  );
  const aliases = s.ai_person_aliases ?? {};
  const disabled = s.ai_alias_disabled ?? [];
  const names = [...new Set([...Object.keys(aliases), ...disabled])].sort((a, b) =>
    a.localeCompare(b),
  );
  const options = (people.data?.items ?? [])
    .map((p) => p.person.name)
    .filter((n) => !names.includes(n));
  const [person, setPerson] = useState("");
  const [text, setText] = useState("");

  const setAliases = (name: string, list: string[]) => {
    const next = { ...aliases };
    if (list.length) next[name] = list;
    else delete next[name];
    update({ ai_person_aliases: next });
  };
  const setDisabled = (name: string, off: boolean) =>
    update({
      ai_alias_disabled: off ? [...disabled, name] : disabled.filter((n) => n !== name),
    });
  const remove = (name: string) =>
    update({
      ai_person_aliases: Object.fromEntries(Object.entries(aliases).filter(([k]) => k !== name)),
      ai_alias_disabled: disabled.filter((n) => n !== name),
    });
  const split = (v: string) =>
    v
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);

  return (
    <Field
      label="Nicknames to hide"
      hint="Extra names hidden for a person when names are hidden (e.g. Janka, Jaja). Tick “not in text” for names that are also normal words."
    >
      <div className="flex flex-col gap-2">
        {names.map((name) => (
          <div
            key={`${name}:${(aliases[name] ?? []).join(",")}`}
            className="flex flex-wrap items-center gap-2 text-sm"
          >
            <span className="w-40 truncate font-medium" title={name}>
              {name}
            </span>
            <input
              className="input min-w-0 flex-1"
              aria-label={`Nicknames for ${name}`}
              placeholder="nickname, short form"
              defaultValue={(aliases[name] ?? []).join(", ")}
              onBlur={(e) => setAliases(name, split(e.target.value))}
            />
            <label className="flex items-center gap-1 text-xs text-muted">
              <input
                type="checkbox"
                checked={disabled.includes(name)}
                onChange={(e) => setDisabled(name, e.target.checked)}
              />
              not in text
            </label>
            <button
              type="button"
              className="icon-btn"
              aria-label={`Remove ${name}`}
              onClick={() => remove(name)}
            >
              ×
            </button>
          </div>
        ))}
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="input w-40"
            aria-label="Person"
            value={person}
            onChange={(e) => setPerson(e.target.value)}
          >
            <option value="">Add a person…</option>
            {options.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          <input
            className="input min-w-0 flex-1"
            placeholder="nickname, short form"
            aria-label="New nicknames"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button
            type="button"
            className="btn btn-sm"
            disabled={!person || !split(text).length}
            onClick={() => {
              setAliases(person, split(text));
              setPerson("");
              setText("");
            }}
          >
            Add
          </button>
        </div>
      </div>
    </Field>
  );
}
