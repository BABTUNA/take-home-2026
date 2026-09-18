# Backend implementation

(Kept in sync with the code as it evolved.)

## Goal and how it works

Turn raw PDP HTML from any store into a validated `Product`, cheaply, with no site-specific logic.

- Deterministic code harvests evidence from five generic channels (JSON-LD, meta tags, embedded JSON blobs, raw script text, visible text with attributes inlined), tracking where each media URL was found.
- Distillation cuts that to a budgeted prompt context: identity anchoring drops other products, blob pruning summarizes related-product subtrees and stubs URLs, media is deduped by full-path asset identity and ranked by hero-stem hits, blob key path (selected boosts, related demotes), and channel spread, with hard exclusions for related-rail and unselected-colorway media.
- One structured-output call on a cheap model fills the schema. Media by IMG_n/VID_n index, prices provenance-gated in code, category hints forced to be an English list of synonyms by the schema itself.
- Category resolution uses union retrieval (stemmed lexical top-100 plus local-embedding top-50) and a gemini-3-flash pick call. The saved category choices score 48/50 against accepted paths in eval/taxonomy_bench.py; the result is validated against categories.txt with no silent fallback.
- Failure path: repair retry with the validation error, model escalation, then a loud raise. 50/50 corpus pages currently extract.

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
      │  ├─ _collect_media()                    # img/srcset/data-src/source/preload/meta/blob
      │  │                                      # walk; beacon+pixel filter; derived
      │  │                                      # query-stripped twins; path hints MERGED
      │  │                                      # across sightings of the same URL
      │  └─ _extract_visible_text()             # strip chrome, inline aria-label/alt/title
      ├─ distill(evidence) -> PromptContext     distill.py
      │  ├─ _identity_tokens()                  # h1 + og:title + title tokens
      │  ├─ _filter_json_ld()                   # Product blocks matching identity + breadcrumbs
      │  ├─ _prune()                            # noise keys out (camelCase-normalized so
      │  │                                      # consentPolicy etc. match), summary mode for
      │  │                                      # related-product subtrees, URLs stubbed to
      │  │                                      # .../tail, embedded js/html strings -> [code],
      │  │                                      # empty strings dropped
      │  ├─ _resolve_media()                    # group/rank media, then filter related
      │  │                                      # or unselected product images
      │  │  ├─ _asset_key()                     # group renditions of the same asset
      │  │  ├─ _hero_stems()                    # hero URL identifiers for ranking
      │  │  └─ _quality()                       # choose the best URL per asset
      │  ├─ _render_identity()                 # h1/title/canonical section
      │  ├─ _fit_blobs()                       # spend blob budget in score order
      │  │  └─ _tabulate()                     # compact same-shaped record lists
      │  ├─ _render_media()                    # numbered IMG_n/VID_n table
      │  └─ _fit()                             # cap each section at its budget
      ├─ _draft_with_retries(ctx) -> Draft      pipeline.py
      │  ├─ extract_draft(ctx, model)           extract.py    # 13-rule sectioned prompt
      │  │  └─ ai.responses(text_format=Draft)  ai.py
      │  └─ _provenance_problems(draft, ctx)    pipeline.py   # prices on page, indices in range
      │     # fail -> repair retry -> escalate model -> raise
      ├─ _breadcrumb_hint(ctx)                  pipeline.py   # BreadcrumbList names for the query
      ├─ taxonomy.resolve(hints, name, ...)     taxonomy.py
      │  ├─ _union_shortlist(query, embed_q)    # lexical + embedding + top-level paths
      │  │  ├─ shortlist()                      # stemmed lexical top-100; top-300 on retry
      │  │  └─ _embed_shortlist()               # cached bge-small cosine top-50
      │  └─ ai.responses(text_format=_Pick)     ai.py         # Gemini 3 Flash picks by index
      └─ resolve_draft(draft, ctx, category)    extract.py    # assemble Product; drop selection-less variants
         └─ media_by_index()                    distill.py    # separate image/video URL lists
