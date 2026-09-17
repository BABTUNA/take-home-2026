# Remaining work

What's left after the core pipeline, in build order.

## 1. Eval harness (`eval/`)

Prove the pipeline works instead of claiming it.

- `eval/ground_truth/*.json`: hand-written expected Product for each of the 5 graded pages. Every value gets an evidence note saying where it came from and why (e.g. the ace price triple: msrp 179 / current 159 / after-savings 129, displayed price is 129 with 159 strikethrough).
- `eval/score.py`: per-field, per-page scoreboard. Tolerant matchers: images by asset id, text by token containment, prices exact, variants by selection pairs. Non-zero exit below 100% so it works in CI.
- `eval/baseline.py`: no-LLM extractor over the same Evidence (JSON-LD + meta only). The scoreboard shows AI vs baseline, which is the measured answer to "what does the model add".
- `eval/reachability.py`: rerun distill with budgets raised, assert every ground-truth value is findable in the context. Separates "distiller lost it" from "model missed it".

Done when: one command prints the matrix, AI beats baseline, and every miss is explained or fixed.

## 2. Deterministic fast path

In `pipeline.py`: when JSON-LD + meta already give name, price, currency, description, brand, and images, and no option axes are detected, skip the extraction call and only run the category step. Log which path each page took; the measured skip rate goes in the README. Biggest cost lever at scale.

## 3. Server + frontend

- `server.py`: FastAPI, two endpoints: `GET /products` (grid data) and `GET /products/{id}` (full detail). Serves `output/*.json`; no database.
- `frontend/`: Vite + React + shadcn. Two views only. Catalog: responsive grid of image/name/brand/price with sale badges. PDP: gallery (video as last slide), price with compare-at strikethrough, description, key features, and a variant picker that resolves selections to a concrete sku/price/availability, which doubles as proof the variant model holds real data. Broken images get a graceful fallback since extracted URLs come from the wild.

Done when: `uv run python server.py` + `npm run dev` shows all extracted products, including the unseen ones, and the picker works on llbean's 83 variants.

## 4. README + system design

- Setup and run instructions for ingestion, server, frontend, eval.
- Measured cost table per page and per configuration from ai.py's logs, with the fast-path skip rate.
- Design-choice bullets, each with the reason.
- Known limits, stated honestly (bot walls, client-rendered shells, per-size stock).
- System design section written last, in my own words: scaling 5 -> 50M and the agentic shopping API, grounded in the measured numbers from this repo.

Done when: a reviewer can clone, run everything from the README alone, and verify the claims.
