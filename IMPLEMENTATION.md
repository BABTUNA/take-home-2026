# Backend implementation

## Goal and how it works

Turn raw PDP HTML from any store into a validated `Product`, cheaply, with no site-specific logic.

- Deterministic code harvests evidence from five generic channels (JSON-LD, meta tags, embedded JSON blobs, raw script text, visible text with attributes inlined).
- The evidence is distilled to a few KB: identity anchoring drops other products (including extra JSON-LD blocks for related items), per-section budgets keep channels balanced, media URLs are deduped and numbered.
- One cheap structured-output LLM call fills the schema. It picks media by index and can only use prices that appear in the evidence, so it can't hallucinate URLs or numbers. Page language is preserved, currency comes from explicit codes, never symbols.
- Category is resolved separately: lexical shortlist from the 5,596-path taxonomy, model copies one verbatim, pydantic validates it exists.
- Failures retry once with the validation error, then escalate to a stronger model, then fail loudly. A fast path skips the LLM when deterministic evidence already covers the schema.

## Call trace

```
main()                                         run_extract.py
└─ extract_product(html)                       pipeline.py
   ├─ harvest(html) -> Evidence                harvest.py
   │  ├─ extract_json_ld()
   │  ├─ extract_meta()
   │  ├─ extract_json_blobs()                  # raw_decode + commerce-key scoring
   │  ├─ extract_script_text()                 # non-JSON scripts, keyword-density ranked
   │  ├─ extract_visible_text()                # aria-label/alt/title inlined
   │  └─ collect_media()
   ├─ distill(evidence) -> PromptContext       distill.py
   │  ├─ page_identity()                       # h1 + og:title + sku + canonical slug
   │  ├─ filter_by_identity()                  # applies to JSON-LD blocks too
   │  ├─ prune_blobs()                         # keep commerce subtrees, drop nav/i18n
   │  ├─ resolve_media()                       # dedupe by asset id, srcset largest, number IMG_n/VID_n
   │  └─ render()                              # sectioned string, per-section budgets
   ├─ try_fast_path(evidence) -> Draft | None  pipeline.py
   ├─ extract_draft(context) -> Draft          extract.py
   │  └─ ai.responses(text_format=Draft)       ai.py
   ├─ resolve_draft_media(draft, context)      extract.py    # IMG_n indices -> URLs
   ├─ resolve_category(draft, context)         taxonomy.py
   │  ├─ shortlist()                           # ~150 lexical matches + all top-levels
   │  └─ ai.responses(pick verbatim)           ai.py
   └─ assemble(draft, ...) -> Product          pipeline.py
      └─ on ValidationError: retry_with_error() -> escalate_model() -> raise
```

### Files

| File | What it does |
|---|---|
| `models.py` | Provided `Product`/`Price`/`Category`, plus `Variant`, `Option`, `Evidence`, `PromptContext`, `Draft` |
| `harvest.py` | HTML in, `Evidence` out. The five channels, nothing else |
| `distill.py` | `Evidence` in, `PromptContext` out. Identity, pruning, media table, budgets |
| `extract.py` | The extraction LLM call, its rule-spec prompt, and index-to-URL resolution |
| `taxonomy.py` | Loads categories.txt, shortlists, runs the pick call, validates |
| `pipeline.py` | Orchestration: fast path, retries, escalation, assembly |
| `ai.py` | Provided OpenRouter wrapper with cost logging (unchanged) |
| `run_extract.py` | Batch CLI: runs `data/` and `data_unseen/`, writes `output/*.json`, prints cost table |
| `server.py` | FastAPI: serves extracted products to the frontend |
| `eval/ground_truth/` | Hand-written expected Product per graded page, with evidence notes |
| `eval/score.py` | Per-field, per-page scoreboard with tolerant matchers |
| `eval/baseline.py` | No-LLM extractor over the same Evidence, the measured floor |
| `eval/reachability.py` | Asserts every ground-truth value survives distillation |

## Core data structures

**`Evidence`** (harvest output): everything found, nothing judged yet.

```python
Evidence(
  json_ld=[{"@type": "Product", "name": "...", "offers": {...}}, ...],
  meta={"og:title": "...", "og:image": "...", "description": "..."},
  json_blobs=[JsonBlob(source="script#__NEXT_DATA__", score=0.91, data={...})],
  script_texts=[ScriptText(score=0.42, text='self.__next_f.push([1,"..."')],
  visible_text="Miller Cotton Lyocell Trousers\n$170\n[option] 46 | in stock ...",
  media=[MediaCandidate(url="https://...", kind="image", origin="blob", width=1728)],
)
```

**`PromptContext`** (distill output): the sectioned string the model sees, plus the media table for resolving indices later.

```python
PromptContext(
  identity=PageIdentity(title="Air Force 1 '07 LV8", sku="IO2077-030", canonical="https://www.nike.com/gb/..."),
  text="== IDENTITY ==\n...\n== JSON-LD ==\n...\n== MEDIA ==\nIMG_0 https://...\nVID_0 https://...",
  media=[...],          # index position == IMG_n
  token_estimate=4200,
)
```

**`Draft`** (LLM output, structured): indices instead of URLs, free-text category.

```json
{"name": "Air Force 1 '07 LV8",
 "price": {"price": 76.99, "currency": "GBP", "compare_at_price": 109.99},
 "description": "...", "key_features": ["..."],
 "image_ids": [0, 1, 4, 5], "video_id": 0,
 "candidate_category": "sneakers",
 "brand": "Nike", "colors": ["Black/Iron Grey"],
 "options": [{"name": "Size", "values": ["UK 6", "UK 7", "UK 8"]}],
 "variants": [{"selections": {"Size": "UK 7"}, "sku": "...", "price": 76.99, "available": true}]}
```

**`Product`** (final): `Draft` with indices resolved to URLs and `candidate_category` replaced by a validated taxonomy path.

```json
{"name": "Air Force 1 '07 LV8",
 "price": {"price": 76.99, "currency": "GBP", "compare_at_price": 109.99},
 "image_urls": ["https://static.nike.com/a/images/t_default/..."],
 "video_url": "https://shortformvideo.nike.com/.../video.mp4",
 "category": {"name": "Apparel & Accessories > Shoes > Sneakers"},
 "variants": [{"selections": {"Size": "UK 7"}, "sku": "...", "price": 76.99, "available": true}]}
```

The invariant across all four shapes: every URL, price, and label in a later structure must be traceable to a field in `Evidence`. The model narrows, it never adds.
