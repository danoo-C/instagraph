import { useId, useState, type FormEvent } from "react";

/** Option B: import from a local path (no copy). */
export function PathPanel({
  disabled,
  onSubmit,
}: {
  disabled?: boolean;
  onSubmit: (path: string) => void;
}) {
  const [path, setPath] = useState("");
  const id = useId();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (path.trim()) onSubmit(path.trim());
  };
  return (
    <section className="card flex flex-col gap-3 p-4" aria-labelledby={`${id}-title`}>
      <div>
        <h2 id={`${id}-title`} className="font-semibold">
          Option B — Local path
        </h2>
        <p className="text-sm text-muted">
          Faster for big exports: nothing is copied, the files are read where they are. Enter an
          absolute path to an unzipped export folder, a .zip file, or a folder containing several
          zips.
        </p>
      </div>
      <form onSubmit={submit} className="flex flex-col gap-2">
        <label htmlFor={id} className="text-sm font-medium">
          Path on this computer
        </label>
        <input
          id={id}
          className="input font-mono"
          placeholder="/home/me/Downloads/instagram-export"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          disabled={disabled}
          spellCheck={false}
          autoComplete="off"
        />
        <button
          type="submit"
          className="btn btn-primary self-start"
          disabled={disabled || !path.trim()}
        >
          Import from path
        </button>
      </form>
    </section>
  );
}
