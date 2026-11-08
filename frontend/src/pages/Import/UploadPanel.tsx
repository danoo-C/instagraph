import { useRef, useState, type DragEvent } from "react";
import { CloseIcon } from "@/components/icons";
import { formatBytes, zipFilesOnly } from "@/features/import/model";
import { cn } from "@/lib/utils";

/** Option A: drag & drop / choose one or more export zips. */
export function UploadPanel({
  disabled,
  onUpload,
}: {
  disabled?: boolean;
  onUpload: (files: File[]) => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [rejected, setRejected] = useState<string[]>([]);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const add = (list: FileList | null) => {
    if (!list) return;
    const { zips, rejected: bad } = zipFilesOnly(Array.from(list));
    setRejected(bad.map((f) => f.name));
    setFiles((prev) => {
      const key = (f: File) => `${f.name}:${f.size}`;
      const seen = new Set(prev.map(key));
      return [...prev, ...zips.filter((f) => !seen.has(key(f)))];
    });
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    if (!disabled) add(e.dataTransfer.files);
  };

  const total = files.reduce((a, f) => a + f.size, 0);

  return (
    <section className="card flex flex-col gap-3 p-4" aria-labelledby="upload-title">
      <div>
        <h2 id="upload-title" className="font-semibold">
          Option A — Upload zip files
        </h2>
        <p className="text-sm text-muted">
          Instagram may split a big export into several zips: add all parts, they are merged.
        </p>
      </div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-6 text-center text-sm transition-colors",
          over ? "border-accent bg-accent/5" : "border-border",
          disabled && "opacity-50",
        )}
      >
        <p>Drag and drop your export .zip file(s) here</p>
        <p className="text-xs text-muted">or</p>
        <button
          type="button"
          className="btn"
          onClick={() => input.current?.click()}
          disabled={disabled}
        >
          Choose files
        </button>
        <input
          ref={input}
          type="file"
          accept=".zip,application/zip,application/x-zip-compressed"
          multiple
          className="sr-only"
          tabIndex={-1}
          aria-hidden
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
      {rejected.length > 0 && (
        <p role="alert" className="text-sm text-danger">
          Only .zip files can be uploaded — skipped: {rejected.join(", ")}
        </p>
      )}
      {files.length > 0 && (
        <ul className="flex flex-col divide-y divide-border rounded-md border border-border text-sm">
          {files.map((f) => (
            <li key={`${f.name}:${f.size}`} className="flex items-center gap-2 px-3 py-1.5">
              <span className="min-w-0 flex-1 truncate">{f.name}</span>
              <span className="tabular-nums text-muted">{formatBytes(f.size)}</span>
              <button
                type="button"
                className="icon-btn"
                aria-label={`Remove ${f.name}`}
                onClick={() => setFiles((prev) => prev.filter((x) => x !== f))}
                disabled={disabled}
              >
                <CloseIcon />
              </button>
            </li>
          ))}
        </ul>
      )}
      <button
        type="button"
        className="btn btn-primary self-start"
        disabled={disabled || files.length === 0}
        onClick={() => onUpload(files)}
      >
        {files.length
          ? `Upload and import ${files.length} file${files.length > 1 ? "s" : ""} (${formatBytes(total)})`
          : "Upload and import"}
      </button>
    </section>
  );
}
