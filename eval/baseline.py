"""No-LLM baseline: JSON-LD + meta tags only, over the same harvest.

Exists purely as the measured floor for the scoreboard: the delta between
this and the full pipeline is what the model (and the blob/DOM channels)
actually buy. Category comes from the lexical shortlist's top hit, so this
whole file runs offline in milliseconds.

Usage:
    uv run python eval/baseline.py     # writes output_baseline/*.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from harvest import harvest  # noqa: E402
from taxonomy import shortlist  # noqa: E402
from models import VALID_CATEGORIES  # noqa: E402


def _ld_products(json_ld: list) -> list[dict]:
    out = []

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            if "@graph" in node:
                visit(node["@graph"])
            t = node.get("@type", "")
            types = t if isinstance(t, list) else [t]
            if any(x in ("Product", "ProductGroup") for x in types):
                out.append(node)

    visit(json_ld)
    return out


def baseline_extract(raw_html: str) -> dict:
    ev = harvest(raw_html)
    prods = _ld_products(ev.json_ld)
    ld = prods[0] if prods else {}
    meta = ev.meta

    offers = ld.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}

    name = ld.get("name") or meta.get("og:title") or ev.title or ""
    description = ld.get("description") or meta.get("og:description") or meta.get("description") or ""
    brand = ld.get("brand") or {}
    brand_name = brand.get("name") if isinstance(brand, dict) else str(brand or "")

    images = ld.get("images") or ld.get("image") or []
    if isinstance(images, str):
        images = [images]
    if not images and meta.get("og:image"):
        images = [meta["og:image"]]

    price = offers.get("price")
    try:
        price = float(price)
    except (TypeError, ValueError):
        price = 0.0

    hint = " ".join(filter(None, [name, brand_name, description[:200]]))
    candidates = shortlist(hint, k=5)
    category = next((c for c in candidates if c in VALID_CATEGORIES), "")

    features = ld.get("positiveNotes") or []
    if isinstance(features, dict):
        features = [str(v) for v in features.values()]

    return {
        "name": str(name),
        "price": {"price": price,
                  "currency": str(offers.get("priceCurrency") or ""),
                  "compare_at_price": None},
        "description": str(description),
        "key_features": [str(f) for f in features],
        "image_urls": [str(u) for u in images],
        "video_url": None,
        "category": {"name": category},
        "brand": str(brand_name or ""),
        "colors": [ld["color"]] if isinstance(ld.get("color"), str) else [],
        "options": [],
        "variants": [],
    }


def main() -> None:
    root = Path(__file__).parent.parent
    out_dir = root / "output_baseline"
    out_dir.mkdir(exist_ok=True)
    for f in sorted((root / "data").glob("*.html")):
        result = baseline_extract(f.read_text(encoding="utf-8", errors="ignore"))
        (out_dir / f"{f.stem}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"{f.stem}: name={bool(result['name'])} price={result['price']['price']} "
              f"imgs={len(result['image_urls'])} cat={result['category']['name'][:40]!r}")


if __name__ == "__main__":
    main()
