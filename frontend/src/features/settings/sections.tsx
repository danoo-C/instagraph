import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { apiFetch } from "@/api/client";
import { queryClient } from "@/api/queryClient";
import { useApi } from "@/api/useApi";
import { SearchSelect } from "@/features/compare/SearchSelect";
import type { Schemas } from "@/features/people/apiTypes";
import {
  ConfirmDialog,
  Field,
  LinesField,
  NumberField,
  RedactedNote,
  Section,
  Toggle,
} from "@/features/settings/controls";
import { DEFAULT_LAUGHTER_PATTERNS, exportHref } from "@/features/settings/constants";
import { formatBytes, invalidPatterns, setLangList, timeZones } from "@/features/settings/diff";
import type { SectionProps } from "@/features/settings/identity";
import { useI18nStore, type Language } from "@/i18n";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import { browserTimeZone, useFilters } from "@/state/filters";
import { useThemeStore, type ThemePreference } from "@/state/theme";

const LANGS: Array<{ code: string; name: string }> = [
  { code: "en", name: "English" },
  { code: "sk", name: "Slovak" },
  { code: "cs", name: "Czech" },
];
const LOCALE_NAMES: Record<string, string> = {
  en: "English",
  sk: "Slovak",
  cs: "Czech",
  de: "German",
  es: "Spanish",
  fr: "French",
  pl: "Polish",
  hu: "Hungarian",
};

// ------------------------------------------------------------------ time

export function TimeSection({ s, update }: SectionProps) {
  const zones = useMemo(() => timeZones(), []);
  return (
    <Section
      id="time"
      title="Time"
      description="Default time zone and the thresholds used for sessions and replies (§6.4). Session and reply changes recompute statistics."
    >
      <Field
        label="Time zone"
        hint={`Used when no time zone is chosen in the filter bar. Your browser: ${browserTimeZone()}.`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded bg-surface-2 px-2 py-1 text-sm">
            {s.timezone ?? "Not set (UTC)"}
          </span>
          <SearchSelect<string>
            className="w-64"
            label="Search time zones"
            placeholder="Search time zones…"
            options={zones.map((z) => ({ value: z, label: z }))}
            onSelect={(z) => update({ timezone: z })}
          />
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => update({ timezone: browserTimeZone() })}
          >
            Use browser zone
          </button>
          {s.timezone && (
            <button type="button" className="btn btn-sm" onClick={() => update({ timezone: null })}>
              Clear
            </button>
          )}
        </div>
      </Field>
      <div className="flex flex-wrap gap-6">
        <NumberField
          label="Session gap"
          hint="A silence longer than this starts a new conversation session. Default 60."
          value={s.session_gap_min}
          onChange={(v) => update({ session_gap_min: Math.round(v) })}
          min={1}
          max={7 * 24 * 60}
          suffix="minutes"
        />
        <NumberField
          label="Reply window"
          hint="Answers later than this don't count as replies. Default 12."
          value={s.reply_window_h}
          onChange={(v) => update({ reply_window_h: v })}
          min={0.1}
          max={24 * 30}
          step={0.5}
          suffix="hours"
        />
      </div>
    </Section>
  );
}

// ------------------------------------------------------------------ system-message locales

