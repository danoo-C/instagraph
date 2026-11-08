import * as Dialog from "@radix-ui/react-dialog";
import { CloseIcon } from "@/components/icons";
import { mediaUrl } from "@/features/messages/api";

export interface MediaRef {
  media_id: number;
  kind: "photo" | "video" | "audio" | "gif" | "sticker";
  /** Caption (e.g. sender · date). */
  caption?: string;
}

/** Full-size media viewer (photo / GIF / sticker image, video or audio player). */
export function MediaDialog({ media, onClose }: { media: MediaRef | null; onClose: () => void }) {
  return (
    <Dialog.Root open={media != null} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/70" />
        <Dialog.Content className="fixed inset-2 z-50 flex flex-col gap-2 rounded-lg bg-surface p-3 shadow-xl sm:inset-8">
          <div className="flex items-center gap-2">
            <Dialog.Title className="truncate text-sm font-medium">
              {media?.caption ?? "Media"}
            </Dialog.Title>
            <Dialog.Description className="sr-only">Attachment preview</Dialog.Description>
            <a
              className="btn btn-sm ml-auto"
              href={media ? mediaUrl(media.media_id) : undefined}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open original
            </a>
            <Dialog.Close asChild>
              <button type="button" className="icon-btn" aria-label="Close">
                <CloseIcon />
              </button>
            </Dialog.Close>
          </div>
          <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto">
            {media && <MediaBody media={media} />}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function MediaBody({ media }: { media: MediaRef }) {
  const src = mediaUrl(media.media_id);
  if (media.kind === "video")
    return <video src={src} controls autoPlay className="max-h-full max-w-full" />;
  if (media.kind === "audio")
    return <audio src={src} controls autoPlay className="w-full max-w-lg" />;
  return (
    <img
      src={src}
      alt={media.caption ?? "Attachment"}
      className="max-h-full max-w-full object-contain"
    />
  );
}
