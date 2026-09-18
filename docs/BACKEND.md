# Backend design

The backend turns one product page's raw HTML into a validated `Product`. It does this in four steps: **harvest** possible facts, **distill** them into a compact evidence packet, **extract** a structured draft, then **categorize** it against Google's product taxonomy. Python handles collection and validation; model calls handle choices that require interpretation.

The Ace Hardware drill page in [`data/ace.html`](../data/ace.html) is the running example. Its 663,704 bytes of HTML contain multiple prices and hundreds of media references. The desired output is one product with the current price, the crossed-out price, its own gallery, and an exact category.

## The page's price puzzle

The same page expresses its prices in different places:

| Source | What it says | What it means |
| --- | --- | --- |
| JSON-LD `offers.price` | `129.00 USD` | Current price after instant savings |
| Embedded product JSON `price.price` | `159.00` | Crossed-out price |
| Embedded product JSON `price.msrp` | `179` | Manufacturer's suggested price; not the displayed compare-at price |
| Embedded product JSON `priceAfterInstantSavings` | `129.00` | Confirms the current price |

If extraction stopped at JSON-LD, it would miss the sale presentation. If it treated the largest number as the compare-at price, it would show the wrong strikethrough. The final price is:

```json
{"price": {"price": 129.0, "currency": "USD", "compare_at_price": 159.0}}
```

## 1. Harvest: collect possible facts

[`harvest.py`](../harvest.py) reads five generic evidence channels. It does not contain store-specific selectors or names.

| Channel | What it contributes |
| --- | --- |
| JSON-LD | Structured product, offers, breadcrumbs |
| Meta tags | Page title, description, hero image |
| Embedded JSON | Product state, price fields, variants, media |
| Raw script text | Product clues in scripts that do not parse as JSON |
| Visible text and attributes | Displayed labels, prices, stock |

Here is what each channel looks like in the saved HTML. Long records are shortened to the fields relevant to this example.

**JSON-LD — Ace.** A `script` tag contains a structured `Product` object:

```html
<script type="application/ld+json">
{"@type":"Product","name":"DeWalt 20V MAX 1/2 in. Brushed Cordless Compact Drill Kit (Battery &amp; Charger)",
 "brand":{"name":"DeWalt"},"sku":"2385458",
 "offers":{"price":"129.00","priceCurrency":"USD"}}
</script>
```

**Meta tags — L.L.Bean.** These are HTML attributes, so the harvester reads their `property` and `content` values:

```html
<meta property="og:title" content="Men&#x27;s Carefree Unshrinkable Tee, Traditional Fit, Henley"/>
<meta property="og:image" content="https://cdni.llbean.net/is/image/wim/224626_0_44"/>
```

**Embedded JSON — Ace.** A product-state `script` contains ordinary JSON with price fields that are absent from JSON-LD:

```json
{"price":{"onSale":false,"msrp":179,"price":159}}
```

```json
{"priceAfterInstantSavings":"129.00","currentPrice":"159.00"}
```

Those are two shortened fragments from the same page, not a claim that all four fields are adjacent in one object.

**Raw script text — Made In.** This Next.js Flight payload is a JavaScript call containing escaped, serialized data. These are two excerpts from its long string:

```text
self.__next_f.push([1,"1:I[672985,[\"/_next/static/...
24:[[\"$\",\"title\",\"0\",{\"children\":\"8\\\" Seasoned Carbon Steel Frying Pan - Made In\"}],...
```

**Visible text and attributes — Nike.** The source markup has two visible prices and an `aria-label` that identifies which is which:

```html
<div id="price-container" aria-label="current price £76.99, original price £109.99">
  <span>£76.99</span><span>£109.99</span>
</div>
```

After HTML cleanup and attribute inlining, the relevant part of `visible_text` looks like:

```text
£76.99
£109.99
[current price £76.99, original price £109.99]
```

The harvester also records media URLs with their origin and, for embedded JSON, the key path where each URL appeared. A URL under `selectedProduct` is stronger evidence than one under `relatedProducts`. It keeps `false` and `0` in parsed data because those can be meaningful stock and price signals.

## 2. Distill: make a usable evidence packet

The following is a **small constructed example**, using the Ace drill's price story plus a related tape measure. It shows the actual behavior of three functions in [distill.py](../distill.py). The input and outputs below were checked against those functions; the small example lets us see every change without reading the full Ace HTML.

### 1. Identify this page's product

`_identity_tokens()` reads the page heading, Open Graph title, and document title:

~~~json
{
  "h1": "DeWalt 20V Cordless Drill Kit",
  "meta": {"og:title": "DeWalt Compact Drill"},
  "title": "DeWalt Drill Kit"
}
~~~

It lowercases and tokenizes those strings, removing short words and stopwords:

~~~json
["20v", "compact", "cordless", "dewalt", "drill", "kit"]
~~~

These tokens are the reference for deciding whether a Product block describes **this** page. They are a set in Python; the array above is sorted for display.

### 2. Keep matching JSON-LD and breadcrumbs

