# Backend design

Raw PDP HTML in, validated `Product` out. Four layers, each with a concrete shape. All samples below are real data from the files in `/data`.

```
html -> [1 harvest] -> Evidence -> [2 distill] -> PromptContext -> [3 extract] -> draft -> [4 categorize+validate] -> Product
```

Module layout:

```
harvest.py     # html -> Evidence (channels 1-5)
distill.py     # Evidence -> PromptContext (budgets, identity anchoring, media candidates)
extract.py     # PromptContext -> draft product (one LLM call, index-based media)
taxonomy.py    # draft category -> validated taxonomy path
pipeline.py    # orchestration, retries, escalation, fast path
models.py      # Product (provided) + Variant, Evidence, PromptContext
run_extract.py # batch CLI over /data, writes output/*.json
eval/          # ground truth, scorer, baseline, reachability check
```

## Layer 1: Harvest

Collect evidence from five generic channels. No site names, no selectors tied to a page.

**Channel A: JSON-LD.** Every `<script type="application/ld+json">`, parsed with a retry ladder (raw, html-unescaped). From `ace.html`:

```json
{"@type": "Product", "name": "DeWalt 20V MAX 1/2 in. Brushed Cordless Compact Drill Kit",
 "brand": {"name": "DEWALT"}, "sku": "2385458",
 "offers": {"price": "129.00", "priceCurrency": "USD", "availability": "InStock"}}
```

Precise but not trustworthy alone: 129.00 here is the after-instant-savings price. The list price never appears in JSON-LD on this page.

**Channel B: meta tags.** og/twitter/description. This is all `llbean.html` gives you up front (it has zero JSON-LD):

```html
<meta property="og:title" content="Men's Carefree Unshrinkable Tee, Traditional Fit, Henley"/>
<meta property="og:image" content="https://cdni.llbean.net/is/image/wim/224626_0_44"/>
<meta property="og:site_name" content="L.L.Bean"/>
```

**Channel C: embedded JSON blobs.** Any script body that parses as a large JSON object, scored by density of commerce keys (price, sku, image, variant, stock...). Found by shape, never by name. This is the richest channel on 4 of 5 pages. From `adaysmarch.html` (inside a 184KB `__NEXT_DATA__`):

```json
{"priceAsNumber": 170, "priceBeforeDiscountAsNumber": 170,
 "items": [{"item": "44", "stock": 18}, {"item": "46", "stock": 55}, {"item": "48", "stock": 111}],
 "relatedProducts": [{"variantName": "Navy"}, {"variantName": "Oyster"}, {"variantName": "Black"}]}
```

Sizes with live stock counts, and sister colorways as related products. From `nike.html`, the same channel carries video:

```json
{"cardType": "video", "properties": {"videoURL": "https://shortformvideo.nike.com/.../video.mp4"}}
```

Parsing detail: `window.__X__ = {...}; other js` needs `JSONDecoder.raw_decode`, not `json.loads`.

**Channel D: raw inline-script text.** Scripts that don't parse as JSON, kept as text and ranked by keyword density with size caps. The main target is Next.js App Router pages, which stream data as escaped string fragments instead of one JSON object:

```html
<script>self.__next_f.push([1,"{\"product\":{\"name\":\"Trail Runner\",\"price\":{\"amount\":128,\"currency\":\"USD\"},\"sizes\":[\"8\",\"9\",\"10\"]}"])</script>
```

A JSON-blob miner sees no parseable object here, but the product data is plainly in the text, and an LLM reads escaped JSON fine. None of the 5 assignment pages need this channel (they all use the older parseable `__NEXT_DATA__` style), which is exactly the point: it exists for unseen sites on newer frameworks.

**Channel E: visible text with attributes inlined.** Strip script/style/nav/footer, then inline `aria-label`, `alt`, `title` into the text stream. What this looks like on `llbean.html`, where the state blob has 83 skus but no human-readable labels:

```html
<button type="button" role="radio" aria-checked="false" data-test-id="answer-Black" title="Black">
  <img src="https://cdni.llbean.net/is/image/wim/224626_1_41?wid=65..." alt="Color Option: Black, $29.95"/>
</button>
```

becomes this line in the text stream:

```
[option] Black | Color Option: Black, $29.95
```

Three real saves from this channel:

- `article.html` has an empty state blob and no price in JSON-LD. Its price exists only as `<span class="regularPrice">$349</span>`.
- `nike.html` DOM: `<div id="price-container" aria-label="current price £76.99, original price £109.99">`. That one attribute disambiguates price vs compare_at.
- `llbean.html` sale colors: `alt="Sale Color Option: Lake, $24.99"` next to `alt="Color Option: Black, $29.95"` carries per-color sale pricing that exists nowhere else on the page.

## Layer 2: Distill

Evidence bundle down to a few KB of prompt context. Three jobs:

