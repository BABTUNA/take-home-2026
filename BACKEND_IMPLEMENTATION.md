# Backend implementation

## Goal and how it works

Turn raw PDP HTML from any store into a validated `Product`, cheaply, with no site-specific logic.

- Deterministic code harvests evidence from five generic channels (JSON-LD, meta tags, embedded JSON blobs, raw script text, visible text with attributes inlined).
- The evidence is distilled to a prompt-sized context: identity anchoring drops other products (including extra JSON-LD blocks for related items), per-section budgets keep channels balanced, media URLs are deduped by asset and ranked by relevance to the page's own hero image, then numbered.
- One cheap structured-output LLM call fills the schema. It picks media by index and its prices must literally appear in the evidence, so it can't hallucinate URLs or numbers. Page language is preserved, currency comes from explicit codes, never symbols.
- Category is resolved separately: stemmed lexical shortlist from the 5,596-path taxonomy (the extractor supplies synonyms in its hint), model picks by number, pydantic validates the path exists.
- Failure path: cheap model, one repair retry with the validation error, escalate to a stronger model, then fail loudly. No silent partial success.

## Call trace

```
main()                                          run_extract.py
└─ run_one(path)                                run_extract.py
   └─ extract_product(raw_html)                 pipeline.py
      ├─ harvest(raw_html) -> Evidence          harvest.py
      │  ├─ _extract_page_identity()            # title, h1, canonical
      │  ├─ _extract_json_ld()                  # retry ladder: raw/CDATA/unescaped
      │  ├─ _extract_meta()                     # og:/twitter:/product:/description
      │  ├─ _extract_scripts()                  # channels C+D in one pass
      │  │  ├─ _json_objects_in_script()        # whole-body / `= {` raw_decode / JSON.parse
      │  │  ├─ _commerce_score()                # keeps product blobs, drops config JSON
      │  │  └─ _keyword_density()               # raw text kept when JSON parse fails
      │  ├─ _collect_media()                    # img/srcset/source/preload/meta/blob walk,
      │  │                                      # beacon filter, derived query-stripped twins
      │  └─ _extract_visible_text()             # strip chrome, inline aria-label/alt/title
      ├─ distill(evidence) -> PromptContext     distill.py
      │  ├─ _identity_tokens()                  # h1 + og:title + title tokens
      │  ├─ _filter_json_ld()                   # Product blocks matching identity + breadcrumbs
      │  ├─ _prune()                            # noise keys out, long strings capped
      │  ├─ _resolve_media()                    # dedupe by _asset_key, rank by _hero_stems
      │  │                                      # match + channel spread + _quality
      │  └─ render sections                     # per-section budgets via _fit()
      ├─ _draft_with_retries(ctx) -> Draft      pipeline.py
      │  ├─ extract_draft(ctx, model)           extract.py
      │  │  └─ ai.responses(text_format=Draft)  ai.py
      │  └─ _provenance_problems(draft, ctx)    pipeline.py   # prices on page, indices in range
      │     # fail -> repair retry -> escalate model -> raise
      ├─ taxonomy.resolve(hint, name, ...)      taxonomy.py
      │  ├─ _breadcrumb_hint(ctx)               pipeline.py   # BreadcrumbList names for the query
      │  ├─ shortlist(query, k)                 # stemmed token overlap, leaf-weighted,
      │  │                                      # top 150 + all top-levels
      │  └─ ai.responses(text_format=_Pick)     ai.py         # picks by index; retry k=400 on stronger model
      └─ resolve_draft(draft, ctx, category)    extract.py
         └─ media_by_index()                    distill.py    # IMG_n/VID_n indices -> URLs,
                                                              # drops selection-less variants
```

Not yet implemented (planned): `try_fast_path()` (skip the LLM when deterministic evidence covers the schema), `server.py`, and the `eval/` harness.

### Files

| File | What it does |
|---|---|
| `models.py` | Provided `Product`/`Price`/`Category`, plus `Selection`, `Option`, `Variant`, LLM-facing `Draft`/`DraftVariant`, and internal `Evidence`/`JsonBlob`/`ScriptText`/`MediaCandidate`/`PromptContext` |
| `harvest.py` | HTML in, `Evidence` out. The five channels, media collection, nothing else |
| `distill.py` | `Evidence` in, `PromptContext` out. Identity filtering, blob pruning, media ranking/numbering, section budgets |
| `extract.py` | The 12-rule extraction prompt, the extraction call, and draft-to-Product assembly |
| `taxonomy.py` | Loads categories.txt, stemmed shortlist, index-pick call, escalation, no silent fallback |
| `pipeline.py` | Orchestration: retries, escalation, provenance gating, breadcrumb hint |
| `ai.py` | Provided OpenRouter wrapper with cost logging (unchanged) |
| `run_extract.py` | Batch CLI: `data/` by default, `--unseen` adds `data_unseen/`, or explicit paths; writes `output/*.json` |
| `server.py` | (planned) FastAPI serving extracted products to the frontend |
| `eval/` | (planned) ground truth with evidence notes, per-field scorer, no-LLM baseline, reachability check |

## Core data structures

**`Evidence`** (harvest output): everything found, nothing judged yet.

```python
Evidence(
  title="Miller Cotton Lyocell Trousers | A Day's March",
  h1="Miller Cotton Lyocell Trousers",
  canonical_url="https://www.adaysmarch.com/us/miller-cotton-lyocell-trousers-iron",
  json_ld=[{"@type": "Product", "name": "...", "offers": {...}}],
  meta={"og:title": "...", "og:image": "...", "description": "..."},
  json_blobs=[JsonBlob(source="__NEXT_DATA__", score=2.81, data={...})],
  script_texts=[ScriptText(score=0.62, text='self.__next_f.push([1,"...')],
  visible_text="Miller Cotton Lyocell Trousers\n$170\n[44 | in stock] ...",
  media=[MediaCandidate(url="https://...", kind="image", origin="blob", width=1728)],
)
```

**`PromptContext`** (distill output): the sectioned string the model sees, plus the media table for resolving indices.

```python
PromptContext(
  text="== IDENTITY ==\nh1: ...\n== JSON-LD ==\n...\n== MEDIA CANDIDATES ==\nIMG_0 https://...\nVID_0 https://...",
  media=[...],                # images first then videos; position within kind == IMG_n / VID_n
  identity_tokens={"miller", "cotton", "lyocell", "trousers"},
)
```

**`Draft`** (LLM output, structured): indices instead of URLs, flat price fields, free-text category hint with synonyms.

```json
{"name": "Miller Cotton Lyocell Trousers",
 "price": 170.0, "currency": "USD", "compare_at_price": null,
 "description": "...", "key_features": ["..."],
 "image_ids": [0, 1, 2, 3], "video_id": 0,
 "candidate_category": "trousers pants",
 "brand": "A Day's March",
 "colors": ["Iron", "Navy", "Oyster", "Black"],
 "options": [{"name": "Size", "values": ["44", "46", "48", "50", "52", "54"]}],
 "variants": [{"selections": [{"name": "Size", "value": "46"}],
               "sku": "102805-46", "price": 170.0, "available": true, "image_ids": []}]}
```

**`Product`** (final): `Draft` with indices resolved to URLs, selection-less variants dropped, and `candidate_category` replaced by a validated taxonomy path.

```json
{"name": "Miller Cotton Lyocell Trousers",
 "price": {"price": 170.0, "currency": "USD", "compare_at_price": null},
 "image_urls": ["https://adaysmarch.centracdn.net/client/dynamic/images/..."],
 "video_url": "https://adaysmarch.centracdn.net/client/dynamic/attributes/531/miller_2x3.mp4",
 "category": {"name": "Apparel & Accessories > Clothing > Pants"},
 "options": [{"name": "Size", "values": ["44", "46", "48", "50", "52", "54"]}],
 "variants": [{"selections": [{"name": "Size", "value": "46"}],
               "sku": "102805-46", "price": 170.0, "available": true, "image_urls": []}]}
```

The invariant across all four shapes: every URL, price, and label in a later structure must be traceable to a field in `Evidence`. The model narrows, it never adds.
