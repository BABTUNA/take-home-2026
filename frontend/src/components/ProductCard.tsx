import { useState } from "react";
import { Link } from "react-router-dom";
import type { ProductSummary } from "../types";
import { SafeImage } from "./SafeImage";
import { PriceBlock } from "./PriceBlock";

export function ProductCard({ product }: { product: ProductSummary }) {
  const [hover, setHover] = useState(false);
  // extracted urls come from the wild; once the hover image 404s, stop
  // swapping to it instead of flashing the placeholder on every hover
  const [hoverBroken, setHoverBroken] = useState(false);
  const onSale =
    product.price.compare_at_price !== null &&
    product.price.compare_at_price > product.price.price;
  const src =
    hover && !hoverBroken && product.hover_image_url
      ? product.hover_image_url
      : product.image_url;

  return (
    <Link
      to={`/product/${product.id}`}
      className="group block focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ink"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <div className="relative aspect-[4/5] overflow-hidden bg-surface">
        <SafeImage
          src={src}
          alt={product.name}
          brand={product.brand}
          className="h-full w-full object-contain transition-transform duration-300 group-hover:scale-[1.02]"
          onBroken={() => {
            if (src === product.hover_image_url) setHoverBroken(true);
          }}
        />
        {onSale && (
          <span className="absolute top-3 left-3 bg-ink px-2 py-0.5 text-[11px] font-medium uppercase tracking-[0.08em] text-white">
            Sale
          </span>
        )}
      </div>
      <div className="mt-3 space-y-1">
        <p className="eyebrow">{product.brand}</p>
        <h3 className="text-sm leading-snug font-medium">{product.name}</h3>
        <PriceBlock price={product.price} compact />
      </div>
    </Link>
  );
}
