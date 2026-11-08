import { useId, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface SearchOption<V extends string | number = number> {
  value: V;
  label: string;
  hint?: string;
  icon?: ReactNode;
  disabled?: boolean;
}

export interface SearchSelectProps<V extends string | number> {
  options: SearchOption<V>[];
  onSelect: (value: V) => void;
  /** Called with the typed text (debounce on the caller side for server search). */
  onSearch?: (text: string) => void;
  /** Filter options locally by label (default true when `onSearch` is absent). */
  localFilter?: boolean;
  placeholder?: string;
  label: string;
  loading?: boolean;
  disabled?: boolean;
  /** Keep the typed text after selecting (single-value pickers show the value instead). */
  clearOnSelect?: boolean;
  maxResults?: number;
  className?: string;
}

/** Accessible searchable combobox (type to filter, ↑/↓/Enter/Escape). */
export function SearchSelect<V extends string | number>({
  options,
  onSelect,
  onSearch,
  localFilter,
  placeholder = "Search…",
  label,
  loading,
  disabled,
  clearOnSelect = true,
  maxResults = 50,
  className,
}: SearchSelectProps<V>) {
  const id = useId();
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);
  const filterLocally = localFilter ?? !onSearch;
  const needle = text.trim().toLocaleLowerCase();
  const shown = (
    filterLocally && needle
      ? options.filter((o) => `${o.label} ${o.hint ?? ""}`.toLocaleLowerCase().includes(needle))
      : options
  ).slice(0, maxResults);

  const choose = (o: SearchOption<V> | undefined) => {
    if (!o || o.disabled) return;
    onSelect(o.value);
    if (clearOnSelect) {
      setText("");
      onSearch?.("");
    }
    setOpen(false);
  };

  return (
    <div className={cn("relative", className)}>
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <input
        id={id}
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        aria-activedescendant={open && shown[active] ? `${id}-opt-${active}` : undefined}
        autoComplete="off"
        className="input h-9 w-full"
        placeholder={placeholder}
        value={text}
        disabled={disabled}
        onChange={(e) => {
          setText(e.target.value);
          onSearch?.(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
            setActive((a) => Math.min(a + 1, shown.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter") {
            if (open) {
              e.preventDefault();
              choose(shown[active]);
            }
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      />
      {open && !disabled && (
        <ul
          id={`${id}-list`}
          ref={listRef}
          role="listbox"
          aria-label={label}
          className="card absolute z-30 mt-1 max-h-72 w-full min-w-[16rem] overflow-auto p-1 text-sm"
        >
          {loading && <li className="px-2 py-1.5 text-muted">Loading…</li>}
          {!loading && shown.length === 0 && <li className="px-2 py-1.5 text-muted">No matches</li>}
          {shown.map((o, i) => (
            <li
              key={String(o.value)}
              id={`${id}-opt-${i}`}
              role="option"
              aria-selected={i === active}
              aria-disabled={o.disabled || undefined}
              className={cn(
                "flex cursor-pointer items-center gap-2 rounded px-2 py-1.5",
                i === active && "bg-surface-2",
                o.disabled && "cursor-not-allowed opacity-50",
              )}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(o);
              }}
              onMouseEnter={() => setActive(i)}
            >
              {o.icon}
              <span className="min-w-0 flex-1 truncate">{o.label}</span>
              {o.hint && <span className="shrink-0 text-xs text-muted">{o.hint}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
