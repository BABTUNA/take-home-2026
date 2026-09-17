"""Scoreboard: extracted output vs hand-written ground truth, per field.

Usage:
    uv run python eval/score.py               # scores output/
    uv run python eval/score.py --baseline    # scores output_baseline/
    uv run python eval/score.py --json        # machine-readable, for CI

Matchers are tolerant where exactness is meaningless (image renditions, text
phrasing) and strict where it isn't (prices, category). Every matcher returns
a score in [0, 1] plus human-readable misses. No model calls here, ever.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from distill import _asset_key  # noqa: E402 - same identity the pipeline uses

FIELDS = ["name", "price", "desc", "feat", "imgs", "video", "cat", "colors", "variants"]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def _tokens(s: str) -> set[str]:
    return set(_norm(s).split())


def match_name(gt: str, out: str) -> tuple[float, list[str]]:
    a, b = _norm(gt), _norm(out)
    if a == b or b.startswith(a) or a.startswith(b):
        return 1.0, []
    return 0.0, [f"name {out!r} != {gt!r}"]


def match_price(gt: dict, out: dict) -> tuple[float, list[str]]:
    misses = []
    score = 0.0
    if abs(gt["price"] - out["price"]) < 0.01:
        score += 0.5
    else:
        misses.append(f"price {out['price']} != {gt['price']}")
    if gt["currency"].upper() == out["currency"].upper():
        score += 0.25
    else:
        misses.append(f"currency {out['currency']} != {gt['currency']}")
    g, o = gt.get("compare_at_price"), out.get("compare_at_price")
    if (g is None and o is None) or (g is not None and o is not None and abs(g - o) < 0.01):
        score += 0.25
    else:
        misses.append(f"compare_at {o} != {g}")
    return score, misses


def match_text(gt: str, out: str) -> tuple[float, list[str]]:
    """Containment of the shorter text's tokens in the longer one."""
    a, b = _tokens(gt), _tokens(out)
    if not a and not b:
        return 1.0, []
    if not a or not b:
        return 0.0, ["one side empty"]
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    ratio = len(shorter & longer) / len(shorter)
    return (1.0, []) if ratio >= 0.7 else (round(ratio, 2), [f"text overlap only {ratio:.2f}"])


def match_features(gt: list[str], out: list[str]) -> tuple[float, list[str]]:
    if not gt:
        return (1.0, []) if not out else (1.0, [])  # nothing asserted
    hits, misses = 0, []
    for g in gt:
        if any(match_text(g, o)[0] == 1.0 for o in out):
            hits += 1
        else:
            misses.append(f"missing feature {g[:60]!r}")
    return hits / len(gt), misses


def _asset_set(urls: list[str]) -> set[str]:
    return {_asset_key(u) for u in urls}


def match_images(gt: list[str], out: list[str]) -> tuple[float, list[str]]:
    g, o = _asset_set(gt), _asset_set(out)
    if not g:
        return (1.0, []) if not o else (0.5, [f"{len(o)} images where none expected"])
    tp = len(g & o)
    prec = tp / len(o) if o else 0.0
    rec = tp / len(g)
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    misses = []
    if rec < 1:
        misses.append(f"missing {len(g) - tp} of {len(g)} expected images")
    if prec < 1:
        misses.append(f"{len(o) - tp} unexpected images")
    return round(f1, 2), misses


def match_video(gt: str | None, out: str | None) -> tuple[float, list[str]]:
    a = (gt or "").split("?")[0]
    b = (out or "").split("?")[0]
    return (1.0, []) if a == b else (0.0, [f"video {b or None} != {a or None}"])


def match_category(gt: dict, out: dict) -> tuple[float, list[str]]:
    if gt["name"] == out["name"]:
        return 1.0, []
    # Partial credit for a correct ancestor: right area, not deep enough.
    if gt["name"].startswith(out["name"]):
        return 0.5, [f"category too shallow: {out['name']!r}"]
    return 0.0, [f"category {out['name']!r} != {gt['name']!r}"]


def match_colors(gt: list[str], out: list[str]) -> tuple[float, list[str]]:
    g = {_norm(c) for c in gt}
    o = {_norm(c) for c in out}
    if not g:
        return (1.0, []) if not o else (0.5, [f"colors {sorted(o)} where none expected"])
    tp = len(g & o)
    prec = tp / len(o) if o else 0.0
    rec = tp / len(g)
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    misses = [] if f1 == 1 else [f"colors got {sorted(o)}, expected {sorted(g)}"]
    return round(f1, 2), misses


def _sel_key(variant: dict) -> frozenset:
    return frozenset((_norm(s["name"]), _norm(s["value"])) for s in variant.get("selections", []))


