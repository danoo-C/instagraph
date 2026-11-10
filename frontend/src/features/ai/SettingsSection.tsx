import { useState } from "react";
import { Link } from "react-router-dom";
import { Field, Section, Toggle, ConfirmDialog } from "@/features/settings/controls";
import type { SectionProps } from "@/features/settings/identity";
import { deleteAllConversations, refreshAi, useAiStatus } from "@/features/ai/api";
import { ApiKeyField } from "@/features/ai/ApiKeyField";

const EFFORTS = ["low", "medium", "high"] as const;

/** Settings → AI assistant (docs/ai-assistant.md A4.5). Off by default. */
export function AiSettingsSection({ s, update }: SectionProps) {
  const status = useAiStatus();
  const providers = status.data?.providers ?? [];
  const provider = providers.find((p) => p.id === s.ai_provider);
  const models = provider?.models ?? [];
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleted, setDeleted] = useState<string | null>(null);

  return (
    <Section
      id="ai"
      title="AI assistant"
      description={
        <>
          Optional and off by default. While it&apos;s off, nothing is sent anywhere. See the{" "}
          <Link to="/ai" className="underline">
            AI page
          </Link>{" "}
          for what is sent and where.
        </>
      }
    >
      <Toggle
        label="AI assistant on"
        hint={
          s.ai_confirmed_notice
            ? undefined
            : "Turn it on from the AI page the first time, after reading what is sent."
        }
        checked={s.ai_enabled}
        disabled={!s.ai_confirmed_notice && !s.ai_enabled}
        onChange={(v) => update({ ai_enabled: v })}
      />
      <Field label="Provider" htmlFor="ai-provider">
        <select
          id="ai-provider"
          className="input"
          value={s.ai_provider}
          onChange={(e) => update({ ai_provider: e.target.value })}
        >
          {providers.length === 0 && <option value={s.ai_provider}>None available</option>}
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
              {p.available ? "" : " (not ready)"}
            </option>
          ))}
        </select>
        {provider && !provider.available && (
          <p className="text-xs text-danger">{provider.message || "Not ready."}</p>
        )}
      </Field>
      {s.ai_provider === "anthropic" && status.data && <ApiKeyField info={status.data.api_key} />}
      {models.length > 0 && (
        <Field label="Model" htmlFor="ai-model">
          <select
            id="ai-model"
            className="input"
            value={models.includes(s.ai_model) ? s.ai_model : models[0]}
            onChange={(e) => update({ ai_model: e.target.value })}
          >
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="Effort" hint="Higher effort thinks longer and uses more tokens.">
        <div role="radiogroup" aria-label="Effort" className="flex flex-wrap gap-1">
          {EFFORTS.map((e) => (
            <button
              key={e}
              type="button"
              role="radio"
              aria-checked={s.ai_effort === e}
              className={s.ai_effort === e ? "btn btn-sm btn-primary" : "btn btn-sm"}
              onClick={() => update({ ai_effort: e })}
            >
              {e}
            </button>
          ))}
        </div>
      </Field>
      <Toggle
        label="Hide names from the AI (default for new conversations)"
        hint="Replaces people, usernames and chat titles with placeholders like [P3] before anything is sent. Message content can still identify people."
        checked={s.ai_hide_names}
        onChange={(v) => update({ ai_hide_names: v })}
      />
      {s.ai_enabled && (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn btn-sm" onClick={() => setConfirmDelete(true)}>
            Delete all AI conversations
          </button>
          {deleted && <span className="text-xs text-muted">{deleted}</span>}
        </div>
      )}
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete all AI conversations?"
        description="Saved AI conversations are removed from this computer. This can't be undone."
        confirmLabel="Delete"
        danger
        onConfirm={() => {
          setConfirmDelete(false);
          void deleteAllConversations().then(async (r) => {
            setDeleted(`Deleted ${(r as { deleted: number }).deleted} conversation(s).`);
            await refreshAi();
          });
        }}
      />
    </Section>
  );
}
