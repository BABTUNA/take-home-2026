# Backend design

Raw PDP HTML in, validated `Product` out. Four stages: harvest grabs everything possibly useful, distill decides what the model gets to see, extract has the model choose values from that evidence, categorize resolves the taxonomy path. Everything except the two model calls is deterministic Python.

```
html -> [harvest] -> Evidence -> [distill] -> PromptContext -> [extract] -> Draft -> [categorize + validate] -> Product
```

The best way to understand it is to follow one real page through: `data/ace.html`, a DeWalt drill kit on Ace Hardware (664KB of raw HTML).

## Stage 1: Harvest

Collect evidence from five generic channels. Nothing here knows about any specific site; blobs are found by shape (large JSON with commerce-looking keys), never by name.

**Channel A: JSON-LD.** Ace ships two blocks. The Product one is rich but has a trap:

```json
{"@type": "Product", "name": "DeWalt 20V MAX 1/2 in. Brushed Cordless Compact Drill Kit",
 "brand": {"name": "DEWALT"}, "sku": "2385458",
 "offers": {"price": "129.00", "priceCurrency": "USD", "availability": "InStock"}}
```

129.00 is the after-instant-savings price. The list price never appears in JSON-LD on this page, so trusting this channel alone gets the sale price with no strikethrough.

**Channel B: meta tags.** Ace has almost none (just `description`), but on `llbean.html` meta is the load-bearing channel because that page has zero JSON-LD:

```html
<meta property="og:title" content="Men's Carefree Unshrinkable Tee, Traditional Fit, Henley"/>
<meta property="og:image" content="https://cdni.llbean.net/is/image/wim/224626_0_44"/>
```

**Channel C: embedded JSON blobs.** Any script body that parses as a large JSON object, scored by density of commerce keys (price, sku, image, variant, stock). Ace's 73KB preload blob resolves the price trap:

```json
{"price": {"msrp": 179, "price": 159}, "currentPrice": "159.00",
 "priceAfterInstantSavings": "129.00",
 "content": {"productImages": [{"src": "https://cdn-tp6.mozu.com/.../files/f7b42b30-..."}]}}
```

Parsing detail: `window.X = {...}; more js` needs `JSONDecoder.raw_decode`, not `json.loads`, and `JSON.parse("...")` arguments need unescaping first.

**Channel D: raw inline-script text.** Scripts that refuse to parse as JSON, kept as text and ranked by keyword density:

```js
self.__next_f.push([1,"{\"product\":{\"name\":\"Nike Pegasus\",\"price\":110}}"])
```

Not clean JSON, so a blob miner sees nothing, but the model still reads `name = Nike Pegasus, price = 110` from the raw text. None of the 5 assignment pages need this channel; it exists for unseen sites on newer frameworks.

**Channel E: visible text with attributes inlined.** Strip script/style/nav/footer, then inline `aria-label`, `alt`, `title` into the text stream:

```html
<button aria-label="Size 10, sold out">10</button>
```

Plain text scraping sees only `10`. Channel E keeps the attribute, so the model sees `10 [Size 10, sold out]`. Three real saves in our data: `article.html`'s price exists only as visible text (`$349`), `nike.html`'s `aria-label="current price £76.99, original price £109.99"` disambiguates price vs compare-at, and `llbean.html`'s variant labels exist only on buttons (`alt="Sale Color Option: Lake, $24.99"`).

**Media collection** runs across all channels and keeps everything with its provenance:

```
main.jpg?w=300      (dom, srcset)
main.jpg?w=1200     (dom, srcset)
main.jpg            (blob, under selectedProduct)
recommended.jpg     (blob, under relatedProducts)
```

Harvest collects all of them and remembers origin and JSON key path; distill later groups the three `main.jpg` renditions into one asset, keeps the cleanest URL, and demotes the recommendation image by its path. Tracking beacons are filtered here, and sized renditions get a query-stripped twin added.

