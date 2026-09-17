# Eval implementation

## Goal and how it works

Prove the pipeline works with numbers a reviewer can regenerate, not adjectives.

- Hand-written ground truth for the 5 graded pages, with a note per value saying where on the page it came from. The notes double as a written record of judgment calls (what counts as a variant, which price is the display price).
- A scorer that compares extracted output to ground truth per field with tolerant matchers, prints a page x field matrix, and exits non-zero below 100%.
- A no-LLM baseline extractor over the same harvested evidence. The scoreboard shows AI vs baseline side by side, which is the measured answer to "what does the model actually add".
- A reachability check that re-runs distillation with budgets raised and asserts every ground-truth value is still findable in the prompt context. A miss here is a distiller bug; a miss only in the scorer is a model bug. That separation is the point.

## Call trace

```
main()                                            eval/score.py
├─ load_pairs()                                   # ground_truth/*.json x output/*.json
│                                                 # (or output_baseline/ with --baseline)
└─ score_product(gt, out) -> dict[str, float]     eval/score.py
   ├─ match_name()                                # normalized exact, or clean prefix
   ├─ match_price()                               # price/currency/compare_at, |delta| < 0.01
   ├─ match_text()                                # description/features: token containment >= 0.7
   ├─ match_images()                              # set F1 over asset ids (same _asset_key as distill)
   ├─ match_video()                               # query-stripped equality
   ├─ match_category()                            # exact path string
   ├─ match_colors()                              # set F1, case-insensitive
   └─ match_variants()                            # matched on selection (name, value) pairs;
                                                  # sku/price/available scored on matched pairs
   -> render matrix, field averages, overall %; details for every cell below 100%
   -> --json for CI; exit 1 if anything is imperfect

main()                                            eval/baseline.py
└─ for each page: harvest() -> baseline_extract() -> output_baseline/<page>.json
   ├─ from JSON-LD: name, brand, description, price, images   # first Product node only
   ├─ from meta: og fallbacks for name/description/image
   └─ category: shortlist(og hint)[0] if valid else fail      # no LLM anywhere

main()                                            eval/reachability.py
└─ for each page: harvest() -> distill(raised budgets) -> assert findable:
   ├─ name/brand: token subset of context
   ├─ prices: any of 129 / 129.0 / 129.00 / 129,00 / 12900 forms
   ├─ features: longest word of each bullet
   ├─ colors + variant values: literal, case-insensitive
   └─ media: ground-truth asset ids present in the candidate table
   -> prints per-page missing values; exit 1 on any miss
```

### Files

| File | What it does |
|---|---|
| `eval/ground_truth/*.json` | Expected `Product` per graded page + `_evidence_notes` per value |
| `eval/score.py` | The scoreboard: tolerant per-field matchers, matrix output, CI exit code |
| `eval/baseline.py` | Deterministic JSON-LD/meta-only extractor, the measured floor |
| `eval/reachability.py` | Asserts ground truth survives distillation with budgets raised |

No new pipeline code: the harness imports `harvest`, `distill`, and `taxonomy.shortlist` so it always tests what actually ships.

## Core data structures

**Ground truth file** (`eval/ground_truth/ace.json`): a valid `Product` plus underscore-prefixed keys the scorer ignores as data but prints in failure details.

```json
{"_source_file": "data/ace.html",
 "_evidence_notes": {
   "price": "Embedded preload JSON has msrp=179, currentPrice=159, priceAfterInstantSavings=129. Displayed price is 129 with a 159 strikethrough; 179 never renders.",
   "variants": "The blob's options/variations arrays are empty: this SKU has no variants. Correct answer is []."},
 "name": "DeWalt 20V MAX 1/2 in. Brushed Cordless Compact Drill Kit (Battery & Charger)",
 "price": {"price": 129.0, "currency": "USD", "compare_at_price": 159.0},
 "category": {"name": "Hardware > Tools > Drills > Handheld Power Drills"},
 "variants": []}
```

**Scorer output** (the deliverable a reviewer regenerates in one command):

```
              name  price  desc  feat  imgs  video  cat  colors  variants  | page
ace           1.00  1.00   1.00  0.75  1.00  1.00   1.00  1.00   1.00     | 0.97
nike          1.00  1.00   1.00  1.00  0.92  1.00   1.00  1.00   0.95     | 0.99
...
field avg     1.00  1.00   0.95  0.88  0.94  1.00   1.00  1.00   0.97
OVERALL       0.96        (baseline: 0.71)

details below 100%:
  ace/feat: missing "1/2 in. keyless chuck" (gt note: spec table row 3)
```

**Matcher contract**: every matcher returns a float in [0, 1] plus a list of human-readable misses. No matcher may import from `extract.py` or call a model; the scorer must stay deterministic and instant.
