# Frontend design

Two views, polished like something customers would see: a catalog grid and a PDP. Vite + React + TypeScript + Tailwind + shadcn, React Router between the two. Everything renders from the pipeline's extracted JSON via the FastAPI server; the UI is deliberately a proof that the extraction schema holds real, usable data.

## Component tree

```
App                                    router: / and /product/:id
├─ CatalogPage                         /
│  ├─ CatalogHeader                    # title + product count
│  ├─ SourceFilter                     # chips: All (50) | Assignment (5)
│  ├─ SearchInput                      # client-side text match on name+brand, minimal
│  ├─ ProductGrid
│  │  └─ ProductCard (xN)              # image (hover swaps to 2nd photo), brand, name,
│  │                                   # price w/ compare-at strikethrough, sale badge
│  └─ GridSkeleton / EmptyState        # loading shimmer; "no matches" for dead searches
└─ ProductPage                         /product/:id
   ├─ Breadcrumb                       # taxonomy path segments
   ├─ Gallery
   │  ├─ ThumbRail                     # vertical thumbnails, video as final slide
   │  └─ MainPane                      # 4:5, <video> w/ poster when selected
   ├─ BuyBox
   │  ├─ PriceBlock                    # current, compare-at strikethrough, %-off badge
   │  ├─ VariantPicker                 # one group per option axis
   │  └─ Resolved SKU                  # shown when a variant matches
   ├─ Description
   ├─ KeyFeatures                      # bullet list
   └─ MetaGrid                         # brand, category, sku of resolved variant
```

## The variant picker (the centerpiece)

Renders one button group per `options` axis. Selection state is `{axisName: value}`. Resolution against `variants`:

- A variant matches when every one of its `selections` agrees with the current choices.
- Full match -> the buy box shows that variant's sku and price (falling back to product price). For example, Lake / Medium / Regular on the saved L.L.Bean tee selects a concrete sale-priced SKU. Availability stays in the extracted data but is not displayed as a stock claim.
- A value is disabled only when the extracted variants cover enough combinations to rule that value out. Sparse variant lists leave the other options selectable.
- Products with options but no variants (axes the page never tied together) show the axes without resolution, and single-configuration products show no picker at all. The UI states degrade exactly as the extraction semantics do.

Stress tests that must feel good: llbean (3 axes, sparse extracted variants), nike (17 sizes), reebok (size x width), ace (no variants at all).

## Robustness (the memorable touch)

Extracted image URLs come from the wild, so media failure is a feature surface, not an accident:

- Every image renders through `SafeImage`: on error it swaps to a neutral placeholder with the brand initial, and the gallery drops broken thumbs from the rail after first error.
- The video slide uses poster + explicit controls; if the video errors, the slide stays with an "open original" link instead of vanishing (some CDNs gate playback by referrer).
- Currency formatting via `Intl.NumberFormat` from the extracted currency code: GBP, EUR, SEK, JPY, CAD all render correctly, which quietly demos the international pages.

## Files

```
frontend/
├─ src/
│  ├─ main.tsx, App.tsx                # router shell
│  ├─ api.ts                           # typed fetch of /products and /products/{id}
│  ├─ types.ts                         # Product/Summary/Variant mirroring models.py
│  ├─ pages/CatalogPage.tsx
│  ├─ pages/ProductPage.tsx
│  ├─ components/ProductCard.tsx
│  ├─ components/Gallery.tsx
│  ├─ components/VariantPicker.tsx
│  ├─ components/PriceBlock.tsx
│  ├─ components/SafeImage.tsx
│  ├─ components/ui/                   # shadcn primitives (button, badge, input, skeleton)
│  └─ lib/resolveVariant.ts            # pure selection-matching logic, unit-testable
└─ index.html, vite.config.ts, tailwind.config.js
```

## Scope discipline

In: the two pages above, the filter, minimal search, loading/error/empty states, responsive down to phone width.
Out (assignment says so): auth, persistence, cart/checkout, home/about pages, hosting. The search stays a client-side string match; no query params, no debounced API.