**Budgets per section**, so one bloated channel can't evict another (JSON gets the biggest budget, then text, then media list). Blob subtrees pruned by commerce-key scoring; nav data, i18n bundles, and analytics config are 60-90% of blob bytes and get dropped.

**Identity anchoring.** Compute the page's own identity first (h1, og:title, sku, canonical slug) and drop evidence about other products. Without this, Nike's recommendation rail and 8 sibling colorway products leak into the extraction.

**Media candidates.** Every image URL found anywhere, deduped by terminal asset id, sized-up (srcset largest wins, size params stripped: `imageNNN.jpg?fit=max&w=1200` -> the 2890x1500 original on article), then numbered:

```
IMG_0 https://cdni.llbean.net/is/image/wim/224626_0_44
IMG_1 https://cdni.llbean.net/is/image/wim/224626_1176_41
IMG_2 ...
VID_0 https://shortformvideo.nike.com/a/videos/.../video.mp4
```

Output shape:

```
== IDENTITY ==   title, canonical url, h1
== JSON-LD ==    (channel A, verbatim)
== META ==       (channel B)
== EMBEDDED ==   (channel C, pruned subtrees)
== SCRIPTS ==    (channel D, capped)
== TEXT ==       (channel E, capped)
== MEDIA ==      numbered candidates
```

## Layer 3: Extract

One structured-output call on a cheap model (gemini-2.5-flash-lite class). The model interprets, it never invents:

- Media by index only. It answers `"image_ids": [0, 1, 4]`, code maps back to URLs. A hallucinated URL is unrepresentable.
- Prices are provenance-gated: accepted only if the number literally appears in the evidence.
- The system prompt is a numbered rule spec of site-agnostic patterns. Examples of rules: price field pairs (`currentPrice` vs `priceAfterInstantSavings`: displayed price is the lower, list price is compare_at); description source ladder (JSON-LD verbatim, then meta description, then prose under the title, never reviews or sister-product copy); variant test (option changes a query param = variant, option links to a different URL path = sister product, record the color only).

Model output for the ace page should look like:

```json
{"name": "DeWalt 20V MAX 1/2 in. Brushed Cordless Compact Drill Kit (Battery & Charger)",
 "price": {"price": 129.00, "currency": "USD", "compare_at_price": 159.00},
 "brand": "DEWALT",
 "key_features": ["20V MAX battery and charger included", "1/2 in. chuck", "..."],
 "image_ids": [0, 1, 2, 3, 4, 5],
 "video_id": null,
 "candidate_category": "cordless drills",
 "colors": [], "options": [], "variants": []}
```

Note `variants: []` is correct here: the drill has no purchase options, and the blob's empty `options` array proves it. Empty beats invented.

Variant model (the one open schema decision):

```json
{"options": [{"name": "Color", "values": ["Navy", "Oyster", "Black"]},
             {"name": "Size", "values": ["44", "46", "48"]}],
 "variants": [{"selections": {"Size": "46"}, "sku": "102805-46", "price": 170.0,
               "available": true, "image_ids": [2]}]}
```

Axes and combinations stored separately. A page that shows axes but never links them yields options with no variants, not a fabricated cartesian product.

**Failure path:** pydantic validation fails -> one retry with the error text appended -> escalate to a stronger model -> fail loudly with the page name and reason. No silent partial success.

## Layer 4: Categorize

`candidate_category: "cordless drills"` can't be trusted to match `categories.txt` exactly. So:

1. Lexical scoring of all 5,596 paths against candidate + name + brand + description tokens. Top ~150, plus all top-level entries as a safety net.
2. Second small call: copy exactly one path verbatim from the list.
3. Pydantic validator confirms it exists. Expected pick here: `Hardware > Tools > Drills > Handheld Power Drills`.

Failure = retry once with a wider list, then fail the product. A valid-but-wrong category is the worst outcome, so no default fallback.

## Fast path

Before layer 3: if deterministic evidence already covers the schema (complete JSON-LD offers + images + no variant axes detected), skip the LLM entirely and only run the category step. Measured skip rate goes in the README. This is the biggest cost lever at scale.

## Eval

Built alongside, not after:

- `eval/ground_truth/*.json`: hand-written expected Product per page, with a note per value on where it came from ("blob says msrp 179, currentPrice 159, priceAfterInstantSavings 129; displayed price is 129 with 159 strikethrough, msrp never shown").
- `eval/score.py`: per-field, per-page matrix. Tolerant matchers: images compared by asset id, text by token containment, prices exact.
- `eval/baseline.py`: no-LLM extractor over the same evidence. The scoreboard shows what the model adds and what it costs.
- `eval/reachability.py`: rerun distill with caps raised, assert every ground-truth value is findable in the context. If distillation drops the article price, this names it before the LLM ever runs.

## Cost target

Distilled context of 3-8K tokens, one flash-lite extraction call plus one small category call. Ballpark $0.002-0.005/page, so $20-50K per 10M pages before the fast path, less after. Real measured numbers from `ai.py`'s logger go in the README, per page and per configuration.
