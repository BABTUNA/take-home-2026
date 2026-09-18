# Eval implementation

## Goal and how it works

Prove the pipeline works with numbers a reviewer can regenerate, not adjectives.

- Hand-written ground truth for the 5 graded pages, with evidence notes for key judgment calls (what counts as a variant, which price is the display price).
- A scorer that compares extracted output to ground truth per field, prints a page x field matrix, and exits non-zero when the overall score is below 0.999.
- A no-LLM baseline extractor over the same harvested evidence. Running the scorer with `--baseline` gives a measured floor for comparison with the pipeline.
- A reachability check that re-runs distillation with raised budgets and checks representative ground-truth facts in the prompt context. A miss here points to lost evidence; a score-only miss points to model selection or interpretation.
- A category evaluator that checks the path actually saved for each of 50 pages against its accepted paths.

## Call trace

```
main()                                            eval/score.py
├─ read ground_truth/*.json + matching output/*.json
│  (or output_baseline/*.json with --baseline)
├─ score_product(gt, out) -> (scores, details)
│  ├─ match_name()                                # normalized exact or prefix
│  ├─ match_price()                               # price/currency/compare-at
│  ├─ match_text()                                # description token overlap
│  ├─ match_features() -> match_text()            # each expected feature
│  ├─ match_images()                              # asset-key set F1
│  ├─ match_video()                               # URL without query string
│  ├─ match_category()                            # exact=1; ancestor=0.5
│  ├─ match_colors()                              # normalized set F1
│  └─ match_variants()                            # selections + price/stock;
│                                                # count/axes for shape-only truth
└─ print matrix, averages, and misses (or --json); exit 1 if overall < 0.999

main()                                            eval/baseline.py
└─ for each data/*.html: baseline_extract(html) -> output_baseline/<page>.json
   ├─ harvest(html)                               # collect JSON-LD and meta
   ├─ _ld_products(ev.json_ld)                    # first Product/ProductGroup
   ├─ JSON-LD fields, then meta/title fallbacks  # name, description, image
   └─ shortlist(name + brand + description)      # first valid path or empty

main()                                            eval/reachability.py
├─ temporarily raise distill budgets and media cap
└─ for each ground-truth page: harvest(html) -> distill.distill(ev)
   └─ check_page(gt, ctx.text, media_keys) -> missing labels
      ├─ name: at least 80% of tokens; brand: literal text
      ├─ _price_forms()                           # numeric display forms
      ├─ features: longest word of each bullet
      ├─ colors + variant values: literal, case-insensitive
      └─ media: expected asset keys in candidate table
   -> print misses; exit 1 on any miss; restore budgets/media cap

main()                                            eval/taxonomy_bench.py
├─ score_categories(output_dir)
│  ├─ read expected_categories.json              # accepted paths for 50 pages
│  ├─ read each output/<page>.json                # saved category.name
│  ├─ exact membership in that page's accepted paths
│  └─ count correct across all 50 and the 5 graded pages
└─ print_report() or --json                       # totals and wrong selections
   -> --strict exits 1 if any page is wrong; no model calls
```

### Files

| File | What it does |
|---|---|
| `eval/ground_truth/*.json` | Expected `Product` per graded page + `_evidence_notes` per value |
| `eval/score.py` | The scoreboard: tolerant per-field matchers, matrix output, CI exit code |
| `eval/baseline.py` | Deterministic JSON-LD/meta-only extractor, the measured floor |
| `eval/reachability.py` | Checks representative ground-truth facts after distillation with raised budgets |
| `eval/expected_categories.json` | Accepted Google taxonomy paths for the 50 benchmark pages |
| `eval/taxonomy_bench.py` | Grades saved category choices against accepted paths for all 50 pages |

The category evaluator reads saved outputs directly. It does not rerun retrieval or picking, so its accuracy measures the final category choice rather than candidate-list coverage. The README also shows historical configuration comparisons; only the shipped row's accuracy is reproducible from the committed outputs.

## Core data structures

**Ground truth file** (`eval/ground_truth/ace.json`): the expected `Product` plus underscore-prefixed evidence notes. The scorer ignores the notes.

```json
{"_source_file": "data/ace.html",
 "_evidence_notes": {
   "price": "Preload blob: msrp=179, price=159, priceAfterInstantSavings=129. Displayed: $129 with $159 strikethrough; 179 never renders.",
   "variants": "Blob options/variations arrays are empty; single-SKU product. Correct answer is []."},
 "name": "DeWalt 20V MAX 1/2 in. Brushed Cordless Compact Drill Kit (Battery & Charger)",
 "price": {"price": 129.0, "currency": "USD", "compare_at_price": 159.0},
 "category": {"name": "Hardware > Tools > Drills > Handheld Power Drills"},
 "variants": []}
```

**Scorer output** (excerpt from the current committed outputs):

```
                   name    price     desc     feat     imgs    video      cat   colors variants     page
ace                1.00     1.00     1.00     1.00     1.00     1.00     1.00     1.00     1.00     1.00
llbean             1.00     1.00     1.00     1.00     0.60     1.00     1.00     1.00     0.52     0.90
nike               1.00     1.00     1.00     1.00     0.70     1.00     1.00     1.00     0.94     0.96
field avg          1.00     1.00     1.00     1.00     0.86     1.00     1.00     1.00     0.89

OVERALL 0.972

details below 100%:
  llbean/imgs: missing 17 of 30 expected images
  llbean/variants: variant count 3 vs 83, axes ['color', 'item', 'size'] vs ['color', 'size']
  nike/imgs: 7 unexpected images
```

Running `eval/score.py --baseline` separately reports **0.442** overall.

**Matcher contract**: every matcher returns a float in [0, 1] plus a list of human-readable misses. No matcher may import from `extract.py` or call a model; the scorer must stay deterministic and instant.
