import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { Product } from "../types";
import { fetchProduct } from "../api";
import { Gallery } from "../components/Gallery";
import { PriceBlock } from "../components/PriceBlock";
import { VariantPicker } from "../components/VariantPicker";
import { resolveVariant, variantImages, type Selections } from "../lib/resolveVariant";

export function ProductPage() {
  const { id } = useParams();
  const [product, setProduct] = useState<Product | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selections, setSelections] = useState<Selections>({});

  useEffect(() => {
    setProduct(null);
    setSelections({});
    if (id) fetchProduct(id).then(setProduct).catch((e) => setError(String(e)));
  }, [id]);

  const resolved = useMemo(
    () => (product ? resolveVariant(product.variants, selections) : null),
    [product, selections],
  );

  const [showAllPhotos, setShowAllPhotos] = useState(false);

  // when the selection matches variants that carry their own images, the
  // gallery shows only those (real-store behavior), with a "show all"
  // escape hatch; selections without image data fall back to the full
  // gallery so sparse products never strand on an empty one
  const variantOnly = useMemo(
    () => (product ? variantImages(product.variants, selections) : []),
    [product, selections],
  );
  const filtered = !showAllPhotos && variantOnly.length > 0;
  const galleryImages = !product ? [] : filtered ? variantOnly : product.image_urls;

  if (error)
    return (
      <main className="mx-auto max-w-6xl px-4 py-24 text-center text-muted">
        <p>Product not found.</p>
        <Link to="/" className="mt-2 inline-block underline">Back to catalog</Link>
      </main>
    );
  if (!product)
    return (
      <main className="mx-auto max-w-6xl animate-pulse px-4 py-10">
        <div className="grid gap-10 lg:grid-cols-2">
          <div className="aspect-[4/5] bg-surface" />
          <div className="space-y-4">
            <div className="h-3 w-24 bg-surface" />
            <div className="h-8 w-3/4 bg-surface" />
            <div className="h-5 w-32 bg-surface" />
          </div>
        </div>
      </main>
    );

  const crumbs = product.category.name.split(" > ");
  const allChosen = product.options.every((o) => selections[o.name]);

  return (
    <main className="mx-auto max-w-6xl px-4 pb-24">
      <nav className="flex flex-wrap items-center gap-1.5 py-6 text-xs text-muted">
        <Link to="/" className="hover:text-ink">Catalog</Link>
        {crumbs.map((c, i) => (
          <span key={i} className="flex items-center gap-1.5">
            <span className="text-faint">/</span>
            <span className={i === crumbs.length - 1 ? "text-ink" : ""}>{c}</span>
          </span>
        ))}
      </nav>

      <div className="grid gap-10 lg:grid-cols-2">
        <div>
          <Gallery
            // remount when the image set changes so the gallery snaps to slide 0
            key={`${filtered}-${galleryImages[0] ?? "empty"}`}
            images={galleryImages}
            videoUrl={product.video_url}
            name={product.name}
            brand={product.brand}
          />
          {variantOnly.length > 0 && (
            <button
              onClick={() => setShowAllPhotos((s) => !s)}
              className="mt-2 text-xs text-muted underline-offset-2 hover:underline"
            >
              {filtered
                ? `Showing ${galleryImages.length} photo${galleryImages.length === 1 ? "" : "s"} for this selection · Show all`
                : "Show selection photos only"}
            </button>
          )}
        </div>

        <div className="max-w-lg">
          <p className="eyebrow">{product.brand}</p>
          <h1 className="font-display mt-1 text-2xl font-medium tracking-tight text-balance">
            {product.name}
          </h1>
          <div className="mt-3">
            <PriceBlock price={product.price} override={resolved?.price ?? undefined} />
          </div>

          <div className="mt-8">
            <VariantPicker
              options={product.options}
              variants={product.variants}
              selections={selections}
              onSelect={(axis, value) =>
                setSelections((s) =>
                  s[axis] === value
                    ? Object.fromEntries(Object.entries(s).filter(([k]) => k !== axis))
                    : { ...s, [axis]: value },
                )
              }
            />
          </div>

          {product.options.length > 0 && (
            <p className="mt-4 min-h-5 text-sm text-muted">
              {resolved ? (
                <>
                  {resolved.available === true && <span className="text-ink">In stock</span>}
                  {resolved.available === false && <span className="text-sale">Out of stock</span>}
                  {resolved.available === null && "Availability not listed"}
                  {resolved.sku && <span className="ml-2 text-faint">SKU {resolved.sku}</span>}
                </>
              ) : product.variants.length > 0 ? (
                allChosen ? "This combination isn't offered." : "Select options to check availability."
              ) : (
                "Configuration details on the brand's site."
              )}
            </p>
          )}

          <button
            disabled={product.variants.length > 0 && (!resolved || resolved.available === false)}
            className="mt-6 w-full bg-ink py-3 text-sm font-medium tracking-wide text-white transition-opacity enabled:hover:opacity-85 disabled:cursor-not-allowed disabled:opacity-30"
          >
            Add to bag
          </button>

          {product.description && (
            <p className="mt-8 text-sm leading-relaxed whitespace-pre-line text-muted">
              {product.description}
            </p>
          )}

          {product.key_features.length > 0 && (
            <section className="mt-8">
              <h2 className="eyebrow mb-3">Details</h2>
              <ul className="space-y-1.5 text-sm">
                {product.key_features.map((f, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="text-faint">—</span>
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="mt-8 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 border-t border-line pt-6 text-sm">
            <span className="text-muted">Brand</span>
            <span>{product.brand}</span>
            <span className="text-muted">Category</span>
            <span>{crumbs[crumbs.length - 1]}</span>
            {product.colors.length > 0 && (
              <>
                <span className="text-muted">Colors</span>
                <span>{product.colors.join(", ")}</span>
              </>
            )}
            <span className="text-muted">Source</span>
            <span className="capitalize">{product.source} page</span>
          </section>
        </div>
      </div>
    </main>
  );
}
