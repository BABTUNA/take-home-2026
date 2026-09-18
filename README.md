# Channel3 Take Home

Site-agnostic product extraction: raw PDP HTML from any store in, a validated `Product` out, plus a storefront UI on top of the extracted catalog. No site-specific logic anywhere; the pipeline was built against the 5 assignment pages and then tested on 45 more pages fetched from other stores (`data_unseen/`) across ~15 platforms, 7 currencies, and 3 languages.

The core idea: **code does the finding, the model does the choosing.** Deterministic Python harvests and verifies every candidate fact from the page; a cheap LLM only interprets and selects (media by index, prices only if literally present); validators make bad output impossible to emit.

## Running it

Copy the example config:

```bash
cp .env.example .env
```

Set `OPEN_ROUTER_API_KEY` in `.env`, then install dependencies:

```bash
uv sync
```

Extract the 5 assignment pages (writes `output/*.json`, logs per-call cost):

```bash
uv run python run_extract.py
```

Add `--unseen` for all 50 pages, or pass explicit paths. Optional settings are listed in `.env.example`. The default extraction model is flash-lite; the committed outputs used `google/gemini-3-flash-preview`. If you change `OUTPUT_DIR`, the server still reads `output/`.

Server + frontend (two terminals):

```bash
uv run uvicorn server:app --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Then open http://localhost:5173. The catalog has an All (50) / Assignment (5) filter; clicking a product opens its PDP, where the variant picker resolves selections to a concrete sku/price/availability.

Evaluation:

```bash
uv run python eval/score.py          # per-field scoreboard vs hand-written ground truth
uv run python eval/baseline.py       # no-LLM floor, then: eval/score.py --baseline
uv run python eval/reachability.py   # does ground truth survive distillation?
uv run python eval/taxonomy_bench.py            # exact category accuracy across all 50 outputs
cd frontend && npx vitest run        # variant-resolution unit tests
```

## How it works

Four stages per page (full walkthrough with real data in [BACKEND.md](docs/BACKEND.md), function-level trace in [BACKEND_IMPLEMENTATION.md](docs/BACKEND_IMPLEMENTATION.md)):

1. **Harvest**: five generic channels: JSON-LD, meta tags, embedded JSON blobs (found by shape, never by name), raw script text, visible text with aria-label/alt/title inlined. Media collected with provenance.
2. **Distill**: identity anchoring drops other products' data; blobs are pruned (framework noise out, related products summarized, record lists rendered as compact tables); media deduped by asset and ranked by relevance to the page's own hero; per-section budgets.
3. **Extract**: one structured-output call. The model picks images by `IMG_n` index (hallucinated URLs are unrepresentable) and its prices must literally appear in the evidence. Validation failures drive repair retry, model escalation, then loud failure.
4. **Categorize**: union retrieval (stemmed lexical + local embeddings) shortlists Google's 5,596-path taxonomy, the model picks by number, pydantic guarantees the result exists. No silent fallback.

## Results

Per-field scoring against hand-written ground truth for the 5 assignment pages (`eval/ground_truth/`, with evidence notes for key judgments):

| | name | price | desc | features | images | video | category | colors | variants | overall |
|---|---|---|---|---|---|---|---|---|---|---|
| pipeline | 1.00 | 1.00 | 1.00 | 1.00 | 0.86 | 1.00 | 1.00 | 1.00 | 0.89 | **0.972** |
| no-LLM baseline | 0.80 | 0.45 | 0.85 | 0.20 | 0.28 | 0.80 | 0.00 | 0.20 | 0.40 | 0.442 |

The delta is the measured value of the blob/DOM channels plus the model. All 50 corpus pages extract successfully.

Historical extraction model sweep (same pipeline and ground truth; the committed outputs currently score 0.972):

| extraction model | score | ~cost/page |
|---|---|---|
| google/gemini-3-flash-preview | 0.976 | $0.015-0.023 |
| openai/gpt-5-mini | 0.905 | ~$0.01 |
| google/gemini-2.5-flash-lite | 0.884 | ~$0.003 |

The premium model earns its cost specifically on variant scoping and colorway judgment; flash-lite is the documented budget config via `EXTRACT_MODEL`.

## Category accuracy

[`eval/taxonomy_bench.py`](eval/taxonomy_bench.py) compares the category already saved in each of the 50 `output/*.json` files with the accepted paths in [`eval/expected_categories.json`](eval/expected_categories.json). A page is correct only when `category.name` exactly matches an accepted path. The evaluator reads files only; it makes no model calls.

The committed outputs score **48/50 (96%)** overall and **5/5** on the assignment pages. The two misses are Aerosoft (`Software` instead of a specific software category) and Peak Design (Camera Bags & Cases instead of Backpacks). Run the command above to see the full paths. `--json` prints per-page results; `--strict` exits non-zero if any page is wrong.

| configuration | all 50 pages | assignment 5 | calls/page | cost/page | latency/page |
|---|---:|---:|---:|---:|---:|
| lexical + flash-lite (initial default) | 44/50 | 5/5 | 1 | $0.00034 | 1.2s |
| lexical + flash-lite 3-vote | 44/50 | 5/5 | 3 (parallel) | $0.00102 | 1.1s |
| lexical + 3-flash picker | 43/50 | 5/5 | 1 | $0.00172 | 1.7s |
| union + flash-lite 3-vote | 45/50 | 4/5 | 3 (parallel) | $0.00090 | 1.4s |
| **union + 3-flash picker (shipped)** | **48/50** | **5/5** | 1 | $0.00153 | 1.6s |
| LLM tree walk (no retrieval) | 35/50 | 4/5 | 3.4 (sequential) | ~$0.0004 | ~4s |

The shipped row's accuracy is reproducible from the committed outputs with `eval/taxonomy_bench.py`. The other rows and the cost/latency columns are historical experiment results; their per-config outputs and usage logs are not committed, so the current evaluator cannot regenerate them.

Shortlist recall is a separate diagnostic: it says whether the correct path was offered to the picker, not whether the final product chose it. The category accuracy above measures the final choice.

## Design choices

- **Media by index, never by URL.** The model answers `image_ids: [0, 1, 4]` against a numbered candidate table; a hallucinated URL is structurally impossible, and prices are provenance-gated the same way (a number the page never showed fails the draft).
- **The eval harness is part of the backend, not an afterthought.** Ground truth with per-value evidence notes, a no-LLM baseline as the measured floor, and a reachability check that separates "the distiller lost it" from "the model missed it". Every distill bug found during development was caught by the harness, none by eyeballing outputs.
- **Variants are compositional and honest.** Option axes are stored separately from variant combinations; a variant exists only where the page ties a concrete combination to a sku/price/stock signal, and availability is null without an explicit signal. A page showing 8 colors and 6 sizes without linking them yields two axes, not 48 invented combos.
- **Two calls per page, cheap by default, escalation on failure.** Distillation (300-2300KB of HTML down to ~40-60KB of evidence) is what makes the cheap call viable; the reachability check is what makes aggressive distillation safe.
- **No site-specific anything.** Blobs are found by commerce-key density, noise is removed by framework vocabulary, media is ranked by the page's own hero image, and no prompt contains an example drawn from the assignment data.

## Known limits

- Pages that tie variants through opaque sku records without label joins (L.L.Bean's 83 combos) get conservative variant enumeration; the harness scores it honestly at 0.52 rather than inventing joins.
- Image-set boundaries on multi-colorway pages are judgment calls (llbean 0.75, nike 0.67 image F1); the evidence notes document the calls made.
- Client-rendered shells genuinely lack data in raw HTML; the pipeline emits a partial product from whatever JSON-LD/meta survives (see `data_unseen/vitamix.html`, a 22KB shell) rather than hallucinating completeness.
- Bot walls, not extraction, are the real-world coverage ceiling: ~20 major retailers refused the corpus fetches outright.
- Google's taxonomy itself has gaps (no category for a camera drone); no retrieval strategy fixes a missing answer.
- Deterministic fast path (skip the LLM when JSON-LD alone covers the schema) is designed but deliberately deferred: the baseline shows deterministic-only quality drops exactly where extraction is cheapest to get right.

## Repo map

Working documents kept as process evidence: [PLAN.md](docs/PLAN.md) (initial plan), [BACKEND.md](docs/BACKEND.md) (design walkthrough with real data), [BACKEND_IMPLEMENTATION.md](docs/BACKEND_IMPLEMENTATION.md) / [SERVER_IMPLEMENTATION.md](docs/SERVER_IMPLEMENTATION.md) / [FRONTEND_IMPLEMENTATION.md](docs/FRONTEND_IMPLEMENTATION.md) (function-level specs kept in sync with the code), [EVAL_IMPLEMENTATION.md](docs/EVAL_IMPLEMENTATION.md), [ROADMAP.md](docs/ROADMAP.md).

## System design

*(to be written)*
