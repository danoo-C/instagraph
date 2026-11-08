import { useId, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as Switch from "@radix-ui/react-switch";
import { linesToText, parseLines } from "@/features/settings/diff";
import { cn } from "@/lib/utils";

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

export function Section({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      className="card flex scroll-mt-20 flex-col gap-4 p-4 sm:p-5"
    >
      <div>
        <h2 id={`${id}-title`} className="text-base font-semibold">
          {title}
        </h2>
        {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
      </div>
      {children}
    </section>
  );
}

export function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: ReactNode;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="text-sm font-medium">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

export function Toggle({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  hint?: ReactNode;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        {hint && <p className="text-xs text-muted">{hint}</p>}
      </div>
      <Switch.Root
        id={id}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onChange}
        className="relative mt-0.5 h-5 w-9 shrink-0 rounded-full bg-border transition-colors disabled:opacity-50 data-[state=checked]:bg-accent"
      >
        <Switch.Thumb className="block h-4 w-4 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[18px]" />
      </Switch.Root>
    </div>
  );
}

/** Textarea editing a list of strings (one per line; optionally commas too). */
export function LinesField({
  label,
  hint,
  value,
  onChange,
  rows = 4,
  commas = false,
  disabled,
  placeholder,
}: {
  label: string;
  hint?: ReactNode;
  value: readonly string[] | undefined;
  onChange: (list: string[]) => void;
  rows?: number;
  commas?: boolean;
  disabled?: boolean;
  placeholder?: string;
}) {
  const id = useId();
  const [text, setText] = useState(() => linesToText(value));
  const [prev, setPrev] = useState<readonly string[]>(value ?? []);
  // Re-sync when the value changes from outside (save, discard, reset).
  const incoming = value ?? [];
  if (!sameList(incoming, prev)) {
    setPrev(incoming);
    if (!sameList(parseLines(text, { commas }), incoming)) setText(linesToText(incoming));
  }
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <textarea
        id={id}
        className="input min-h-[4rem] font-mono text-xs"
        rows={rows}
        value={text}
        disabled={disabled}
        placeholder={placeholder}
        spellCheck={false}
        onChange={(e) => {
          setText(e.target.value);
          onChange(parseLines(e.target.value, { commas }));
        }}
      />
    </Field>
  );
}

export function NumberField({
  label,
  hint,
  value,
  onChange,
  min,
  max,
  step = 1,
  suffix,
}: {
  label: string;
  hint?: ReactNode;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
}) {
  const id = useId();
  const [text, setText] = useState(String(value));
  const [prev, setPrev] = useState(value);
  if (prev !== value) {
    setPrev(value);
    if (Number(text) !== value) setText(String(value));
  }
  const n = Number(text);
  const invalid = text === "" || !Number.isFinite(n) || n < min || n > max;
  return (
    <Field
      label={label}
      hint={invalid ? `Enter a number between ${min} and ${max}.` : hint}
      htmlFor={id}
    >
      <span className="flex items-center gap-2">
        <input
          id={id}
          type="number"
          className={cn("input h-9 w-32", invalid && "border-danger")}
          value={text}
          min={min}
          max={max}
          step={step}
          aria-invalid={invalid}
          onChange={(e) => {
            setText(e.target.value);
            const v = Number(e.target.value);
            if (e.target.value !== "" && Number.isFinite(v) && v >= min && v <= max) onChange(v);
          }}
        />
        {suffix && <span className="text-sm text-muted">{suffix}</span>}
      </span>
    </Field>
  );
}

export function RedactedNote({ what }: { what: string }) {
  return (
    <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-xs text-muted">
      🔒 {what} hidden while anonymize mode is on. Turn anonymize off to edit them.
    </p>
  );
}

/** Confirmation dialog (Radix). `confirmText` makes the user type a word first. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  confirmText,
  danger,
  busy,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  confirmText?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
}) {
  const [typed, setTyped] = useState("");
  const id = useId();
  const ok = !confirmText || typed.trim() === confirmText;
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(o) => {
        if (!o) setTyped("");
        onOpenChange(o);
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content className="card fixed left-1/2 top-1/2 z-50 flex w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col gap-3 p-5">
          <Dialog.Title className="text-base font-semibold">{title}</Dialog.Title>
          <Dialog.Description asChild>
            <div className="text-sm text-muted">{description}</div>
          </Dialog.Description>
          {confirmText && (
            <Field label={`Type ${confirmText} to confirm`} htmlFor={id}>
              <input
                id={id}
                className="input h-9"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                autoComplete="off"
              />
            </Field>
          )}
          <div className="mt-2 flex justify-end gap-2">
            <Dialog.Close asChild>
              <button type="button" className="btn">
                Cancel
              </button>
            </Dialog.Close>
            <button
              type="button"
              className={cn(
                "btn",
                danger
                  ? "border-transparent bg-danger text-white hover:bg-danger/90"
                  : "btn-primary",
              )}
              disabled={!ok || busy}
              onClick={onConfirm}
            >
              {busy ? "Working…" : confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
