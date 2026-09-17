# Server implementation

## Goal and how it works

Put an HTTP surface on the extracted JSON. No database, no auth, no reshaping layer.

- Loads every `output/*.json` into memory at startup; bad files are logged and skipped, never fatal.
- Two endpoints: summaries for the grid, the full record for the PDP.
- Each product carries `source` ("assignment" or "unseen", derived from which data directory its stem exists in) so the frontend can filter 5 vs 50.
- CORS open to the Vite dev origin.

## Call trace

```
uvicorn server:app                              server.py
└─ startup: load_catalog()                      server.py
   ├─ for f in output/*.json: json.loads(f)     # invalid file -> log + skip
   ├─ _source(stem)                             # data/<stem>.html exists -> "assignment"
   │                                            # data_unseen/ -> "unseen"
   └─ _summary(product)                         # id, name, brand, price, category,
                                                # image_url = image_urls[0],
                                                # hover_image_url = image_urls[1] if any

GET /products                                   server.py
└─ list_products() -> list[ProductSummary]      # in-memory list, no pagination

GET /products/{id}                              server.py
└─ get_product(id) -> ProductDetail             # full extracted Product + id + source
   └─ unknown id -> 404 {"detail": "no product <id>"}
```

### Files

| File | What it does |
|---|---|
| `server.py` | The whole server: startup loader, the two endpoints, summary derivation |
| `output/*.json` | Its only data source, written by `run_extract.py` |

Dependencies added: `fastapi`, `uvicorn`.

## Core data structures

**`ProductSummary`** (grid payload, nothing the grid doesn't render):

```json
{"id": "llbean",
 "name": "Men's Carefree Unshrinkable Tee, Traditional Fit, Henley",
 "brand": "L.L.Bean",
 "price": {"price": 29.95, "currency": "USD", "compare_at_price": null},
 "image_url": "https://cdni.llbean.net/is/image/wim/224626_36814_41",
 "hover_image_url": "https://cdni.llbean.net/is/image/wim/224626_38221_41",
 "category": "Apparel & Accessories > Clothing > Shirts & Tops",
 "source": "assignment"}
```

**`ProductDetail`**: the pipeline's `Product` verbatim (price object, image_urls, video_url, category, colors, options, variants with selections/sku/price/available) plus `id` and `source`. The frontend consumes exactly what extraction emits, so a schema gap would be visible in the UI instead of patched over.

Run: `uv run uvicorn server:app --reload` (port 8000; the frontend's `api.ts` points at it).
