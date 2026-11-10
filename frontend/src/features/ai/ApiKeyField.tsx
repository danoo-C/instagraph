import { useState } from "react";
import { refreshAi, removeApiKey, saveApiKey, testApiKey, type AiKeyInfo } from "@/features/ai/api";

/**
 * Anthropic API key: save (stored in the data directory, owner-only), test (a free call
 * that lists models) or remove. The key is never shown again after saving, only its
 * last 4 characters.
 */
export function ApiKeyField({ info }: { info: AiKeyInfo }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const run = async (fn: () => Promise<{ ok: boolean; text: string } | null>) => {
    setBusy(true);
    setNote(null);
    try {
      setNote(await fn());
    } catch (e) {
      setNote({ ok: false, text: e instanceof Error ? e.message : "Something went wrong." });
    } finally {
      setBusy(false);
      await refreshAi();
    }
  };

  const save = () =>
    run(async () => {
      await saveApiKey(key.trim());
      setKey("");
      const t = await testApiKey();
      return { ok: t.ok, text: t.ok ? "Saved. The key works." : `Saved, but: ${t.message}` };
    });
  const test = () =>
    run(async () => {
      const t = await testApiKey(key.trim() || undefined);
      return { ok: t.ok, text: t.message };
    });
  const remove = () =>
    run(async () => {
      await removeApiKey();
      return { ok: true, text: "Key removed." };
    });

  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm font-medium">Anthropic API key</p>
      {info.set ? (
        <p className="text-xs text-muted">
          Key {info.hint}{" "}
          {info.source === "env" ? "from the ANTHROPIC_API_KEY variable" : "saved on this computer"}
          .
        </p>
      ) : (
        <p className="text-xs text-muted">
          Create one at console.anthropic.com. It&apos;s stored only in your data folder and never
          shown again. With a Claude Max plan, your monthly API credits can pay for it.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="password"
          className="input min-w-0 flex-1"
          placeholder={info.set ? "Replace with a new key…" : "sk-ant-…"}
          aria-label="Anthropic API key"
          autoComplete="off"
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={busy || !key.trim()}
          onClick={() => void save()}
        >
          Save
        </button>
        <button
          type="button"
          className="btn btn-sm"
          disabled={busy || (!info.set && !key.trim())}
          onClick={() => void test()}
        >
          Test
        </button>
        {info.set && info.source === "file" && (
          <button
            type="button"
            className="btn btn-sm"
            disabled={busy}
            onClick={() => void remove()}
          >
            Remove
          </button>
        )}
      </div>
      {note && (
        <p className={note.ok ? "text-xs text-muted" : "text-xs text-danger"} role="status">
          {note.text}
        </p>
      )}
    </div>
  );
}
