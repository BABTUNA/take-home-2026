"""Resolve product hints to an exact Google taxonomy path.

Combine lexical and embedding shortlists, have the model pick by index,
then validate the path or fail.
"""

import logging
import os
import re
from pathlib import Path

from pydantic import BaseModel

import ai
from models import VALID_CATEGORIES, Category

logger = logging.getLogger(__name__)

CATEGORIES: list[str] = sorted(VALID_CATEGORIES)
TOP_LEVEL: list[str] = [c for c in CATEGORIES if ">" not in c]

# benchmark-winning defaults, overridable for cost/accuracy experiments:
#   PICK_MODEL=google/gemini-2.5-flash-lite  TAXONOMY_RETRIEVAL=lexical
_PICK_MODEL = os.environ.get("PICK_MODEL", "google/gemini-3-flash-preview")
_RETRIEVAL = os.environ.get("TAXONOMY_RETRIEVAL", "union")  # union | lexical
_EMB_CACHE = Path(__file__).parent / ".cache" / "taxonomy_embeddings.npy"


class _Pick(BaseModel):
    # 0-based index into the numbered list shown to the model.
    index: int


# tokenize with naive plural stemming so "shirt" matches "Shirts & Tops"
# real synonyms (trousers/pants) come from the extractor's category hint
def _tokens(text: str) -> set[str]:
    out = set()
    for t in re.findall(r"[a-z0-9]+", text.lower()):
        if len(t) > 2:
            out.add(t[:-1] if len(t) > 3 and t.endswith("s") else t)
    return out


# Precompute per-path token sets, with leaf tokens kept separately: a match on
# the leaf ("drills") says more than a match higher up ("hardware").
_PATH_TOKENS: list[tuple[str, set[str], set[str]]] = [
    (path, _tokens(path), _tokens(path.rsplit(">", 1)[-1])) for path in CATEGORIES
]


_embedder = None
_path_emb = None


# embed all 5596 paths once (cached to disk), embed the query, cosine top-k
# returns [] when fastembed isn't installed so the union degrades to lexical
def _embed_shortlist(query: str, k: int = 50) -> list[str]:
    global _embedder, _path_emb
    try:
        import numpy as np
        from fastembed import TextEmbedding
    except ImportError:
        return []
    if _path_emb is None:
        _embedder = TextEmbedding("BAAI/bge-small-en-v1.5")
        if _EMB_CACHE.exists():
            _path_emb = np.load(_EMB_CACHE)
        else:
            _path_emb = np.array(list(_embedder.embed(CATEGORIES)))
            _EMB_CACHE.parent.mkdir(exist_ok=True)
            np.save(_EMB_CACHE, _path_emb)
    import numpy as np
    q = np.array(list(_embedder.embed([query])))[0]
    sims = _path_emb @ q / (np.linalg.norm(_path_emb, axis=1) * np.linalg.norm(q) + 1e-9)
    return [CATEGORIES[i] for i in np.argsort(-sims)[:k]]


# narrow 5596 paths to the k closest by token overlap, leaf matches weighted
def shortlist(query: str, k: int = 150) -> list[str]:
    q = _tokens(query)
    scored = []
    for path, all_toks, leaf_toks in _PATH_TOKENS:
        overlap = len(q & all_toks)
        if not overlap:
            continue
        score = overlap + 1.5 * len(q & leaf_toks) + 0.05 * path.count(">")
        scored.append((score, path))
    scored.sort(reverse=True)
    top = [p for _, p in scored[:k]]
    # Top-level entries as a safety net, so a bad shortlist can still resolve
    # to something honest rather than a wrong deep leaf.
    return top + [t for t in TOP_LEVEL if t not in top]


# return lexical matches, optional embedding matches, then top-level paths
def _union_shortlist(query: str, embed_query: str, k_lex: int, k_emb: int = 50) -> list[str]:
    top_set = set(TOP_LEVEL)
    lex = [p for p in shortlist(query, k=k_lex) if p not in top_set][:k_lex]
    seen = set(lex)
    extra = []
    # lexical mode skips embeddings; union mode adds semantic matches
    if _RETRIEVAL == "union":
        extra = [p for p in _embed_shortlist(embed_query, k=k_emb) if p not in seen]
        seen.update(extra)
    return lex + extra + [t for t in TOP_LEVEL if t not in seen]


# shortlist then have the model pick one path by index, validated to exist
async def resolve(candidate: str, name: str, brand: str, description: str,
                  breadcrumb: str = "") -> Category:
    query = " ".join([candidate, candidate, name, brand, breadcrumb, description[:300]])
    embed_query = f"{candidate} {name}"

    last_err = None
    for attempt, (k, model) in enumerate([(100, _PICK_MODEL), (300, _PICK_MODEL)]):
        options = _union_shortlist(query, embed_query, k_lex=k)
        numbered = "\n".join(f"{i}. {p}" for i, p in enumerate(options))
        prompt = [
            {"role": "system", "content":
                "You classify a product into Google's product taxonomy. "
                "Reply with the index of the single best-fitting category from the list. "
                "Prefer the deepest path that is actually correct; top-level entries are a "
                "last resort. Judge by what the product IS, not who it's for or its brand. "
                "Never pick an accessory or part category for the product itself: a lamp "
                "is not a lamp shade, a camera is not a camera lens. Prefer the ordinary "
                "retail category over specialty ones (traditional, ceremonial, costume, "
                "medical) unless the page explicitly says the product is that specialty kind. "
                "Sport-branded apparel and footwear belong under Apparel & Accessories, not "
                "under the sport's equipment category: a basketball shoe is Shoes, not "
                "Basketball. Media products go under their format: an album is Music CDs "
                "or Digital Music Downloads, not a hobby category."},
            {"role": "user", "content":
                f"Product: {name}\nBrand: {brand}\nPage hint: {candidate}\n"
                f"Breadcrumb: {breadcrumb}\nDescription: {description[:400]}\n\n"
                f"Categories:\n{numbered}"},
        ]
        try:
            pick = await ai.responses(model, prompt, text_format=_Pick)
            if 0 <= pick.index < len(options):
                return Category(name=options[pick.index])
            last_err = ValueError(f"index {pick.index} out of range ({len(options)} options)")
        except Exception as e:  # noqa: BLE001 - retry once, then re-raise below
            last_err = e
        logger.warning("category resolve attempt %d failed: %s", attempt, last_err)

    raise RuntimeError(f"could not resolve category for {name!r}: {last_err}")
