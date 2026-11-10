import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import { queryClient } from "@/api/queryClient";
import { apiQueryKey, useApi } from "@/api/useApi";
import { ErrorCard } from "@/components/ErrorCard";
import { Skeleton } from "@/components/Skeleton";
import type { Schemas } from "@/features/people/apiTypes";
import {
  diffSettings,
  touchesDerived,
  type AppSettings,
  type SettingsPatch,
} from "@/features/settings/diff";
import { OwnerSection, PeopleMergeSection, ThreadMergeSection } from "@/features/settings/identity";
import {
  AppearanceSection,
  DangerSection,
  EmojiSection,
  ExportSection,
  LocalesSection,
  MediaSection,
  PrivacySection,
  TextSection,
  TimeSection,
} from "@/features/settings/sections";
import { AiSettingsSection } from "@/features/ai/SettingsSection";
import { cn } from "@/lib/utils";

type SettingsResponse = Schemas["SettingsResponse"];

const TOC = [
  ["identity", "Owner"],
  ["people", "People"],
  ["threads", "Chats"],
  ["time", "Time"],
  ["locales", "Languages"],
  ["media", "Media"],
  ["text", "Words"],
  ["emoji", "Emojis"],
  ["privacy", "Anonymize"],
  ["ai", "AI"],
  ["appearance", "Appearance"],
  ["export", "Export"],
  ["danger", "Clear data"],
] as const;

export default function SettingsPage() {
  const q = useApi("/api/settings", undefined, { filters: false });
  // Local edits on top of the server document (only changed keys).
  const [edits, setEdits] = useState<SettingsPatch>({});
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const save = useMutation({
    mutationFn: (patch: SettingsPatch) =>
      apiFetch<SettingsResponse>("PUT", "/api/settings", { body: patch }),
    onMutate: () => setNotice(null),
    onSuccess: (res, patch) => {
      queryClient.setQueryData(apiQueryKey("/api/settings", undefined, {}), res);
      setEdits((prev) => {
        const next = { ...prev } as Record<string, unknown>;
        for (const k of Object.keys(patch)) delete next[k];
        return next as SettingsPatch;
      });
      setNotice({
        kind: "ok",
        text: res.rederived
          ? "Saved. Statistics were recomputed with the new settings."
          : "Settings saved.",
      });
      void queryClient.invalidateQueries();
    },
    onError: (err) =>
      setNotice({ kind: "error", text: err instanceof Error ? err.message : "Saving failed." }),
  });

  if (q.error) {
    return (
      <div className="page">
        <h1 className="page-title">Settings</h1>
        <ErrorCard error={q.error} onRetry={() => void q.refetch()} />
      </div>
    );
  }
  if (!q.data) {
    return (
      <div className="page">
        <h1 className="page-title">Settings</h1>
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-40 w-full" />
        ))}
      </div>
    );
  }

  const server: AppSettings = q.data.settings;
  const redacted = q.data.redacted;
  const effective: AppSettings = { ...server, ...edits };
  const patch = diffSettings(server, effective);
  const dirty = Object.keys(patch).length > 0;
  const update = (p: SettingsPatch) => setEdits((prev) => ({ ...prev, ...p }));
  const saveNow = (p: SettingsPatch) => save.mutate(p);
  const recomputing = save.isPending && save.variables != null && touchesDerived(save.variables);
  const props = { s: effective, update, redacted };

  return (
    <div className="page max-w-4xl pb-28">
      <div>
        <h1 className="page-title">Settings</h1>
        <p className="text-sm text-muted">
          Stored in the data directory on this computer, so they survive restarts.
        </p>
      </div>
      {redacted && (
        <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-sm">
          🔒 Anonymize mode is on. Settings that contain real names are hidden; they are kept
          unchanged when you save.
        </p>
      )}
      <nav aria-label="Settings sections" className="flex flex-wrap gap-1 text-xs" data-print-hide>
        {TOC.map(([id, label]) => (
          <a
            key={id}
            href={`#${id}`}
            className="rounded-full bg-surface-2 px-2.5 py-1 text-muted hover:text-fg"
          >
            {label}
          </a>
        ))}
      </nav>

      <OwnerSection {...props} />
      <PeopleMergeSection {...props} />
      <ThreadMergeSection {...props} />
      <TimeSection {...props} />
      <LocalesSection {...props} />
      <MediaSection />
      <TextSection {...props} />
      <EmojiSection {...props} />
      <PrivacySection
        anonymize={server.anonymize}
        busy={save.isPending}
        onChange={(v) => saveNow({ anonymize: v })}
      />
      <AiSettingsSection {...props} />
      <AppearanceSection saveNow={saveNow} />
      <ExportSection anonymize={server.anonymize} />
      <DangerSection />

      {(dirty || save.isPending || notice) && (
        <div
          className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface/95 px-4 py-3 shadow-lg backdrop-blur md:left-56"
          role="region"
          aria-label="Unsaved changes"
        >
          {recomputing && (
            <div
              className="absolute inset-x-0 top-0 h-1 animate-pulse bg-accent"
              aria-hidden="true"
            />
          )}
          <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-3">
            <p
              className={cn(
                "flex-1 text-sm",
                notice?.kind === "error" && !save.isPending && "text-danger",
              )}
              role="status"
              aria-live="polite"
            >
              {save.isPending
                ? recomputing
                  ? "Recomputing statistics… this can take a moment."
                  : "Saving…"
                : dirty
                  ? `${Object.keys(patch).length} unsaved change(s)${touchesDerived(patch) ? " — saving will recompute statistics" : ""}.`
                  : notice?.text}
            </p>
            {dirty && (
              <>
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={save.isPending}
                  onClick={() => setEdits({})}
                >
                  Discard
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  disabled={save.isPending}
                  onClick={() => save.mutate(patch)}
                >
                  Save changes
                </button>
              </>
            )}
            {!dirty && !save.isPending && notice && (
              <button
                type="button"
                className="icon-btn"
                aria-label="Dismiss"
                onClick={() => setNotice(null)}
              >
                ×
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