## Stage 2: Distill

Harvest hands distill a messy `Evidence` object: 2 JSON-LD blocks, 6 blobs, ~230 media URLs, 5K of visible text. Distill does seven things in order.

**1. Build the page's identity fingerprint.** Tokenize h1 + og:title + title:

```python
{"dewalt", "20v", "max", "brushed", "cordless", "compact", "drill", "kit", "battery", "charger"}
```

Every later step can now ask: is this evidence about *this* product?

**2. Filter JSON-LD against it.** A Product block whose name shares under ~30% of its tokens with the fingerprint is a related item or bundle component and gets dropped. BreadcrumbLists always survive (they feed the category step). Several `data_unseen/` pages ship JSON-LD for recommended products; this is what keeps them out.

**3. Prune the blobs (top 3 by commerce score).** `_prune()` walks recursively:

- Keys matching framework noise vocabulary (`nav, menu, footer, i18n, analytics, tracking, router, webpack, warehouses...`) are dropped whole. On llbean/nike this is 60-90% of blob bytes.
- A key matching `related|recommend|upsell|cross|similar` flips on **summary mode** for its subtree: sibling products keep shallow scalars (name, price, uri) and lose their nested media/variant/stock structures. Before this rule, A Day's March's `relatedProducts` was 89KB (each colorway embeds its full product record) and ate the entire budget, which is how "Light Khaki" never reached the model.
- URLs longer than 60 chars become `.../f7b42b30-...` stubs. The media table already carries them in full; the stub stays joinable. Empty strings and empty containers vanish; `false` and `0` stay (`available: false` is evidence).

**4. Dedupe media by underlying asset.** `_asset_key()` normalizes away rendition noise: query params, `/2890x1500/` path segments, size words (`thumb, standard, full, square...`), and content-hash tokens (hex-with-letters). So these three group as one asset:

```
.../files/f7b42b30-cf5a-4829-be02-76bf93727867?quality=60&max=480
.../files/f7b42b30-cf5a-4829-be02-76bf93727867?max=100
.../files/f7b42b30-cf5a-4829-be02-76bf93727867
```

The hash rule needs at least one a-f letter, so llbean's numeric asset ids (`224626`) survive as identity. This also collapses Centra-style CDNs that give every rendition its own hash.

**5. Pick the best URL per group, then rank groups by "is this the product?".** Within a group, `_quality()` prefers blob/JSON-LD origin, then clean URLs over `?w=320` renditions, then srcset width. Across groups, relevance ranks by:

- hero-stem hits: identifier tokens from the og:image URL (filename stem `2385458`, or a path segment like `/SKU25289/`), *counted* not boolean, so on a multi-colorway page the displayed colorway (which also matches its color tokens) outranks siblings that share only the product name
- blob key path: found under `relatedProducts` demotes, under `selectedProduct` boosts (framework vocabulary, not site vocabulary)
- channel spread: real product images appear in og + blob + DOM; chrome lives in one channel

Then two hard exclusions, not just rankings: demoted groups are dropped entirely when enough clean ones exist, and when a page explicitly marks a selected product's media, blob-only media outside that marking (other colorways) is dropped. Both earned their place: Nike's 8 sibling colorways and L.L.Bean's fit-guide illustrations each poisoned the gallery before.

**6. Number the survivors.** The model will answer with indices, so a hallucinated URL is unrepresentable:

```
IMG_0 https://cdn-tp6.mozu.com/.../files/f7b42b30-cf5a-4829-be02-76bf93727867
IMG_1 https://cdn-tp6.mozu.com/.../files/b9e63a53-01c5-44c6-97e2-9c78a7ed2a10
VID_0 https://adaysmarch.centracdn.net/.../miller_2x3.mp4
```

**7. Render budgeted sections.** Each channel gets guaranteed room so a 300KB state dump can't evict the meta tags:

