# Plan

Core idea: code does the finding, the model does the choosing. Deterministic Python gathers and verifies every candidate fact from the page, a cheap LLM only interprets and selects, and validators make bad output impossible to emit.

## Why this shape

I read the 5 HTML files before writing anything. Three findings drive the design:

- No single data channel covers all 5 pages. JSON-LD is precise but thin or misleading (one page's JSON-LD reports the promo price and hides the list price). 4 of 5 pages ship an embedded JSON state blob with everything: full image sets, variant matrices, stock, video URLs. The fifth has an empty blob, and its price exists only as visible text.
- Raw HTML into a model fails twice: 300-780KB pages are expensive at scale, and models hallucinate image URLs. DOM pruning work like AXE (arXiv 2602.01838) shows ~98% token reduction is achievable first.
- Category must exactly match 1 of 5,596 taxonomy strings. A model asked cold produces near-misses that fail validation.

## Pipeline

Four stages per page:

1. **Harvest** (deterministic). Five channels: JSON-LD, og/meta tags, embedded JSON blobs (found generically: any large script-tag JSON scored by product-keyword density, never by name or site), raw inline-script text ranked by keyword density (catches Next.js Flight payloads that don't parse as JSON), and visible text with aria-label/alt/title inlined so swatch names and price labels survive.
2. **Distill** (deterministic). Prune to a few KB. Per-section token budgets so a bloated blob can't crowd out the meta tags. Identity anchoring: read the page's own h1/og:title/SKU first, drop anything about other products, so recommendation rails can't leak in. Images deduped by terminal asset id, full resolution from srcset parsing and size-param stripping.
3. **Extract** (one small-model call). Structured output against the Product schema. Images are numbered candidates and the model answers with indices, so a hallucinated URL can't exist. Prices count only if the harvester saw them on the page. The prompt is a numbered rule spec of site-agnostic patterns (price field-name pairs like currentPrice vs priceAfterInstantSavings, a source ladder for descriptions, a decision procedure for variants). On validation failure: one retry with the error, then a stronger model, then fail loudly.
4. **Categorize**. Lexical shortlist of ~150 taxonomy paths plus all top-level entries, model copies one verbatim, pydantic validates it exists. No silent fallback. A drill filed under Apparel is worse than an error.

## Variants

A variant is one purchasable configuration: {name, value} options plus sku, price, availability, images. Option axes are stored separately from variant combinations, so a page showing 8 colors and 6 sizes without linking them yields two axes, not 48 invented combos. Colorways on sister URLs are colors, not variants.

## Proving it works

- Hand-written ground truth per page, with notes on where each value comes from (these double as a record of judgment calls).
- Per-field scorer with tolerant matchers (asset-id comparison for images, containment for text).
- A no-LLM baseline over the same evidence, as the measured floor.
- A reachability check: rerun preprocessing with caps raised, assert every ground-truth value is still findable. Separates "distiller lost it" from "model missed it".
- A few pages from stores outside the dataset, reported honestly, including where it degrades (client-rendered shells genuinely lack variant data, and the right output there is a partial product).

## Cost

Cheap model by default, escalate only on failure. Distillation makes this work: a few KB in means fractions of a cent per page. A deterministic fast path skips the LLM when structured data already covers the schema. Measured numbers go in the README table.

## Frontend

FastAPI serving the extracted products, React + shadcn. Catalog grid and PDP. The PDP variant picker resolves selections to a concrete sku, price, and availability, which doubles as proof the variant model holds real data.

## Order of work

1. Models and module layout
2. Harvest + distill with the reachability check
3. Extraction call + taxonomy step
4. Ground truth, scorer, baseline; run all 5 pages, publish the cost table
5. FastAPI + frontend
6. README + system design

Cut line: everything above ships. If budget allows: full-res image verification by fetching image header bytes, and fast-path hit-rate measurement.
