import * as Dialog from "@radix-ui/react-dialog";
import { CloseIcon } from "@/components/icons";
import { formatDateTime } from "@/lib/format";

export interface LightboxItem {
  media_id: number;
  kind: string;
  url?: string | null;
  exists: boolean;
  ts?: string;
  senderName?: string;
}

/** Full-size media viewer (image / video / audio) with previous / next navigation. */
export function MediaLightbox({
  items,
  index,
  onIndexChange,
  timeZone,
  onShowMessage,
}: {
  items: readonly LightboxItem[];
  index: number | null;
  onIndexChange: (i: number | null) => void;
  timeZone?: string;
  onShowMessage?: (item: LightboxItem) => void;
}) {
  const item = index != null ? items[index] : undefined;
  const src = item?.url ?? (item ? `/api/media/${item.media_id}` : undefined);
  const go = (d: number) => {
    if (index == null) return;
    const n = index + d;
    if (n >= 0 && n < items.length) onIndexChange(n);
  };
  return (
    <Dialog.Root open={item != null} onOpenChange={(o) => !o && onIndexChange(null)}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/80" />
        <Dialog.Content
          className="fixed inset-2 z-50 flex flex-col gap-2 text-white sm:inset-8"
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") go(-1);
            if (e.key === "ArrowRight") go(1);
          }}
        >
          <div className="flex items-center gap-2 text-sm">
            <Dialog.Title className="font-medium">
              {item?.senderName ?? "Media"}
              {item?.ts && (
                <span className="ml-2 font-normal opacity-80">
                  {formatDateTime(item.ts, timeZone)}
                </span>
              )}
            </Dialog.Title>
            <Dialog.Description className="sr-only">
              Media viewer. Use the left and right arrow keys to browse.
            </Dialog.Description>
            {item && onShowMessage && (
              <button
                type="button"
                className="btn btn-sm ml-auto"
                onClick={() => onShowMessage(item)}
              >
                Show in chat
              </button>
            )}
            <Dialog.Close asChild>
              <button
                type="button"
                className={`icon-btn text-white ${onShowMessage ? "" : "ml-auto"}`}
                aria-label="Close"
              >
                <CloseIcon />
              </button>
            </Dialog.Close>
          </div>
          <div className="flex min-h-0 flex-1 items-center justify-center gap-2">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => go(-1)}
              disabled={index == null || index <= 0}
              aria-label="Previous"
            >
              ‹
            </button>
            <div className="flex h-full min-w-0 flex-1 items-center justify-center">
              {item && !item.exists ? (
                <p className="opacity-80">This file is not included in the export.</p>
              ) : item?.kind === "video" ? (
                <video key={src} src={src} controls autoPlay className="max-h-full max-w-full" />
              ) : item?.kind === "audio" ? (
                <audio key={src} src={src} controls autoPlay />
              ) : item ? (
                <img
                  key={src}
                  src={src}
                  alt={`${item.kind} from ${item.senderName ?? "unknown"}`}
                  className="max-h-full max-w-full object-contain"
                />
              ) : null}
            </div>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => go(1)}
              disabled={index == null || index >= items.length - 1}
              aria-label="Next"
            >
              ›
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
