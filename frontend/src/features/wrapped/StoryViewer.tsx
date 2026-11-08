import { useCallback, useEffect, useRef, useState } from "react";
import { CloseIcon, DownloadIcon } from "@/components/icons";
import type { Slide } from "@/features/wrapped/model";
import { paintSlide, slideToPng } from "@/features/wrapped/paint";
import { slideAltText } from "@/features/wrapped/slides";
import { useOpenMessages } from "@/lib/drilldown";
import { downloadFile, slugify } from "@/lib/utils";

const SWIPE_PX = 40;

function SlideCanvas({ slide }: { slide: Slide }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (ref.current) paintSlide(ref.current, slide);
  }, [slide]);
  return (
    <canvas
      ref={ref}
      role="img"
      aria-label={slideAltText(slide)}
      className="h-full w-full rounded-2xl shadow-2xl"
      width={1080}
      height={1920}
    />
  );
}

/** Full-screen story-style slideshow (tap / click / arrows / swipe), §5.10. */
export function StoryViewer({
  slides,
  initialIndex = 0,
  filenameBase,
  onClose,
}: {
  slides: Slide[];
  initialIndex?: number;
  filenameBase: string;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(initialIndex);
  const [saving, setSaving] = useState(false);
  const start = useRef<{ x: number; y: number } | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const open = useOpenMessages();
  const slide = slides[Math.min(index, slides.length - 1)];

  const next = useCallback(
    () => setIndex((i) => Math.min(i + 1, slides.length - 1)),
    [slides.length],
  );
  const prev = useCallback(() => setIndex((i) => Math.max(i - 1, 0)), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight" || e.key === " " || e.key === "PageDown") {
        e.preventDefault();
        next();
      } else if (e.key === "ArrowLeft" || e.key === "PageUp") {
        e.preventDefault();
        prev();
      } else if (e.key === "Escape") onClose();
      else if (e.key === "Home") setIndex(0);
      else if (e.key === "End") setIndex(slides.length - 1);
    };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [next, prev, onClose, slides.length]);

  if (!slide) return null;

  const save = async (s: Slide, i: number) => {
    setSaving(true);
    try {
      const blob = await slideToPng(s);
      if (blob)
        downloadFile(
          `${slugify(filenameBase)}-${String(i + 1).padStart(2, "0")}-${s.key}.png`,
          blob,
        );
    } finally {
      setSaving(false);
    }
  };
  const saveAll = async () => {
    for (let i = 0; i < slides.length; i++) await save(slides[i]!, i);
  };

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label="Wrapped slideshow"
      tabIndex={-1}
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/95 p-2 outline-none sm:p-4"
    >
      <div className="relative flex h-full max-h-full w-full flex-col items-center justify-center">
        <div
          className="relative aspect-[9/16] h-full max-h-[calc(100vh-5rem)] max-w-full select-none touch-pan-y"
          onPointerDown={(e) => {
            start.current = { x: e.clientX, y: e.clientY };
          }}
          onPointerUp={(e) => {
            const s = start.current;
            start.current = null;
            if (!s) return;
            const dx = e.clientX - s.x;
            const dy = e.clientY - s.y;
            if (Math.abs(dx) > SWIPE_PX && Math.abs(dx) > Math.abs(dy)) {
              if (dx < 0) next();
              else prev();
              return;
            }
            if (Math.abs(dx) < 10 && Math.abs(dy) < 10) {
              const rect = e.currentTarget.getBoundingClientRect();
              if (e.clientX - rect.left < rect.width / 3) prev();
              else next();
            }
          }}
        >
          <SlideCanvas slide={slide} />
          {/* Progress bars */}
          <div
            className="pointer-events-none absolute inset-x-3 top-3 flex gap-1"
            aria-hidden="true"
          >
            {slides.map((s, i) => (
              <span key={s.key} className="h-1 flex-1 overflow-hidden rounded-full bg-white/30">
                <span
                  className="block h-full bg-white"
                  style={{ width: i <= index ? "100%" : "0%" }}
                />
              </span>
            ))}
          </div>
          <p className="sr-only" aria-live="polite">
            Slide {index + 1} of {slides.length}: {slideAltText(slide)}
          </p>
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-center gap-2 text-sm text-white">
          <button
            type="button"
            className="btn btn-sm"
            onClick={prev}
            disabled={index === 0}
            aria-label="Previous slide"
          >
            ←
          </button>
          <span className="tabular-nums text-white/80">
            {index + 1} / {slides.length}
          </span>
          <button
            type="button"
            className="btn btn-sm"
            onClick={next}
            disabled={index === slides.length - 1}
            aria-label="Next slide"
          >
            →
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => void save(slide, index)}
            disabled={saving}
          >
            <DownloadIcon /> Save PNG
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => void saveAll()}
            disabled={saving}
          >
            Save all
          </button>
          {slide.drilldown && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => {
                onClose();
                open(slide.drilldown!);
              }}
            >
              Open messages
            </button>
          )}
          <button
            type="button"
            className="btn btn-sm"
            onClick={onClose}
            aria-label="Close slideshow"
          >
            <CloseIcon /> Close
          </button>
        </div>
      </div>
    </div>
  );
}
