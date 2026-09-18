# Frontend implementation

## Goal and how it works

Catalog grid and PDP, rendered straight from the server's JSON, polished enough to ship. Vite + React + TypeScript + Tailwind + shadcn, React Router between the two views.

- The catalog fetches summaries once, filters and searches client-side (50 products, no server round-trips).
- The PDP fetches one full product and drives everything off it: gallery, buy box, variant resolution.
- The variant picker resolves the user's axis choices to a concrete variant (sku, price, availability), which is the visible proof the extraction schema holds real data.
- Media failure is handled, not assumed away: every image goes through SafeImage, the video slide survives CDN referrer-blocking.

## Call trace (component/render flow)

```
main.tsx -> App                                 App.tsx        router: / and /product/:id
├─ CatalogPage                                  pages/CatalogPage.tsx
│  ├─ useProducts()                             api.ts         # GET /products once, cached in state
│  ├─ filter chain                              # source chip (all | assignment) -> text match
│  │                                            # on name+brand, both client-side
│  ├─ <SourceFilter>                            # chips: All (50) | Assignment (5)
│  ├─ <SearchInput>                             # controlled input, no debounce needed
│  ├─ <ProductGrid>
│  │  └─ <ProductCard> xN                       components/ProductCard.tsx
│  │     ├─ <SafeImage>                         # hover swaps to hover_image_url
│  │     ├─ <PriceBlock compact>                # strikethrough + sale badge
│  │     └─ Link -> /product/:id
│  └─ <GridSkeleton> | <EmptyState>             # while loading | zero matches
└─ ProductPage                                  pages/ProductPage.tsx
   ├─ useProduct(id)                            api.ts         # GET /products/{id}
   ├─ <Breadcrumb>                              # category path split on " > "
   ├─ <Gallery>                                 components/Gallery.tsx
   │  ├─ variantImages(variants, selections)    lib/resolveVariant.ts  # when compatible
   │  │                                         # variants carry image_urls, the gallery
   │  │                                         # shows ONLY those ("show all" toggle
   │  │                                         # underneath); selections without image
   │  │                                         # data fall back to the full gallery
   │  ├─ thumb rail                             # <SafeImage>; broken thumbs drop from rail
   │  └─ main pane                              # 4:5; video as last slide, poster +
   │                                            # "open original" link on error
   ├─ <BuyBox>
   │  ├─ <PriceBlock>                           components/PriceBlock.tsx
   │  │  └─ formatPrice()                       lib/format.ts  # Intl.NumberFormat from the
   │  │                                         # extracted currency code (JPY, SEK, ...)
   │  ├─ <VariantPicker>                        components/VariantPicker.tsx
   │  │  ├─ selection state {axis: value}
   │  │  ├─ resolveVariant(selections, variants)   lib/resolveVariant.ts
   │  │  └─ valueDisabled(axis, value, ...)        lib/resolveVariant.ts
   │  └─ availability line                      # from the resolved variant
   ├─ <Description> + <KeyFeatures>
   └─ <MetaGrid>                                # brand, category, resolved sku
```

### Files

| File | What it does |
|---|---|
| `frontend/src/App.tsx` | Router shell, page transitions |
| `frontend/src/api.ts` | Typed fetch helpers for the two endpoints |
| `frontend/src/types.ts` | `ProductSummary`/`Product`/`Variant`/`Option`, mirroring models.py |
| `frontend/src/pages/CatalogPage.tsx` | Grid, filter chips, search, skeleton/empty states |
| `frontend/src/pages/ProductPage.tsx` | PDP layout, data fetch, not-found state |
| `frontend/src/components/ProductCard.tsx` | Card with hover image swap and sale badge |
| `frontend/src/components/Gallery.tsx` | Thumb rail + main pane + video slide |
| `frontend/src/components/VariantPicker.tsx` | One button group per option axis |
| `frontend/src/components/PriceBlock.tsx` | Price, compare-at strikethrough, %-off badge |
| `frontend/src/components/SafeImage.tsx` | onError placeholder with brand initial |
| `frontend/src/components/ui/` | shadcn primitives (button, badge, input, skeleton) |
| `frontend/src/lib/resolveVariant.ts` | Pure selection-matching logic (unit-testable) |
| `frontend/src/lib/format.ts` | Currency and percent formatting |

## Core data structures

**Picker selection state** and its resolution:

```ts
// user has chosen:
selections = { Color: "Delta Blue", Size: "Medium", Item: "Regular" }

// resolveVariant: a variant matches when EVERY one of its own selections
// agrees with the user's current choices
variant = { selections: [{name: "Color", value: "Delta Blue"},
                         {name: "Size", value: "Medium"},
                         {name: "Item", value: "Regular"}],
            sku: "0ATH201002", price: 29.95, available: true }

// buy box then renders: $29.95 · In stock · SKU 0ATH201002
```

**`valueDisabled(axis, value)`**: true when no variant is compatible with `{...selections, [axis]: value}` ignoring the axis's own current choice. Out-of-stock variants render struck-through but stay selectable; impossible combinations disable.

**Degradation states** (mirror extraction semantics on purpose):

```
variants populated        -> full resolution (llbean: 3 axes, 83 variants)
options but no variants   -> axes render, buy box shows product-level price, a quiet
                             "configuration details on the brand's site" line
no options, no variants   -> no picker at all (ace drill)
available: null           -> availability line omitted, never claimed
```

## Build order

1. Scaffold (Vite + Tailwind + shadcn init), types, api client
2. resolveVariant + unit tests (pure logic first)
3. CatalogPage end to end against the live server
4. ProductPage: gallery, buy box, picker
5. Robustness pass: SafeImage, video fallback, skeletons, empty/404 states
6. Responsive + polish pass (phone width, focus states, transitions)