```
JSON-LD 20K   META 2K   EMBEDDED JSON 45K   SCRIPT TEXT 8K   VISIBLE TEXT 10K
```

Blobs get a waterfall: the best-scoring blob takes what it needs first, the next sees what remains. Before that rule, llbean's junk `__INITIAL_STATE__` truncated the real `__SERVER_DATA__` mid-sku-list, and the model could only see 11 of 83 skus.

The result for ace: 664KB of HTML becomes a ~50K-char sectioned context (~92% reduction) where every load-bearing fact survived. `eval/reachability.py` proves that survival per page, so budget tuning is safe instead of hopeful.

## Stage 3: Extract

One structured-output call. The model interprets, it never invents:

- Media by index only (`"image_ids": [0, 1, 4]`), resolved back to URLs in code.
- Prices are provenance-gated in code: a price the harvester never saw on the page fails the draft.
- The system prompt is a 12-rule spec of site-agnostic patterns: price field-name pairs (`currentPrice` vs `priceAfterInstantSavings`), a source-priority ladder for descriptions, exact color names, currency from explicit codes with tax-inclusive prices kept as displayed, catalog flags (`status: ACTIVE`) explicitly not counting as stock, and a two-step variant test (option changes a query param = variant; option navigates to another URL = sibling product, record the color only).

Expected draft for the drill:

```json
{"name": "DeWalt 20V MAX 1/2 in. Brushed Cordless Compact Drill Kit (Battery & Charger)",
 "price": 129.0, "currency": "USD", "compare_at_price": 159.0,
 "image_ids": [0, 1, 2, 3, 4, 5, 6, 7], "video_id": null,
 "candidate_category": "cordless drill power tools",
 "brand": "DeWalt", "colors": [], "options": [], "variants": []}
```

`variants: []` is correct: the blob's options array is empty, and a single-configuration product has no variants. Empty beats invented.

**Failure path:** pydantic or provenance failure -> one retry with the error appended -> escalate to the stronger model -> fail loudly with the reason. No silent partial success.

## Stage 4: Categorize

`"cordless drill power tools"` can't be trusted to match `categories.txt` byte-for-byte. So: stemmed lexical scoring over all 5,596 paths (leaf tokens weighted; the extractor's hint includes synonyms like "trousers pants" because stemming can't bridge those), top ~150 plus every top-level as a safety net, second small call picks one by index, pydantic validates it exists. Expected: `Hardware > Tools > Drills > Handheld Power Drills`. On failure: retry with a wider list on the stronger model, then fail the product. A drill silently filed under Apparel is worse than an error.

## Variant model

```json
{"options": [{"name": "Size", "values": ["44", "46", "48", "50", "52", "54"]}],
 "variants": [{"selections": [{"name": "Size", "value": "46"}],
               "sku": "1028055046S", "price": 170.0, "available": true, "image_urls": []}]}
```

Axes (`options`) and combinations (`variants`) are stored separately. A page showing 8 colors and 6 sizes without tying them together yields two axes and no invented cartesian product. A variant exists only where the page asserts a sku/price/stock for that combination, and availability is null without an explicit stock signal.

## Proving it works

`eval/` holds ground truth with per-value evidence notes, a per-field scorer, a no-LLM baseline, and the reachability check. Current numbers: pipeline 0.977 (gemini-3-flash) / ~0.95 (flash-lite) vs deterministic baseline 0.442. The harness caught every distill bug above; none were visible by eyeballing outputs. Known limits are documented, not hidden: llbean ties 83 variant combos through sku records whose labels aren't statically joinable, so conservative enumeration scores 0.53 there.

## Cost

Two calls per page on cheap models, escalation only on failure. Flash-lite lands ~$0.003/page, gemini-3-flash ~$0.015-0.025/page on the heaviest pages; measured numbers per configuration go in the README table. A deterministic fast path (skip the extraction call when structured data already covers the schema) is the next cost lever.
