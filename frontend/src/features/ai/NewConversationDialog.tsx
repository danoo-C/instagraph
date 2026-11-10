import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { CloseIcon } from "@/components/icons";
import { Toggle } from "@/features/settings/controls";
import { ScopePicker } from "@/features/ai/ScopePicker";
import { createConversation, type AiConversation, type AiStatus } from "@/features/ai/api";

/** Pick the allowed chats, name hiding and provider for a new conversation. */
export function NewConversationDialog({
  open,
  onOpenChange,
  status,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  status: AiStatus;
  onCreated: (conv: AiConversation) => void;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50" />
        <Dialog.Content className="card fixed inset-x-2 top-8 z-50 mx-auto flex max-h-[calc(100dvh-4rem)] max-w-xl flex-col gap-4 overflow-y-auto p-4">
          {open && <Body status={status} onCreated={onCreated} />}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Body({ status, onCreated }: { status: AiStatus; onCreated: (c: AiConversation) => void }) {
  const [chatIds, setChatIds] = useState<number[]>(status.default_chat_ids);
  const [hideNames, setHideNames] = useState(status.hide_names_default);
  const ready = status.providers.filter((p) => p.available);
  const [provider, setProvider] = useState(
    ready.find((p) => p.id === status.provider)?.id ?? ready[0]?.id ?? status.provider,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      onCreated(
        await createConversation({
          chat_ids: chatIds,
          hide_names: hideNames,
          provider,
          mode: "ask",
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't create the conversation.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="flex items-start justify-between gap-2">
        <div>
          <Dialog.Title className="text-base font-semibold">New conversation</Dialog.Title>
          <Dialog.Description className="text-sm text-muted">
            Choose which chats the AI may read. You can change this later.
          </Dialog.Description>
        </div>
        <Dialog.Close className="icon-btn" aria-label="Close">
          <CloseIcon />
        </Dialog.Close>
      </div>
      <ScopePicker selected={chatIds} onChange={setChatIds} />
      <Toggle
        label="Hide names from the AI"
        hint="Fixed once you send the first message."
        checked={hideNames}
        onChange={setHideNames}
      />
      {status.providers.length > 1 && (
        <label className="flex items-center justify-between gap-2 text-sm">
          <span className="font-medium">Provider</span>
          <select className="input" value={provider} onChange={(e) => setProvider(e.target.value)}>
            {status.providers.map((p) => (
              <option key={p.id} value={p.id} disabled={!p.available}>
                {p.label}
                {p.available ? "" : " (not ready)"}
              </option>
            ))}
          </select>
        </label>
      )}
      {error && <p className="text-sm text-danger">{error}</p>}
      <div className="flex justify-end gap-2">
        <Dialog.Close className="btn">Cancel</Dialog.Close>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy}
          onClick={() => void create()}
        >
          {chatIds.length === 0 ? "Start without chats" : `Start with ${chatIds.length} chats`}
        </button>
      </div>
    </>
  );
}
