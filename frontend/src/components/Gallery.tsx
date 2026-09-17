import { useState } from "react";
import { SafeImage } from "./SafeImage";

interface Props {
  images: string[];
  videoUrl: string | null;
  name: string;
  brand: string;
}

// thumb rail + main pane; the video rides as the final slide. Broken thumbs
// drop out of the rail after their first error instead of showing dead boxes.
export function Gallery({ images, videoUrl, name, brand }: Props) {
  const [active, setActive] = useState(0);
  const [dead, setDead] = useState<Set<number>>(new Set());
  const [videoFailed, setVideoFailed] = useState(false);

  const slides: Array<{ kind: "image" | "video"; url: string }> = [
    ...images.map((url) => ({ kind: "image" as const, url })),
    ...(videoUrl ? [{ kind: "video" as const, url: videoUrl }] : []),
  ];
  const alive = slides.map((s, i) => [s, i] as const).filter(([, i]) => !dead.has(i));
  const current = slides[active] && !dead.has(active) ? slides[active] : alive[0]?.[0];

  if (!current) {
    return (
      <div className="flex aspect-[4/5] items-center justify-center bg-surface text-faint">
        <span className="font-display text-4xl">{brand.charAt(0)}</span>
      </div>
    );
  }

  return (
    <div className="flex gap-3">
      {alive.length > 1 && (
        <div className="flex w-16 shrink-0 flex-col gap-2 overflow-y-auto">
          {alive.map(([slide, i]) => (
            <button
              key={i}
              onClick={() => setActive(i)}
              className={`relative aspect-square overflow-hidden bg-surface transition-opacity ${
                i === active ? "outline-1 outline-ink" : "opacity-60 hover:opacity-100"
              }`}
              aria-label={slide.kind === "video" ? "Play video" : `View image ${i + 1}`}
            >
              {slide.kind === "image" ? (
                <SafeImage
                  src={slide.url}
                  alt=""
                  brand={brand}
                  className="h-full w-full object-cover"
                  onBroken={() => setDead((d) => new Set(d).add(i))}
                />
              ) : (
                <span className="flex h-full w-full items-center justify-center text-lg">▶</span>
              )}
            </button>
          ))}
        </div>
      )}

      <div className="min-w-0 flex-1">
        <div className="flex aspect-[4/5] items-center justify-center overflow-hidden bg-surface">
          {current.kind === "image" ? (
            <SafeImage src={current.url} alt={name} brand={brand} className="h-full w-full object-contain" />
          ) : videoFailed ? (
            // some video hosts gate playback by referrer; keep the slide useful
            <div className="text-center text-sm text-muted">
              <p>Video can't play embedded.</p>
              <a href={current.url} target="_blank" rel="noreferrer" className="underline">
                Open original
              </a>
            </div>
          ) : (
            <video
              src={current.url}
              controls
              playsInline
              className="h-full w-full object-contain"
              onError={() => setVideoFailed(true)}
            />
          )}
        </div>
      </div>
    </div>
  );
}