Suppose harvest found this single JSON-LD graph:

~~~json
{
  "@graph": [
    {"@type": "Product", "name": "DeWalt 20V Cordless Drill Kit",
     "offers": {"price": "129.00"}},
    {"@type": "Product", "name": "Stanley Tape Measure",
     "offers": {"price": "12.00"}},
    {"@type": "BreadcrumbList", "itemListElement": ["Tools", "Drills"]},
    {"@type": "Organization", "name": "Ace Hardware"}
  ]
}
~~~

`_filter_json_ld()` unwraps the graph. The drill's name matches the identity tokens, the tape measure's does not, and breadcrumbs are kept regardless of name. The Organization node is outside the kept types:

~~~json
[
  {"@type": "Product", "name": "DeWalt 20V Cordless Drill Kit",
   "offers": {"price": "129.00"}},
  {"@type": "BreadcrumbList", "itemListElement": ["Tools", "Drills"]}
]
~~~

### 3. Prune the embedded product blob

In this constructed example, the page also has a product-state blob. This is the compact input to `_prune()`:

~~~json
{
  "product": {
    "name": "DeWalt 20V Cordless Drill Kit",
    "price": {"price": 159, "msrp": 179},
    "stock": 0,
    "available": false,
    "imageUrl": "https://cdn.example.com/products/dewalt/dcd771c2/main-image-1200.jpg?width=400",
    "description": "",
    "widgetHtml": "<iframe src=\"https://widgets.example.com/checkout?campaign=fall-sale\" width=\"600\" height=\"400\"></iframe>"
  },
  "consentPolicy": {"enabled": true},
  "cookiesManager": {"enabled": true},
  "relatedProducts": [{
    "name": "Stanley Tape Measure",
    "price": 12,
    "url": "https://shop.example.com/products/stanley-tape-measure-25ft?ref=related",
    "images": [{"src": "https://cdn.example.com/other.jpg"}],
    "variants": [{"sku": "TAPE-25"}]
  }]
}
~~~

The exact output is:

~~~json
{
  "product": {
    "name": "DeWalt 20V Cordless Drill Kit",
    "price": {"price": 159, "msrp": 179},
    "stock": 0,
    "available": false,
    "imageUrl": ".../main-image-1200.jpg",
    "widgetHtml": "[code]"
  },
  "relatedProducts": [{
    "name": "Stanley Tape Measure",
    "price": 12,
    "url": ".../stanley-tape-measure-25ft"
  }]
}
~~~

Here, camelCase normalization makes `consentPolicy` and `cookiesManager` match the noise-key filter. The empty description disappears; `false` and `0` stay because they may describe stock. Long URLs become short, joinable tails because the separate media table keeps full URLs. The long iframe string becomes `[code]`. Under `relatedProducts`, summary mode keeps shallow name, price, and URL but drops nested images and variants, so another product cannot consume the evidence budget.

After these steps, `distill()` groups and ranks media, numbers the survivors as `IMG_0`, `IMG_1`, and so on, and renders labeled text sections with separate character budgets. The result is a `PromptContext`: the text sent to the model, the full media lookup table, and the identity tokens. The embedded-JSON budget is currently 58,000 characters; [`eval/reachability.py`](../eval/reachability.py) checks whether expected facts survive the cuts on the five assignment pages.

## 3. Extract: choose values from the packet

[`extract.py`](../extract.py) asks for a structured `Draft`. It selects image **indices**, not URLs, and returns category phrases rather than trying to spell an exact taxonomy path. A shortened Ace draft looks like this:

```json
{
  "name": "DeWalt 20V MAX 1/2 in. Brushed Cordless Compact Drill Kit (Battery & Charger)",
  "price": 129.0,
  "currency": "USD",
  "compare_at_price": 159.0,
  "image_ids": [0, 1, 2, 3, 4, 5, 6, 7],
  "video_id": null,
  "candidate_categories": ["cordless drill", "handheld power drill"],
  "brand": "DeWalt",
  "colors": [],
  "options": [],
  "variants": []
}
```

The category hints are illustrative. Ace has no selectable options, so `variants` is empty.

[`pipeline.py`](../pipeline.py) rejects prices absent from the evidence, invalid image IDs, or an empty image selection when candidates exist. It retries with feedback, escalates to a stronger model, then fails if necessary. Gemini 2.5 Flash-Lite is the default extractor; `EXTRACT_MODEL` and `ESCALATION_MODEL` override the models. These checks verify provenance, not meaning: both `159` and `179` appear on Ace's page, so the prompt rules distinguish the displayed compare-at price from MSRP.

## 4. Categorize and assemble

[`taxonomy.py`](../taxonomy.py) narrows the 5,596 paths in [`categories.txt`](../categories.txt) before asking the model to choose. **Lexical** retrieval ranks paths by shared words, weighting matches in the final category name more heavily. The default **union** mode keeps the first 100 lexical paths, adds up to 50 embedding matches that were not already present, then appends top-level categories as a fallback. Setting `TAXONOMY_RETRIEVAL=lexical` skips the embedding step.

