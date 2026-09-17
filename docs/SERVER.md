# Server design

Thin by intent: the backend's product is the extracted JSON, and the server just puts an HTTP surface on it. No database, no auth, per the assignment.

## Endpoints

```
GET /products            -> list[ProductSummary]   # grid payload
GET /products/{id}       -> ProductDetail          # full record for the PDP
```

- `id` is the source file stem (`nike`, `brooklinen`). Stable, human-readable, already unique.
- 404 with a plain message for unknown ids.
- CORS open to the Vite dev origin so `npm run dev` works against `uvicorn` directly.

## Data shapes

`ProductSummary` (everything the grid needs, nothing more):

```json
{"id": "nike",
 "name": "Nike Air Force 1 '07 LV8",
 "brand": "Nike",
 "price": {"price": 76.99, "currency": "GBP", "compare_at_price": 109.99},
 "image_url": "https://static.nike.com/...",
 "hover_image_url": "https://static.nike.com/...",
 "category": "Apparel & Accessories > Shoes",
 "source": "assignment"}
```

`ProductDetail` is the extracted `Product` verbatim plus `id` and `source`. The frontend consumes exactly what the pipeline emits; no reshaping layer to hide schema problems.

`source` is `"assignment"` for pages from `data/`, `"unseen"` for `data_unseen/`, derived from which directory the stem exists in. It powers the catalog's 5-vs-50 filter.

## Implementation notes

- `server.py`, FastAPI + uvicorn. Loads every `output/*.json` into memory at startup; a missing or invalid file is logged and skipped, never fatal.
- Summaries are derived at startup: `image_url` is the product's first image, `hover_image_url` the second when one exists.
- Run: `uv run uvicorn server:app --reload` (README carries the exact commands).

## Deliberate non-features

Pagination, persistence, mutation endpoints, and image proxying are all out: 50 products fit in one response, and the assignment excludes storage. The one production note worth making in the README: at real scale this layer is where cached summaries and a CDN image proxy would live.