export function LocalesSection({ s, update }: SectionProps) {
  const report = useApi("/api/import/report", undefined, { filters: false });
  const info = report.data?.locales;
  const detected = info?.detected ?? {};
  const manual = s.enabled_locales != null;
  const enabled = s.enabled_locales ?? info?.enabled ?? [];
  const codes = Array.from(
    new Set([...Object.keys(detected), "en", "sk", "cs", ...(s.enabled_locales ?? [])]),
  ).sort((a, b) => (detected[b] ?? 0) - (detected[a] ?? 0) || a.localeCompare(b));
  return (
    <Section
      id="locales"
      title="System-message languages"
      description="Instagram writes system messages (“You named the group…”, “Liked a message”) in the export's language. Languages are detected automatically; switch to manual to choose them yourself."
    >
      <div
        role="radiogroup"
        aria-label="Language detection"
        className="flex flex-wrap gap-4 text-sm"
      >
        <label className="inline-flex items-center gap-2">
          <input
            type="radio"
            checked={!manual}
            onChange={() => update({ enabled_locales: null })}
          />
          Automatic
          {info && (
            <span className="text-xs text-muted">
              (now: {(info.auto_enabled ?? []).join(", ") || "none"})
            </span>
          )}
        </label>
        <label className="inline-flex items-center gap-2">
          <input
            type="radio"
            checked={manual}
            onChange={() => update({ enabled_locales: [...enabled] })}
          />
          Manual
        </label>
      </div>
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {codes.map((code) => (
          <li key={code}>
            <label
              className={cn(
                "flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm",
                !manual && "opacity-70",
              )}
            >
              <input
                type="checkbox"
                disabled={!manual}
                checked={enabled.includes(code)}
                onChange={(e) =>
                  update({
                    enabled_locales: e.target.checked
                      ? [...enabled, code]
                      : enabled.filter((c) => c !== code),
                  })
                }
              />
              <span className="flex-1">
                {LOCALE_NAMES[code] ?? code} <span className="text-xs text-muted">({code})</span>
              </span>
              <span
                className="text-xs tabular-nums text-muted"
                title="Matches found in a sample of the import"
              >
                {formatNumber(detected[code] ?? 0)} detected
              </span>
            </label>
          </li>
        ))}
      </ul>
      {info && info.sample_size > 0 && (
        <p className="text-xs text-muted">
          Detection sampled {formatNumber(info.sample_size)} messages.
        </p>
      )}
    </Section>
  );
}

// ------------------------------------------------------------------ media

export function MediaSection() {
  const report = useApi("/api/import/report", undefined, { filters: false });
  const [open, setOpen] = useState(false);
  const del = useMutation({
    mutationFn: () => apiFetch<Schemas["DeleteUploadsResponse"]>("DELETE", "/api/import/uploads"),
    onSuccess: () => {
      setOpen(false);
      void queryClient.invalidateQueries();
    },
  });
  const zips = report.data?.zip_files ?? [];
  return (
    <Section
      id="media"
      title="Media"
      description="Uploaded zips are kept so photos and videos can be shown from them. Deleting them keeps every statistic, but media from zip imports can no longer be viewed."
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm">
          {zips.length
            ? `${zips.length} uploaded zip file(s) in the last import.`
            : "No uploaded zips recorded in the last import."}
        </span>
        <button type="button" className="btn btn-sm" onClick={() => setOpen(true)}>
          Delete uploaded zips
        </button>
      </div>
      {del.data && (
        <p className="text-sm text-muted" role="status">
          Deleted {formatNumber(del.data.deleted_files)} file(s), freed{" "}
          {formatBytes(del.data.freed_bytes)}.
        </p>
      )}
      {del.error && <p className="text-sm text-danger">{del.error.message}</p>}
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Delete uploaded zips?"
        description="Statistics stay. Photos, videos and voice messages from zip uploads will no longer be viewable. This cannot be undone."
        confirmLabel="Delete zips"
        danger
        busy={del.isPending}
        onConfirm={() => del.mutate()}
      />
    </Section>
  );
}

// ------------------------------------------------------------------ text

