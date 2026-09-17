"""Resolve a free-text category guess to an exact Google taxonomy path.

The model can't reliably emit 1 of 5,596 exact strings, so: lexically
shortlist candidate paths, have the model pick one BY NUMBER from the list,
and validate the result exists. No silent fallback: a valid-but-wrong
category is worse than a loud failure.
"""

import logging
import re

from pydantic import BaseModel

import ai
from models import VALID_CATEGORIES, Category

logger = logging.getLogger(__name__)

CATEGORIES: list[str] = sorted(VALID_CATEGORIES)
TOP_LEVEL: list[str] = [c for c in CATEGORIES if ">" not in c]

_SHORTLIST_MODEL = "google/gemini-2.5-flash-lite"
_ESCALATION_MODEL = "google/gemini-3-flash-preview"


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


# shortlist then have the model pick one path by index, validated to exist
async def resolve(candidate: str, name: str, brand: str, description: str,
                  breadcrumb: str = "") -> Category:
    query = " ".join([candidate, candidate, name, brand, breadcrumb, description[:300]])

    last_err = None
    for attempt, (k, model) in enumerate([(150, _SHORTLIST_MODEL), (400, _ESCALATION_MODEL)]):
        options = shortlist(query, k=k)
        numbered = "\n".join(f"{i}. {p}" for i, p in enumerate(options))
        prompt = [
            {"role": "system", "content":
                "You classify a product into Google's product taxonomy. "
                "Reply with the index of the single best-fitting category from the list. "
                "Prefer the deepest path that is actually correct; top-level entries are a "
                "last resort. Judge by what the product IS, not who it's for or its brand. "
                "Never pick an accessory or part category for the product itself: a lamp "
                "is not a lamp shade, a camera is not a camera lens."},
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