For the shortened Ace hints above (`cordless drill`, `handheld power drill`), the current code produces this excerpt using Ace's name and description but no breadcrumb:

```json
{
  "lexical": {
    "0": "Hardware > Tools > Drills > Handheld Power Drills",
    "1": "Electronics > Electronics Accessories > Power > Battery Accessories > General Purpose Battery Chargers",
    "total": 100
  },
  "embedding_only_in_union": {
    "100": "Hardware > Tool Accessories > Power Tool Batteries",
    "101": "Hardware > Tool Accessories > Drill & Screwdriver Accessories > Drill Chucks",
    "total_added": 34
  },
  "top_level_fallback_starts_at": 134
}
```

Ace's correct path is already first in the lexical results. Union matters more when page wording and taxonomy wording differ, such as "Barrel Jeans" versus "Pants".

The model sees the numbered union list and returns an index. For example, a pick of `{"index": 0}` selects the handheld-drill path above. `Category` checks that the path exists in the taxonomy, producing:

```json
{"category": {"name": "Hardware > Tools > Drills > Handheld Power Drills"}}
```

The index is an illustration, not a saved model response; the committed Ace output contains the category shown. An invalid index triggers one retry with a wider shortlist, then an explicit failure. `resolve_draft()` maps the draft's `image_ids: [0, 1, 2, 3, 4, 5, 6, 7]` to full media URLs and assembles the final [`Product`](../models.py):

```json
{
  "name": "DeWalt 20V MAX 1/2 in. Brushed Cordless Compact Drill Kit (Battery & Charger)",
  "price": {
    "price": 129.0,
    "currency": "USD",
    "compare_at_price": 159.0
  },
  "description": "The DCD771C2 20V MAX Lithium Ion Compact Drill/Driver Kit is lightweight and compact for working in tight spaces for long periods of time. High-speed transmission delivers 2-Speed variations allowing users to choose the level of performance needed for various applications.",
  "key_features": [
    "Compact, lightweight design fits into tight areas",
    "High performance motor delivers 300 unit watts out (UWO) of power ability completing a wide range of applications",
    "1/2 in. single sleeve ratcheting chuck provides tight bit gripping strength",
    "Ergonomic handle delivers comfort and control"
  ],
  "image_urls": [
    "https://cdn-tp6.mozu.com/24645-37138/cms/37138/files/f7b42b30-cf5a-4829-be02-76bf93727867?_mzcb=_1767877756410",
    "https://cdn-tp6.mozu.com/24645-37138/cms/37138/files/b9e63a53-01c5-44c6-97e2-9c78a7ed2a10",
    "https://cdn-tp6.mozu.com/24645-37138/cms/37138/files/373f3cb5-5ecf-4a57-b7d6-510d8698e977",
    "https://cdn-tp6.mozu.com/24645-37138/cms/37138/files/9e7c188d-d2b8-4217-b5f8-48b29fd445b1",
    "https://cdn-tp6.mozu.com/24645-37138/cms/37138/files/f27d0a21-766f-4f84-9927-30cf76b27188",
    "https://cdn-tp6.mozu.com/24645-37138/cms/37138/files/1b434a2e-7528-42cc-a489-83177395594b",
    "https://cdn-tp6.mozu.com/24645-37138/cms/37138/files/bb222bcf-a069-4d02-b9d9-f23410f64138",
    "https://cdn-tp6.mozu.com/24645-37138/cms/37138/files/028f1d30-dca3-4b44-84ef-40a6f3b149e8"
  ],
  "video_url": null,
  "category": {
    "name": "Hardware > Tools > Drills > Handheld Power Drills"
  },
  "brand": "DeWalt",
  "colors": [],
  "options": [],
  "variants": []
}
```

## What variants mean

`options` lists the axes a page offers; `variants` lists only combinations the page ties to a SKU, price, or stock signal. The two stay separate because seeing six sizes and eight colors does not prove all 48 combinations exist.

```json
{
  "options": [{"name": "Size", "values": ["44", "46", "48"]}],
  "variants": [{
    "selections": [{"name": "Size", "value": "46"}],
    "sku": "1028055046S",
    "price": 170.0,
    "available": true
  }]
}
```

Availability stays `null` when the page gives no explicit stock signal. Color links to separate product URLs can be listed as colors or options without inventing purchasable combinations on the current page. L.L.Bean's 83 SKU records are a known hard case: some combinations cannot be joined confidently to their labels from static HTML, so the extractor leaves them out.

## How to verify the design

- **Quality:** [`eval/score.py`](../eval/score.py) compares the five assignment pages with [hand-written ground truth](../eval/ground_truth/): **0.972** overall versus **0.442** for the [no-model baseline](../eval/baseline.py). All 50 collected pages produced output.
- **Evidence:** [`eval/reachability.py`](../eval/reachability.py) checks that expected facts survive distillation.
- **Cost:** The normal path uses two model calls per page; failures can trigger retries. The [README](../README.md) has measured extraction costs and exact category accuracy. A deterministic fast path remains future work.