export function TextSection({ s, update, redacted }: SectionProps) {
  const [lang, setLang] = useState("en");
  const bad = invalidPatterns(s.laughter_patterns ?? []);
  const terms = s.custom_terms ?? {};
  const threads = useApi("/api/settings/threads", undefined, {
    filters: false,
    enabled: !redacted,
  });
  const titleByFolder = new Map((threads.data?.items ?? []).map((t) => [t.folder ?? "", t.title]));
  return (
    <Section
      id="text"
      title="Words & text"
      description="Stop-words are left out of word clouds and top-word lists. Built-in lists exist for English, Slovak and Czech; add or remove words per language."
    >
      <div role="tablist" aria-label="Stop-word language" className="flex gap-1">
        {LANGS.map((l) => (
          <button
            key={l.code}
            type="button"
            role="tab"
            aria-selected={lang === l.code}
            className={cn("btn btn-sm", lang === l.code && "btn-primary")}
            onClick={() => setLang(l.code)}
          >
            {l.name}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <LinesField
          key={`add-${lang}`}
          label="Extra stop-words"
          hint="Added to the built-in list. One per line or comma-separated."
          commas
          value={s.stopwords_added?.[lang] ?? []}
          onChange={(list) =>
            update({ stopwords_added: setLangList(s.stopwords_added, lang, list) })
          }
        />
        <LinesField
          key={`rm-${lang}`}
          label="Built-in stop-words to keep counting"
          hint="Removed from the built-in list."
          commas
          value={s.stopwords_removed?.[lang] ?? []}
          onChange={(list) =>
            update({ stopwords_removed: setLangList(s.stopwords_removed, lang, list) })
          }
        />
      </div>
      <LinesField
        label="Ignored words (all languages)"
        hint="Never counted anywhere (e.g. names, inside jokes you don't want in screenshots)."
        commas
        value={s.ignored_words}
        onChange={(list) => update({ ignored_words: list })}
      />
      <div className="flex flex-col gap-1">
        <LinesField
          label="Laughter patterns"
          hint="One per line: a case-insensitive regular expression or a literal emoji."
          rows={6}
          value={s.laughter_patterns}
          onChange={(list) => update({ laughter_patterns: list })}
        />
        {bad.length > 0 && (
          <p className="text-xs text-danger" role="alert">
            These may be invalid regular expressions: {bad.join(", ")}
          </p>
        )}
        <div>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => update({ laughter_patterns: [...DEFAULT_LAUGHTER_PATTERNS] })}
          >
            Reset to defaults
          </button>
        </div>
      </div>
      <Toggle
        label="Track swear words"
        hint="Off by default. When on, the words below are counted in chat stats."
        checked={s.swear_words_enabled}
        onChange={(v) => update({ swear_words_enabled: v })}
      />
      <LinesField
        label="Swear-word list"
        commas
        value={s.swear_words}
        disabled={!s.swear_words_enabled}
        onChange={(list) => update({ swear_words: list })}
      />
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">Custom terms to track</h3>
        {redacted ? (
          <RedactedNote what="Custom terms are" />
        ) : (
          <>
            <LinesField
              label="Overview (all chats)"
              hint="Words or phrases tracked by the term tracker."
              value={terms["*"] ?? []}
              onChange={(list) => update({ custom_terms: setLangList(terms, "*", list) })}
            />
            {Object.keys(terms)
              .filter((k) => k !== "*")
              .map((folder) => (
                <div key={folder} className="flex items-end gap-2">
                  <div className="flex-1">
                    <LinesField
                      label={titleByFolder.get(folder) ?? folder}
                      rows={2}
                      value={terms[folder] ?? []}
                      onChange={(list) =>
                        update({ custom_terms: setLangList(terms, folder, list) })
                      }
                    />
                  </div>
                  <button
                    type="button"
                    className="btn btn-sm mb-1"
                    onClick={() => update({ custom_terms: setLangList(terms, folder, []) })}
                  >
                    Remove
                  </button>
                </div>
              ))}
          </>
        )}
      </div>
    </Section>
  );
}

// ------------------------------------------------------------------ emoji, privacy, appearance

export function EmojiSection({ s, update }: SectionProps) {
  return (
    <Section
      id="emoji"
      title="Emojis"
      description="How emoji variants are grouped in emoji statistics."
    >
      <Toggle
        label="Merge skin tones"
        hint="👍🏻 👍🏽 👍🏿 count as 👍."
        checked={s.emoji_merge_skin_tones}
        onChange={(v) => update({ emoji_merge_skin_tones: v })}
      />
      <Toggle
        label="Merge gendered variants"
        hint="🤷‍♀️ 🤷‍♂️ count as 🤷."
        checked={s.emoji_merge_gendered}
        onChange={(v) => update({ emoji_merge_gendered: v })}
      />
    </Section>
  );
}

export function PrivacySection({
  anonymize,
  onChange,
  busy,
}: {
  anonymize: boolean;
  onChange: (v: boolean) => void;
  busy: boolean;
}) {
  return (
    <Section
      id="privacy"
      title="Anonymize mode"
      description="For safe screenshots: every name becomes “Person A/B/C…” and message text is hidden. Applied on the backend, so no real names reach this page while it is on. Saved immediately."
    >
      <Toggle
        label="Anonymize names and hide message text"
        checked={anonymize}
        disabled={busy}
        onChange={onChange}
      />
    </Section>
  );
}

export function AppearanceSection({
  saveNow,
}: {
  saveNow: (p: { theme?: ThemePreference; ui_language?: Language }) => void;
}) {
  const theme = useThemeStore((st) => st.theme);
  const setTheme = useThemeStore((st) => st.setTheme);
  const language = useI18nStore((st) => st.language);
  const setLanguage = useI18nStore((st) => st.setLanguage);
  return (
    <Section
      id="appearance"
      title="Appearance"
      description="Applied right away on this device and saved with your settings."
    >
      <Field label="Theme">
        <div role="radiogroup" aria-label="Theme" className="flex flex-wrap gap-1">
          {(["system", "light", "dark"] as const).map((t) => (
            <button
              key={t}
              type="button"
              role="radio"
              aria-checked={theme === t}
              className={cn("btn btn-sm", theme === t && "btn-primary")}
              onClick={() => {
                setTheme(t);
                saveNow({ theme: t });
              }}
            >
              {t === "system" ? "System" : t === "light" ? "Light" : "Dark"}
            </button>
          ))}
        </div>
      </Field>
      <Field
        label="Language"
        hint="Slovak translation is partial; missing texts fall back to English."
      >
        <select
          className="input h-9 w-48"
          value={language}
          onChange={(e) => {
            const l = e.target.value as Language;
            setLanguage(l);
            saveNow({ ui_language: l });
          }}
          aria-label="Interface language"
        >
          <option value="en">English</option>
          <option value="sk">Slovenčina</option>
        </select>
      </Field>
    </Section>
  );
}

// ------------------------------------------------------------------ export

export function ExportSection({ anonymize }: { anonymize: boolean }) {
  const { toQuery, rangeLabel } = useFilters();
  const [withMessages, setWithMessages] = useState(false);
  const href = exportHref("all", toQuery(), withMessages);
  return (
    <Section
      id="export"
      title="Export"
      description={`Full statistics summary as JSON for everything in the current filters (${rangeLabel}). Message text is only included if you tick the box.`}
    >
      <label className="inline-flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={withMessages}
          onChange={(e) => setWithMessages(e.target.checked)}
        />
        Include messages (with text)
      </label>
      {withMessages && anonymize && (
        <p className="text-xs text-muted">Anonymize mode is on: message text will be empty.</p>
      )}
      <div>
        <a className="btn btn-sm btn-primary" href={href} download="instagraph-export.json">
          Download JSON
        </a>
      </div>
    </Section>
  );
}

// ------------------------------------------------------------------ danger zone

export function DangerSection() {
  const navigate = useNavigate();
  const [step, setStep] = useState<0 | 1 | 2>(0);
  const clear = useMutation({
    mutationFn: () => apiFetch("DELETE", "/api/data"),
    onSuccess: () => {
      setStep(0);
      queryClient.clear();
      navigate("/import");
    },
  });
  return (
    <Section
      id="danger"
      title="Clear all data"
      description="Deletes the imported dataset, settings, uploaded zips and thumbnails from the data directory."
    >
      <div>
        <button
          type="button"
          className="btn border-danger/60 text-danger hover:bg-danger/10"
          onClick={() => setStep(1)}
        >
          Clear all data…
        </button>
      </div>
      {clear.error && <p className="text-sm text-danger">{clear.error.message}</p>}
      <ConfirmDialog
        open={step === 1}
        onOpenChange={(o) => setStep(o ? 1 : 0)}
        title="Clear all data?"
        description="Everything Instagraph stored on this computer will be deleted. Your original Instagram export is not touched."
        confirmLabel="Continue"
        danger
        onConfirm={() => setStep(2)}
      />
      <ConfirmDialog
        open={step === 2}
        onOpenChange={(o) => setStep(o ? 2 : 0)}
        title="This cannot be undone"
        description="You will have to import your data again."
        confirmLabel="Delete everything"
        confirmText="DELETE"
        danger
        busy={clear.isPending}
        onConfirm={() => clear.mutate()}
      />
    </Section>
  );
}
