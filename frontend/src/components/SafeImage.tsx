import { useState } from "react";

interface Props {
  src: string | null;
  alt: string;
  brand?: string;
  className?: string;
  onBroken?: () => void;
}

// extracted urls come from the wild; a broken image renders as a quiet
// placeholder with the brand initial instead of a browser error glyph
export function SafeImage({ src, alt, brand, className = "", onBroken }: Props) {
  const [broken, setBroken] = useState(false);

  if (!src || broken) {
    return (
      <div className={`flex items-center justify-center bg-surface text-faint ${className}`}>
        <span className="font-display text-3xl">{(brand ?? alt ?? "?").charAt(0)}</span>
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      className={className}
      onError={() => {
        setBroken(true);
        onBroken?.();
      }}
    />
  );
}
