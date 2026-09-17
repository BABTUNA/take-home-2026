## Channel3 Take Home Assignment

Refer to the "Take-Home Engineering Assignment" for setup instructions.

(Full README with run instructions, cost table, and system design coming with the final submission; sections below are filled in as the work lands.)

## Category resolution benchmark

The category field must exactly match 1 of 5,596 Google taxonomy paths. Resolution is two steps: retrieve a candidate shortlist, then an LLM picks one by index (validated to exist). Both steps were benchmarked on 50 pages (the 5 assignment pages plus 45 pages fetched from other stores, `data_unseen/`) against hand-judged accepted categories (`eval/expected_categories.json`; multiple accepted paths where the taxonomy is genuinely ambiguous). Cost and latency measured over 5 representative pages; run it yourself with `uv run python eval/taxonomy_bench.py`.

| config | all 50 pages | assignment 5 only | calls/page | cost/page | latency/page |
|---|---|---|---|---|---|
| lexical + flash-lite (initial default) | 44/50 | 5/5 | 1 | $0.00034 | 1.2s |
| lexical + flash-lite 3-vote | 44/50 | 5/5 | 3 (parallel) | $0.00102 | 1.1s |
| lexical + 3-flash picker | 43/50 | 5/5 | 1 | $0.00172 | 1.7s |
| union + flash-lite 3-vote | 45/50 | 4/5 | 3 (parallel) | $0.00090 | 1.4s |
| **union + 3-flash picker (shipped)** | **48/50** | 5/5 | 1 | $0.00153 | 1.6s |
| LLM tree walk (no retrieval) | 35/50 | 4/5 | 3.4 (sequential) | ~$0.0004 | ~4s |

What the numbers decomposed: every miss of the initial config was a retrieval miss (the right answer never made the lexical shortlist: "Barrel Jeans" shares no tokens with "Pants", "Chronograph" none with "Watches"), while flash-lite's residual errors were judgment (it filed a Gore-Tex jacket under Rain Suits with the right answer on the list). Union retrieval (stemmed lexical top-100 unioned with local bge-small embedding top-50, embeddings cached, ~10ms/page) fixes the first; the stronger picker on its small ~3K-token prompt fixes the second for ~$0.0012/page extra. Majority voting a weak model is strictly worse: it converges on the model's consistent mistakes at 3x the calls. The tree walk (pick a child per level, no shortlist) is cheap but commits early and strands products at shallow levels.

The two remaining misses: a seed packet filed under fresh vegetables, and Peak Design's Everyday Backpack as "Camera Bags & Cases" (arguably correct; it is marketed as a camera backpack). The assignment-5 column is why the unseen corpus exists: every config aces the 5 graded pages, and the differences only show on unseen sites.
