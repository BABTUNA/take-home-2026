"""Benchmark taxonomy retrieval methods against expected_categories.json.

Methods: lexical (current), bm25, embedding (bge-small via fastembed),
union (lexical top-100 + embedding top-50), treewalk (LLM walks the tree
level by level, no retrieval at all).

Two metrics per method:
  recall  - is an accepted path in the candidate list at all (retrieval quality)
  pick    - does the production pick call land on an accepted path (end to end)

Usage:
    uv run python eval/taxonomy_bench.py --recall          # free, no LLM
    uv run python eval/taxonomy_bench.py --pick lexical bm25 embed union
    uv run python eval/taxonomy_bench.py --treewalk
"""

import argparse
import asyncio
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import ai  # noqa: E402
from harvest import harvest  # noqa: E402
from pipeline import _breadcrumb_hint  # noqa: E402
from distill import distill  # noqa: E402
from taxonomy import CATEGORIES, TOP_LEVEL, _tokens, shortlist, _Pick  # noqa: E402

ROOT = Path(__file__).parent.parent
CACHE = Path(os.environ.get("BENCH_CACHE",
             "/private/tmp/claude-501/-Users-benba-projects/27a0f3a4-7bfe-4e87-aa2f-e54980672536/scratchpad/taxbench"))
CACHE.mkdir(parents=True, exist_ok=True)
PICK_MODEL = "google/gemini-2.5-flash-lite"


def load_pages() -> dict:
    expected = json.loads((ROOT / "eval" / "expected_categories.json").read_text())
    pages = {}
    for stem, accepted in expected.items():
        out = json.loads((ROOT / "output" / f"{stem}.json").read_text())
        pages[stem] = {"accepted": accepted, "name": out["name"], "brand": out["brand"],
                       "description": out["description"][:300]}
    return pages


async def gen_hints(pages: dict) -> dict:
    """candidate_categories per page, generated once and cached (mirrors the
    production draft's hint field without rerunning full extraction)."""
    cache_file = CACHE / "hints.json"
    hints = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    from pydantic import BaseModel

    class Hints(BaseModel):
        candidate_categories: list[str]

    for stem, p in pages.items():
        if stem in hints:
            continue
        r = await ai.responses(PICK_MODEL, [
            {"role": "system", "content":
             "Give 2-4 short English phrases for what this product IS, as alternatives: "
             "the seller's own wording first, then common synonyms, then the US retail "
             "term when it differs. English only, even for foreign products."},
            {"role": "user", "content": f"{p['name']} | {p['brand']} | {p['description']}"},
        ], text_format=Hints)
        hints[stem] = r.candidate_categories
        cache_file.write_text(json.dumps(hints, indent=2))
    return hints


def breadcrumbs() -> dict:
    cache_file = CACHE / "breadcrumbs.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    crumbs = {}
    for d in ("data", "data_unseen"):
        for f in sorted((ROOT / d).glob("*.html")):
            ctx = distill(harvest(f.read_text(encoding="utf-8", errors="ignore")))
            crumbs[f.stem] = _breadcrumb_hint(ctx)
    cache_file.write_text(json.dumps(crumbs, indent=2))
    return crumbs


def query_text(p: dict, hints: list[str], crumb: str) -> str:
    h = " ".join(hints)
    return f"{h} {h} {p['name']} {p['brand']} {crumb} {p['description']}"


# ---------------------------------------------------------------------------
# retrieval methods -> ranked candidate list
# ---------------------------------------------------------------------------

def m_lexical(query: str, **_) -> list[str]:
    return shortlist(query, k=150)


class BM25:
    def __init__(self, docs: list[set[str]]):
        self.docs = docs
        self.avg = sum(len(d) for d in docs) / len(docs)
        df = Counter(t for d in docs for t in d)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def score(self, q: set[str], i: int, k1=1.5, b=0.75) -> float:
        d = self.docs[i]
        norm = k1 * (1 - b + b * len(d) / self.avg)
        return sum(self.idf.get(t, 0) * (1 + k1) / (1 + norm) for t in q if t in d)


_bm25 = None

def m_bm25(query: str, **_) -> list[str]:
    global _bm25
    if _bm25 is None:
        _bm25 = BM25([_tokens(p) for p in CATEGORIES])
    q = _tokens(query)
    ranked = sorted(range(len(CATEGORIES)), key=lambda i: -_bm25.score(q, i))
    top = [CATEGORIES[i] for i in ranked[:150]]
    return top + [t for t in TOP_LEVEL if t not in top]


_embedder = None
_path_emb = None

def _get_embeddings():
    global _embedder, _path_emb
    if _path_emb is None:
        import numpy as np
        # optional: `uv add fastembed` to run the embedding methods; the
        # shipped pipeline stays lexical (see the benchmark table in README)
        from fastembed import TextEmbedding
        _embedder = TextEmbedding("BAAI/bge-small-en-v1.5")
        emb_file = CACHE / "path_embeddings.npy"
        if emb_file.exists():
            _path_emb = np.load(emb_file)
        else:
            _path_emb = np.array(list(_embedder.embed(CATEGORIES)))
            np.save(emb_file, _path_emb)
    return _embedder, _path_emb


def m_embed(query: str, name: str = "", hints: str = "", **_) -> list[str]:
    import numpy as np
    embedder, path_emb = _get_embeddings()
    q = np.array(list(embedder.embed([f"{hints} {name}"])))[0]
    sims = path_emb @ q / (np.linalg.norm(path_emb, axis=1) * np.linalg.norm(q) + 1e-9)
    ranked = np.argsort(-sims)[:150]
    top = [CATEGORIES[i] for i in ranked]
    return top + [t for t in TOP_LEVEL if t not in top]


