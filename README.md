# Channel3 Take Home

## Overview

This project turns raw product-page HTML into validated `Product` records and serves them in a catalog and product-detail UI. It was built for the five assignment pages and exercised on 45 additional pages. Extraction uses page evidence rather than store-specific selectors.

## Backend

Copy the config template and add your OpenRouter key as `OPEN_ROUTER_API_KEY` in `.env`:

```bash
cp .env.example .env
```

Install dependencies and extract the five assignment pages:

```bash
uv sync
uv run python run_extract.py
```

Results go to `output/*.json`. Add `--unseen` to process all 50 pages, or pass HTML file paths. Optional model and retrieval settings are in [`.env.example`](.env.example). Keep `OUTPUT_DIR=output` when using the server, which reads that folder.

## Server

Run the API and frontend in separate terminals:

```bash
uv run uvicorn server:app --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Open [localhost:5173](http://localhost:5173). `GET /products` returns catalog cards; `GET /products/{id}` returns the full product. The UI filters assignment and unseen pages and resolves variant selections on the product page. Restart the API after changing `output/` because it loads the catalog at startup.

## How it works

1. **Harvest:** Read JSON-LD, meta tags, embedded JSON, script text, visible text, and media from the raw HTML.
2. **Distill:** Keep evidence for the page's product, remove noise, and number relevant media candidates.
3. **Extract:** A model selects structured fields and media indices. Code checks that prices and image indices appear in the evidence; failed drafts are retried, then escalated.
4. **Categorize:** Lexical and local embedding retrieval shortlist Google taxonomy paths. A model picks an index, and `Category` validates the selected path.

The detailed Ace example is in [`docs/BACKEND.md`](docs/BACKEND.md).

## Results

The five assignment pages have [hand-written ground truth](eval/ground_truth/). The per-field scorer reports:

| Field score | Pipeline | No-LLM baseline |
| --- | ---: | ---: |
| Overall | **0.972** | 0.442 |
| Images | 0.86 | 0.28 |
| Variants | 0.89 | 0.40 |

The baseline uses JSON-LD and meta tags with lexical category matching, so the difference reflects both the extra evidence channels and model selection. All 50 collected pages have saved outputs.

```bash
uv run python eval/score.py
uv run python eval/baseline.py
uv run python eval/score.py --baseline
uv run python eval/reachability.py
uv run python eval/taxonomy_bench.py
cd frontend && npx vitest run
```

`eval/score.py` exits non-zero below 0.999 overall; its current 0.972 result is therefore an expected non-zero exit.

## Categories

[`eval/taxonomy_bench.py`](eval/taxonomy_bench.py) checks the category already saved in each output against the [accepted paths](eval/expected_categories.json). It requires an exact match to one accepted path and makes no model calls.

| Saved outputs | Correct | Accuracy |
| --- | ---: | ---: |
| All 50 pages | **48/50** | **96%** |
| Five assignment pages | **5/5** | **100%** |

The misses are Aerosoft (`Software` rather than a specific software path) and Peak Design (Camera Bags & Cases rather than Backpacks). The evaluator prints the full selected and accepted paths; `--json` provides per-page results.

## Design choices

- **Evidence-bound output:** The model selects media by index, and asserted prices must appear in the page evidence.
- **Conservative variants:** Options are kept separate from purchasable combinations; stock stays unknown without an explicit signal.
- **Visible failures:** Invalid drafts get one repair attempt and a stronger-model attempt before extraction fails.

## Limits

- Raw HTML can omit client-rendered product data, and some stores block collection altogether.
- Complex variant links and multi-color galleries remain difficult: L.L.Bean scores 0.52 on variants; L.L.Bean and Nike image F1 scores are 0.60 and 0.70.
- The taxonomy may be ambiguous or lack a precise path; two of the 50 saved category picks miss the accepted set.

## System design

At 50 million products, I would replace local files and the startup-loaded catalog with a queue of page-ingestion jobs, stateless workers, object storage for raw HTML, and versioned product records in a database and search index. Workers would deduplicate canonical URLs, record source evidence and extraction versions, retry transient fetch failures, and reprocess pages when parsers change. Deterministic checks would handle clear cases; model calls would be reserved for ambiguous evidence and tracked for cost and quality. The current in-memory catalog and per-page model calls would not scale as written.

For shopping applications, I would expose a versioned API for search, filters, product details, offers, variants, availability, and source freshness, with cursor pagination and stable product IDs. Change feeds or webhooks would let clients refresh listings. Typed SDKs and tool schemas would make the same data usable by storefronts and shopping agents, while provenance fields would let them distinguish verified page facts from inferred fields.

Implementation notes: [backend](docs/BACKEND_IMPLEMENTATION.md), [server](docs/SERVER_IMPLEMENTATION.md), [frontend](docs/FRONTEND_IMPLEMENTATION.md), and [evaluation](docs/EVAL_IMPLEMENTATION.md).
