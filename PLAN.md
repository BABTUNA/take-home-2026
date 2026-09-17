# Plan

Working notes for how I'm approaching this. The short version: code does the finding, the model does the choosing. Deterministic Python gathers and verifies every candidate fact from the page, a cheap LLM only interprets and selects, and validators make bad output impossible to emit.

## Why this shape

I spent time reading the 5 HTML files before writing anything. What I found drives every decision below:

- Product data hides in a few generic channels, and no single one covers all 5 pages. JSON-LD is precise but thin or misleading (one page's JSON-LD reports the promo price and hides the list price). 4 of 5 pages ship a big embedded JSON state blob with everything: full image sets, variant matrices, stock, even video URLs. One page has an empty blob and its price exists only as visible text.
- Feeding raw HTML to a model is the naive approach and fails twice: 300-780KB pages are expensive at scale, and models hallucinate image URLs. Recent work on DOM pruning (e.g. AXE, arXiv 2602.01838) shows ~98% token reduction is achievable before the model ever sees the page.
- The category field must exactly match 1 of 5,596 taxonomy strings. A model asked cold produces near-misses that fail validation.

## Pipeline

Four stages per page:

1. **Harvest** (deterministic). Collect evidence from five channels: JSON-LD blocks, og/meta tags, embedded JSON blobs (found generically: any large script-tag JSON scored by product-keyword density, never by name or site), raw inline-script text ranked by keyword density (catches Next.js Flight payloads that don't parse as JSON), and visible DOM text with load-bearing attributes inlined (aria-label, alt, title, so swatch names and "current price / original price" labels survive).
2. **Distill** (deterministic). Prune the evidence to a few KB. Per-section token budgets so a bloated blob can't crowd out the meta tags. Identity anchoring: read the page's own h1/og:title/SKU first and drop anything about other products, so recommendation rails and sibling models can't leak in. Image URLs deduped by terminal asset id, full resolution resolved by parsing srcset and stripping size params, verified rather than guessed.
3. **Extract** (one small-model LLM call). Structured output against the Product schema. Images are numbered candidates (IMG_0, IMG_1, ...) and the model answers with indices, so a hallucinated URL is structurally impossible. Prices count only if the harvester literally saw them on the page. The prompt is a numbered rule spec, each rule a site-agnostic pattern (price field-name pairs like currentPrice vs priceAfterInstantSavings, a source-priority ladder for descriptions, a decision procedure for what counts as a variant). On validation failure: one retry with the error appended, then escalate to a stronger model, then fail loudly.
4. **Categorize**. Lexical shortlist of ~150 taxonomy paths plus all top-level entries as a safety net, model copies one verbatim, pydantic validates it exists. No silent fallback: a drill filed under Apparel is worse than an error.

## Variant model

A variant is one purchasable configuration: a list of {name, value} options plus sku, price, availability, and images. Options (the axes a page offers) are kept separately from variants (the combinations the page actually asserts), so a page showing 8 colors and 6 sizes without linking them yields two option axes, not 48 invented combinations. Colorways that live on sister URLs are recorded as colors, not variants.

## Proving it works

- Hand-written ground truth for each page, with notes on where every value comes from and why (these double as a record of judgment calls, like the promo-price page above).
- A per-field scorer with tolerant matchers (asset-id comparison for images, containment for descriptions).
- A no-LLM baseline extractor over the same evidence, as a measured floor showing what the model actually adds.
- A reachability check: re-run preprocessing with caps raised and assert every ground-truth value is still findable. Separates "distiller lost it" bugs from "model missed it" bugs, and lets me tune pruning aggressiveness safely.
- A couple of pages from stores not in the dataset, fetched by me, run through the pipeline and reported honestly, including where it degrades (client-rendered shells genuinely lack variant data in raw HTML, and the right output there is a partial product, not a hallucinated one).

## Cost

Cheap model by default, escalation only on failure. Distillation is what makes this work: a few KB in means fractions of a cent per page. A deterministic fast path skips the LLM entirely when structured data already covers the schema. Measured numbers land in the README table, not adjectives.

## Frontend

FastAPI serving the extracted products, React + shadcn on top. Two views: catalog grid and PDP. The PDP's variant picker resolves selections to a concrete sku, price, and availability, which doubles as proof the variant model holds real data.

## Order of work

1. Models (Variant, evidence types) and module layout
2. Harvest + distill layers with the reachability check
3. Extraction call + taxonomy step
4. Ground truth, scorer, baseline; run on all 5 pages, publish the cost table
5. FastAPI + frontend
6. README + system design write-up

Cut line: everything above ships. Below it, if budget allows: measured full-res image verification by fetching image header bytes, and the fast-path hit-rate measurement.