```

### Files

| File | What it does |
|---|---|
| `models.py` | Provided schema + `Selection`/`Option`/`Variant`, LLM-facing `Draft` (`candidate_categories` is a list so the schema forces synonyms), internal `Evidence`/`JsonBlob`/`ScriptText`/`MediaCandidate` (with `path_hint`)/`PromptContext` |
| `harvest.py` | HTML in, `Evidence` out. Five channels + media collection with provenance |
| `distill.py` | `Evidence` in, `PromptContext` out. Identity, pruning, media identity/ranking/exclusions, budget waterfall |
| `extract.py` | The 13-rule extraction prompt (sectioned: ground rules / price / text / media / variants / category hint), the call, draft-to-Product assembly |
| `taxonomy.py` | Union retrieval + index pick + validation; config justified by the bench in its docstring; `PICK_MODEL` and `TAXONOMY_RETRIEVAL=lexical\|union` env overrides |
| `pipeline.py` | Orchestration: retries, escalation, provenance gating, breadcrumb hint |
| `ai.py` | Provided OpenRouter wrapper with cost logging (unchanged) |
| `run_extract.py` | Batch CLI: `data/`, `--unseen`, or explicit paths; `OUTPUT_DIR`/`EXTRACT_MODEL` env overrides |
| `eval/ground_truth/` | Expected Product per graded page with `_evidence_notes` per value |
| `eval/score.py` | Page x field scoreboard, tolerant matchers, CI exit code |
| `eval/baseline.py` | No-LLM floor (JSON-LD + meta only): 0.44 vs pipeline 0.97 |
| `eval/reachability.py` | Asserts every ground-truth value survives distillation |
| `eval/expected_categories.json` | Hand-judged accepted category sets for all 50 corpus pages |
| `eval/taxonomy_bench.py` | Exact accuracy of saved category choices across all 50 pages |
| `server.py` | (planned) FastAPI serving extracted products |

## Core data structures

**`Evidence`** (harvest output): everything found, nothing judged.

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
  media=[MediaCandidate(url="https://...", kind="image", origin="blob", width=1728,
                        path_hint=".props.pageProps...mediaObjects.sources.full.url")],
)
```

**`PromptContext`** (distill output): the sectioned string plus the media table for index resolution.

```python
PromptContext(
  text="== IDENTITY ==\n...\n== JSON-LD ==\n...\n== MEDIA CANDIDATES ==\nIMG_0 https://...\nVID_0 https://...",
  media=[...],                # images first then videos; position within kind == IMG_n / VID_n
  identity_tokens={"miller", "cotton", "lyocell", "trousers"},
)
```

**`Draft`** (LLM output): indices instead of URLs, flat price fields, category hints as a forced list.

```json
{"name": "Miller Cotton Lyocell Trousers",
 "price": 170.0, "currency": "USD", "compare_at_price": null,
 "description": "...", "key_features": ["..."],
 "image_ids": [0, 1, 2, 3], "video_id": 0,
 "candidate_categories": ["trousers", "pants", "menswear bottoms"],
 "brand": "A Day's March",
 "colors": ["Iron", "Navy", "Oyster", "Black", "Olive", "Dark Brown", "Light Khaki"],
 "options": [{"name": "Size", "values": ["44", "46", "48", "50", "52", "54"]}],
 "variants": [{"selections": [{"name": "Size", "value": "46"}],
               "sku": "1028055046S", "price": 170.0, "available": true, "image_ids": []}]}
```

**`Product`** (final): indices resolved to URLs, selection-less variants dropped, category replaced by a validated taxonomy path.

```json
{"name": "Miller Cotton Lyocell Trousers",
 "price": {"price": 170.0, "currency": "USD", "compare_at_price": null},
 "image_urls": ["https://adaysmarch.centracdn.net/client/dynamic/images/..."],
 "video_url": "https://adaysmarch.centracdn.net/client/dynamic/attributes/531/miller_2x3.mp4",
 "category": {"name": "Apparel & Accessories > Clothing > Pants"},
 "options": [{"name": "Size", "values": ["44", "46", "48", "50", "52", "54"]}],
 "variants": [{"selections": [{"name": "Size", "value": "46"}],
               "sku": "1028055046S", "price": 170.0, "available": true, "image_urls": []}]}
```

The invariant across all four shapes: every URL, price, and label in a later structure must be traceable to a field in `Evidence`. The model narrows, it never adds.

## Measured state

- Graded pages (eval/score.py): 0.972 overall, 3 of 5 at 1.00; no-LLM baseline 0.442. The imperfect fields are llbean and nike images/variants (llbean's 83 sku combos have no statically joinable labels; multi-colorway image boundaries).
- Historical extraction model sweep: gemini-3-flash 0.976, gpt-5-mini 0.905, flash-lite 0.884. The premium model earns its cost on variant scoping and colorway judgment; flash-lite remains the budget config via `EXTRACT_MODEL`.
- Category outputs (eval/taxonomy_bench.py): 48/50 accepted paths overall and 5/5 graded pages; Aerosoft and Peak Design are the two misses.
- Corpus: 50/50 pages extract (5 graded + 45 unseen across ~15 platforms, 7 currencies, 3 languages).
- Cost: ~$0.003/page flash-lite config, ~$0.02/page gemini-3-flash config on the heaviest pages, + ~$0.002 category pick.
