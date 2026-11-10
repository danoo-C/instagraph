import { useState } from "react";
import { apiFetch } from "@/api/client";
import { Toggle } from "@/features/settings/controls";
import { ApiKeyField } from "@/features/ai/ApiKeyField";
import { refreshAi, type AiStatus } from "@/features/ai/api";
import { queryClient } from "@/api/queryClient";

/**
 * Shown while the AI is off (docs/ai-assistant.md A4.2): what is sent where, provider
 * choice, name hiding, and a one-time confirmation before turning it on.
 */
export function SetupCard({ status }: { status: AiStatus }) {
  const available = status.providers.filter((p) => p.available);
  const [provider, setProvider] = useState(
    status.providers.find((p) => p.id === status.provider)?.id ?? status.providers[0]?.id ?? "",
  );
  const [hideNames, setHideNames] = useState(status.hide_names_default);
  const [confirmed, setConfirmed] = useState(status.confirmed_notice);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chosen = status.providers.find((p) => p.id === provider);

  const turnOn = async () => {
    setBusy(true);
    setError(null);
    try {
      await apiFetch("PUT", "/api/settings", {
        body: {
          ai_enabled: true,
          ai_confirmed_notice: true,
          ai_provider: provider,
          ai_hide_names: hideNames,
        },
      });
      await Promise.all([refreshAi(), queryClient.invalidateQueries()]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't turn the AI on.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card mx-auto flex w-full max-w-2xl flex-col gap-4 p-5">
      <div>
        <h2 className="text-lg font-semibold">AI assistant</h2>
        <p className="mt-1 text-sm text-muted">
          Ask questions about your chats in plain language. The answers use the app&apos;s own
          statistics and cite real messages you can open.
        </p>
      </div>

      <div className="flex flex-col gap-2 rounded-md border border-border bg-surface-2 p-3 text-sm">
        <p className="font-medium">What gets sent, and where</p>
        <ul className="list-disc space-y-1 pl-5 text-muted">
          <li>
            This is the only part of Instagraph that sends data off your computer, and only when you
            send a message. While it&apos;s off, nothing is sent.
          </li>
          <li>
            The AI reads only the chats you allow in each conversation (none by default): statistics
            and message excerpts it asks for, sent to Anthropic.
          </li>
          <li>
            With &quot;Hide names&quot; on, people, usernames and chat titles are replaced with
            placeholders like [P3] first. Message content can still identify people.
          </li>
          <li>
            Your chats include other people&apos;s messages. They didn&apos;t choose to share them
            with an AI service, so share only what you&apos;re comfortable with.
          </li>
        </ul>
      </div>

      <div className="flex flex-col gap-2">
        <p className="text-sm font-medium">Provider</p>
        {status.providers.length === 0 ? (
          <p className="text-sm text-muted">
            No AI provider is available on this installation yet.
          </p>
        ) : (
          <div role="radiogroup" aria-label="AI provider" className="flex flex-col gap-1">
            {status.providers.map((p) => (
              <label
                key={p.id}
                className="flex cursor-pointer items-start gap-2 rounded-md border border-border p-2 text-sm hover:bg-surface-2"
              >
                <input
                  type="radio"
                  name="ai-provider"
                  className="mt-1"
                  checked={provider === p.id}
                  onChange={() => setProvider(p.id)}
                />
                <span className="flex-1">
                  <span className="font-medium">{p.label}</span>
                  <span
                    className={
                      p.available ? "block text-xs text-muted" : "block text-xs text-danger"
                    }
                  >
                    {p.available ? p.message || "Ready" : p.message || "Not ready"}
                  </span>
                </span>
              </label>
            ))}
          </div>
        )}
      </div>

      {provider === "anthropic" && <ApiKeyField info={status.api_key} />}

      <Toggle
        label="Hide names from the AI"
        hint="Default for new conversations. You can choose per conversation before its first message."
        checked={hideNames}
        onChange={setHideNames}
      />

      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          className="mt-1"
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
        />
        <span>I understand what is sent and where.</span>
      </label>

      {error && <p className="text-sm text-danger">{error}</p>}
      <div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || !confirmed || !chosen?.available}
          onClick={() => void turnOn()}
        >
          {busy ? "Turning on…" : "Turn on AI"}
        </button>
        {available.length === 0 && status.providers.length > 0 && (
          <p className="mt-2 text-xs text-muted">The selected provider isn&apos;t ready yet.</p>
        )}
      </div>
    </section>
  );
}