def match_variants(gt, out: list[dict]) -> tuple[float, list[str]]:
    # Shape-asserted ground truth ({"count": N, "axes": [...]}) for pages
    # whose variant labels can't be joined statically (see llbean notes).
    if isinstance(gt, dict):
        count_score = min(len(out), gt["count"]) / max(len(out), gt["count"]) if out else 0.0
        axes_present = {_norm(s["name"]) for v in out for s in v.get("selections", [])}
        axes_expected = {_norm(a) for a in gt["axes"]}
        axes_score = len(axes_present & axes_expected) / len(axes_expected)
        score = round(0.5 * count_score + 0.5 * axes_score, 2)
        misses = [] if score == 1 else [
            f"variant count {len(out)} vs {gt['count']}, axes {sorted(axes_present)} vs {sorted(axes_expected)}"]
        return score, misses

    if not gt:
        return (1.0, []) if not out else (0.0, [f"{len(out)} variants where none expected"])
    out_by_sel = {_sel_key(v): v for v in out}
    hits, sub_scores, misses = 0, [], []
    for g in gt:
        o = out_by_sel.get(_sel_key(g))
        if o is None:
            misses.append(f"missing variant {dict(_sel_key(g))}")
            continue
        hits += 1
        sub = 1.0
        if g.get("price") is not None and (o.get("price") is None or abs(g["price"] - o["price"]) > 0.01):
            sub -= 0.25
            misses.append(f"variant {dict(_sel_key(g))} price {o.get('price')} != {g['price']}")
        if g.get("available") != o.get("available"):
            sub -= 0.25
            misses.append(f"variant {dict(_sel_key(g))} available {o.get('available')} != {g.get('available')}")
        sub_scores.append(sub)
    recall = hits / len(gt)
    precision = hits / len(out) if out else 0.0
    quality = sum(sub_scores) / len(sub_scores) if sub_scores else 0.0
    if precision < 1:
        misses.append(f"{len(out) - hits} extra variants not in ground truth")
    return round(recall * 0.5 + precision * 0.25 + quality * 0.25, 2), misses


def score_product(gt: dict, out: dict) -> tuple[dict[str, float], list[str]]:
    scores, details = {}, []
    checks = [
        ("name", match_name, gt["name"], out["name"]),
        ("price", match_price, gt["price"], out["price"]),
        ("desc", match_text, gt["description"], out["description"]),
        ("feat", match_features, gt["key_features"], out["key_features"]),
        ("imgs", match_images, gt["image_urls"], out["image_urls"]),
        ("video", match_video, gt.get("video_url"), out.get("video_url")),
        ("cat", match_category, gt["category"], out["category"]),
        ("colors", match_colors, gt["colors"], out["colors"]),
        ("variants", match_variants, gt["variants"], out["variants"]),
    ]
    for field, fn, g, o in checks:
        s, misses = fn(g, o)
        scores[field] = s
        details.extend(f"{field}: {m}" for m in misses)
    return scores, details


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).parent.parent
    out_dir = root / ("output_baseline" if args.baseline else "output")
    gt_dir = root / "eval" / "ground_truth"

    rows, all_details = {}, {}
    for gt_file in sorted(gt_dir.glob("*.json")):
        out_file = out_dir / gt_file.name
        gt = json.loads(gt_file.read_text())
        if not out_file.exists():
            rows[gt_file.stem] = {f: 0.0 for f in FIELDS}
            all_details[gt_file.stem] = ["no output file"]
            continue
        scores, details = score_product(gt, json.loads(out_file.read_text()))
        rows[gt_file.stem] = scores
        all_details[gt_file.stem] = details

    field_avg = {f: sum(r[f] for r in rows.values()) / len(rows) for f in FIELDS}
    overall = sum(field_avg.values()) / len(FIELDS)

    if args.json:
        print(json.dumps({"pages": rows, "field_avg": field_avg, "overall": overall}, indent=2))
    else:
        header = f"{'':14}" + "".join(f"{f:>9}" for f in FIELDS) + f"{'page':>9}"
        print(header)
        for page, r in rows.items():
            avg = sum(r.values()) / len(r)
            print(f"{page:14}" + "".join(f"{r[f]:>9.2f}" for f in FIELDS) + f"{avg:>9.2f}")
        print(f"{'field avg':14}" + "".join(f"{field_avg[f]:>9.2f}" for f in FIELDS))
        print(f"\nOVERALL {overall:.3f}")
        imperfect = {p: d for p, d in all_details.items() if d}
        if imperfect:
            print("\ndetails below 100%:")
            for page, details in imperfect.items():
                for d in details:
                    print(f"  {page}/{d}")

    sys.exit(0 if overall >= 0.999 else 1)


if __name__ == "__main__":
    main()
