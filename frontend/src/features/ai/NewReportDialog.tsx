import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useApi } from "@/api/useApi";
import type { components } from "@/api/schema";
import { CloseIcon } from "@/components/icons";
import { Toggle } from "@/features/settings/controls";
import { ScopePicker } from "@/features/ai/ScopePicker";
import { REPORTS, reportScope, type ReportInput, type ReportTemplate } from "@/features/ai/reports";
import {
  createConversation,
  useAllChats,
  type AiConversation,
  type AiStatus,
} from "@/features/ai/api";
import { cn } from "@/lib/utils";

type WrappedYears = components["schemas"]["WrappedYearsResponse"];

/** Pick a report, its chat or year, review the prompt, then start it (A7). */
export function NewReportDialog({
  open,
  onOpenChange,
  status,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  status: AiStatus;
  /** Called with the new conversation and the prompt to send right away. */
  onCreated: (conv: AiConversation, prompt: string) => void;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50" />
        <Dialog.Content className="card fixed inset-x-2 top-8 z-50 mx-auto flex max-h-[calc(100dvh-4rem)] max-w-xl flex-col gap-4 overflow-y-auto p-4">
          <div className="flex items-start justify-between gap-2">
            <div>
              <Dialog.Title className="text-base font-semibold">New report</Dialog.Title>
              <Dialog.Description className="text-sm text-muted">
                A ready-made question. You can edit it before it&apos;s sent, and ask follow-ups
                afterwards.
              </Dialog.Description>
            </div>
            <Dialog.Close className="icon-btn" aria-label="Close">
              <CloseIcon />
            </Dialog.Close>
          </div>
          {open && <Body status={status} onCreated={onCreated} />}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Body({
  status,
  onCreated,
}: {
  status: AiStatus;
  onCreated: (conv: AiConversation, prompt: string) => void;
}) {
  const [template, setTemplate] = useState<ReportTemplate | null>(null);
  const [chatId, setChatId] = useState<number | null>(null);
  const [year, setYear] = useState<number | null>(null);
  const [chosen, setChosen] = useState<number[]>(status.default_chat_ids);
  const [hideNames, setHideNames] = useState(status.hide_names_default);
  const [edited, setEdited] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chats = useAllChats();
  const years = useApi<WrappedYears>("/api/wrapped/years", undefined, { filters: false });

  const options = (chats.data?.items ?? []).filter(
    (c) => template?.need !== "group" || c.kind === "group",
  );
  const chat = options.find((c) => c.thread_id === chatId);
  const yearList = (years.data?.years ?? []).map((y) => y.year).sort((a, b) => b - a);
  const pickedYear = year ?? yearList[0];
  const input: ReportInput = {
    chat: chat ? { id: chat.thread_id, title: chat.title } : undefined,
    year: pickedYear,
  };
  const needsChat = template?.need === "chat" || template?.need === "group";
  const ready = template != null && (!needsChat || chat != null);
  const prompt = edited ?? (template ? template.prompt(input) : "");
  const scope = template ? reportScope(template, input, chosen) : [];
  const provider =
    status.providers.find((p) => p.id === status.provider && p.available)?.id ??
    status.providers.find((p) => p.available)?.id ??
    status.provider;

  const pick = (t: ReportTemplate) => {
    setTemplate(t);
    setChatId(null);
    setEdited(null);
  };

  const start = async () => {
    if (!template || !ready) return;
    setBusy(true);
    setError(null);
    try {
      const conv = await createConversation({
        mode: "report",
        title: template.label(input),
        chat_ids: scope,
        hide_names: hideNames,
        provider,
      });
      onCreated(conv, prompt.trim());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't start the report.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div role="radiogroup" aria-label="Report" className="grid gap-2 sm:grid-cols-2">
        {REPORTS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="radio"
            aria-checked={template?.id === t.id}
            className={cn(
              "rounded-md border p-2 text-left text-sm hover:bg-surface-2",
              template?.id === t.id ? "border-accent bg-accent/5" : "border-border",
            )}
            onClick={() => pick(t)}
          >
            <span className="block font-medium">{t.title}</span>
            <span className="block text-xs text-muted">{t.description}</span>
          </button>
        ))}
      </div>

      {template && needsChat && (
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{template.need === "group" ? "Group chat" : "Chat"}</span>
          <select
            className="input"
            value={chatId ?? ""}
            onChange={(e) => {
              setChatId(e.target.value ? Number(e.target.value) : null);
              setEdited(null);
            }}
          >
            <option value="">Choose…</option>
            {options.map((c) => (
              <option key={c.thread_id} value={c.thread_id}>
                {c.title}
              </option>
            ))}
          </select>
          <span className="text-xs text-muted">The AI can read only this chat.</span>
        </label>
      )}

      {template?.need === "year" && (
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">Year</span>
          <select
            className="input"
            value={pickedYear ?? ""}
            onChange={(e) => {
              setYear(Number(e.target.value));
              setEdited(null);
            }}
          >
            {yearList.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </label>
      )}

      {template && !needsChat && <ScopePicker selected={chosen} onChange={setChosen} />}

      {template && (
        <>
          <Toggle
            label="Hide names from the AI"
            hint="Fixed for this report and its follow-ups."
            checked={hideNames}
            onChange={setHideNames}
          />
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">Question</span>
            <textarea
              className="input min-h-[6rem] resize-y"
              value={prompt}
              onChange={(e) => setEdited(e.target.value)}
            />
          </label>
        </>
      )}

      {error && <p className="text-sm text-danger">{error}</p>}
      <div className="flex justify-end gap-2">
        <Dialog.Close className="btn">Cancel</Dialog.Close>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || !ready || !prompt.trim()}
          onClick={() => void start()}
        >
          Start report
        </button>
      </div>
    </>
  );
}
