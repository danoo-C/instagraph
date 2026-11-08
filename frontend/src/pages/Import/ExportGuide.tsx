import { AlertIcon } from "@/components/icons";

const STEPS = [
  "Open Instagram → Settings → Accounts Center.",
  "Go to Your information and permissions → Download your information.",
  "Choose Download or transfer information, pick your account, then Some of your information → Messages (or everything).",
  "Choose Download to device. Set Format: JSON, Media quality: any, Date range: All time.",
  "Create the files. Instagram emails you when the export is ready (minutes to days).",
  "Download all parts (zip files) and upload them here, or unzip them and enter the folder path.",
];

/** "How to get your export" (§3.1, §5.2) with the JSON warning, privacy and E2EE notes (§3.4). */
export function ExportGuide() {
  return (
    <section className="card flex flex-col gap-3 p-4" aria-labelledby="guide-title">
      <h2 id="guide-title" className="font-semibold">
        How to get your export
      </h2>
      <ol className="list-decimal space-y-1 pl-5 text-sm">
        {STEPS.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>
      <div
        role="note"
        className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger/5 p-3 text-sm"
      >
        <AlertIcon className="mt-0.5 shrink-0 text-danger" />
        <p>
          <strong>Choose “JSON”, not “HTML”.</strong> Instagraph can only read the JSON format. An
          HTML export (files named <code>message_1.html</code>) is rejected — request a new export
          in JSON.
        </p>
      </div>
      <p className="text-sm text-muted">
        Chats that used end-to-end encryption may not be part of this export.
      </p>
      <p className="text-sm">
        <strong>Your data stays on this computer.</strong>{" "}
        <span className="text-muted">
          Files are processed locally by the Instagraph server running on your machine. Nothing is
          sent to the internet.
        </span>
      </p>
    </section>
  );
}
