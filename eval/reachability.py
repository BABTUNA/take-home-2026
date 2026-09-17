"""Reachability: does every ground-truth value survive distillation?

Re-runs harvest + distill with the section budgets raised and asserts each
ground-truth value is findable in the prompt context. A miss here is a
distiller bug (the model never had a chance); a miss only in score.py is a
model bug. Keeping those separable is the point of this file.

Usage:
    uv run python eval/reachability.py
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import distill  # noqa: E402
from distill import _asset_key  # noqa: E402
from harvest import harvest  # noqa: E402

RAISED = {"json_ld": 200_000, "meta": 10_000, "blobs": 400_000,
          "scripts": 50_000, "text": 60_000}


def _price_forms(value: float) -> set[str]:
    forms = {f"{value:g}", f"{value:.2f}", f"{value:.2f}".replace(".", ","),
             str(int(round(value * 100)))}
    if value == int(value):
        forms.add(str(int(value)))
    return forms


def check_page(gt: dict, ctx_text: str, media_keys: set[str]) -> list[str]:
    text = ctx_text.lower()
    missing = []

    def need(label: str, ok: bool):
        if not ok:
            missing.append(label)

    name_toks = [t for t in re.findall(r"[a-z0-9]+", gt["name"].lower()) if len(t) > 2]
    need("name", sum(t in text for t in name_toks) >= len(name_toks) * 0.8)
    need("brand", gt["brand"].lower() in text)

    p = gt["price"]
    need(f"price {p['price']}", any(f in ctx_text for f in _price_forms(p["price"])))
    if p.get("compare_at_price"):
        need(f"compare_at {p['compare_at_price']}",
             any(f in ctx_text for f in _price_forms(p["compare_at_price"])))

    for feat in gt["key_features"]:
        words = sorted(re.findall(r"[a-z]{4,}", feat.lower()), key=len, reverse=True)
        if words:
            need(f"feature word {words[0]!r}", words[0] in text)

    for color in gt["colors"]:
        need(f"color {color!r}", color.lower() in text)

    variants = gt["variants"]
    if isinstance(variants, list):
        for v in variants:
            for sel in v["selections"]:
                need(f"variant value {sel['value']!r}", sel["value"].lower() in text)

    for url in gt["image_urls"]:
        key = _asset_key(url)
        need(f"image {key}", key in media_keys)
    if gt.get("video_url"):
        need("video", _asset_key(gt["video_url"]) in media_keys)

    return missing


def main() -> None:
    root = Path(__file__).parent.parent
    original = dict(distill._BUDGETS)
    distill._BUDGETS.update(RAISED)
    distill._MAX_IMAGES, original_max = 400, distill._MAX_IMAGES
    try:
        failed = False
        for gt_file in sorted((root / "eval" / "ground_truth").glob("*.json")):
            gt = json.loads(gt_file.read_text())
            html = (root / gt["_source_file"]).read_text(encoding="utf-8", errors="ignore")
            ctx = distill.distill(harvest(html))
            media_keys = {_asset_key(m.url) for m in ctx.media}
            missing = check_page(gt, ctx.text, media_keys)
            status = "OK" if not missing else f"MISSING {len(missing)}"
            print(f"{gt_file.stem:12} {status}")
            for m in missing:
                print(f"    {m}")
            failed = failed or bool(missing)
        sys.exit(1 if failed else 0)
    finally:
        distill._BUDGETS.update(original)
        distill._MAX_IMAGES = original_max


if __name__ == "__main__":
    main()