def m_union(query: str, name: str = "", hints: str = "", **_) -> list[str]:
    lex = shortlist(query, k=100)
    emb = m_embed(query, name=name, hints=hints)[:50]
    seen, out = set(), []
    for p in lex + emb:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out + [t for t in TOP_LEVEL if t not in seen]


METHODS = {"lexical": m_lexical, "bm25": m_bm25, "embed": m_embed, "union": m_union}


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def recall_report(pages, hints, crumbs):
    print(f"{'page':18}" + "".join(f"{m:>9}" for m in METHODS))
    totals = {m: 0 for m in METHODS}
    ranks = {m: [] for m in METHODS}
    for stem, p in pages.items():
        row = f"{stem:18}"
        q = query_text(p, hints[stem], crumbs.get(stem, ""))
        for m, fn in METHODS.items():
            cands = fn(q, name=p["name"], hints=" ".join(hints[stem]))
            pos = next((i for i, c in enumerate(cands) if c in p["accepted"]), None)
            hit = pos is not None and pos < 150  # ignore the top-level safety net
            totals[m] += hit
            if hit:
                ranks[m].append(pos)
            row += f"{('@' + str(pos)) if hit else 'MISS':>9}"
        print(row)
    n = len(pages)
    print(f"{'recall':18}" + "".join(f"{totals[m]}/{n:<5}" for m in METHODS))
    print(f"{'median rank':18}" + "".join(
        f"{sorted(r)[len(r)//2] if r else '-':>9}" for r in ranks.values()))


async def pick_accuracy(pages, hints, crumbs, methods):
    results = {m: {} for m in methods}
    for stem, p in pages.items():
        q = query_text(p, hints[stem], crumbs.get(stem, ""))
        for m in methods:
            cands = METHODS[m](q, name=p["name"], hints=" ".join(hints[stem]))
            numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(cands))
            r = await ai.responses(PICK_MODEL, [
                {"role": "system", "content":
                 "You classify a product into Google's product taxonomy. Reply with the "
                 "index of the single best-fitting category. Prefer the deepest correct "
                 "path; top-levels are a last resort. A basketball shoe is Shoes, not "
                 "Basketball; an album is Music CDs, not a hobby category."},
                {"role": "user", "content":
                 f"Product: {p['name']}\nBrand: {p['brand']}\nHints: {hints[stem]}\n"
                 f"Breadcrumb: {crumbs.get(stem, '')}\nDescription: {p['description']}\n\n"
                 f"Categories:\n{numbered}"}], text_format=_Pick)
            picked = cands[r.index] if 0 <= r.index < len(cands) else None
            results[m][stem] = picked
    print(f"{'page':18}" + "".join(f"{m:>9}" for m in methods))
    for stem, p in pages.items():
        row = f"{stem:18}"
        for m in methods:
            ok = results[m][stem] in p["accepted"]
            row += f"{'ok' if ok else 'X':>9}"
        print(row)
    for m in methods:
        acc = sum(results[m][s] in pages[s]["accepted"] for s in pages)
        print(f"{m}: {acc}/{len(pages)}")
        for s in pages:
            if results[m][s] not in pages[s]["accepted"]:
                print(f"   X {s}: {results[m][s]}")


async def treewalk(pages, hints, crumbs):
    tree = {}
    for path in CATEGORIES:
        parts = [x.strip() for x in path.split(">")]
        for i in range(len(parts)):
            tree.setdefault(" > ".join(parts[:i]) or "", set()).add(parts[i])
    ok_count, calls = 0, 0
    for stem, p in pages.items():
        node, path = "", []
        for _level in range(7):
            children = sorted(tree.get(node, []))
            if not children:
                break
            opts = children + (["<stop here>"] if node in set(CATEGORIES) or path else [])
            numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(opts))
            r = await ai.responses(PICK_MODEL, [
                {"role": "system", "content":
                 "You are walking Google's product taxonomy one level at a time. Pick the "
                 "child that fits the product, or <stop here> if the current path is "
                 "already the best fit. Reply with the index."},
                {"role": "user", "content":
                 f"Product: {p['name']} | {p['brand']} | Hints: {hints[stem]}\n"
                 f"Current path: {node or '(root)'}\n\nChildren:\n{numbered}"}],
                text_format=_Pick)
            calls += 1
            if not (0 <= r.index < len(opts)) or opts[r.index] == "<stop here>":
                break
            path.append(opts[r.index])
            node = " > ".join(path)
        ok = node in p["accepted"]
        ok_count += ok
        if not ok:
            print(f"   X {stem}: {node}")
    print(f"treewalk: {ok_count}/{len(pages)} accurate, {calls} calls total "
          f"({calls/len(pages):.1f} per page)")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recall", action="store_true")
    ap.add_argument("--pick", nargs="*", default=None)
    ap.add_argument("--treewalk", action="store_true")
    args = ap.parse_args()

    pages = load_pages()
    hints = await gen_hints(pages)
    crumbs = breadcrumbs()

    if args.recall:
        recall_report(pages, hints, crumbs)
    if args.pick is not None:
        await pick_accuracy(pages, hints, crumbs, args.pick or list(METHODS))
    if args.treewalk:
        await treewalk(pages, hints, crumbs)


if __name__ == "__main__":
    asyncio.run(main())
