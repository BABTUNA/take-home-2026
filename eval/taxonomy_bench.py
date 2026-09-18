"""Grade the category already selected in each saved product output.

Each output's category.name must exactly match one accepted path in
eval/expected_categories.json. This reads saved files only: no retrieval,
embedding model, or LLM call runs during evaluation.

Usage:
    uv run python eval/taxonomy_bench.py
    uv run python eval/taxonomy_bench.py --json
    uv run python eval/taxonomy_bench.py --output-dir another_output --strict
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
EXPECTED = ROOT / "eval" / "expected_categories.json"
GROUND_TRUTH = ROOT / "eval" / "ground_truth"


# compare saved category paths with the accepted paths for all 50 pages
def score_categories(output_dir: Path) -> dict:
    expected = json.loads(EXPECTED.read_text())
    graded = {p.stem for p in GROUND_TRUTH.glob("*.json")}
    missing_ground_truth = graded - expected.keys()
    if missing_ground_truth:
        raise ValueError(f"graded pages missing accepted categories: {sorted(missing_ground_truth)}")

    pages = {}
    for stem, accepted in sorted(expected.items()):
        output_file = output_dir / f"{stem}.json"
        predicted = None
        if output_file.exists():
            product = json.loads(output_file.read_text())
            category = product.get("category")
            if isinstance(category, dict):
                predicted = category.get("name")

        pages[stem] = {
            "predicted": predicted,
            "accepted": accepted,
            "correct": predicted in accepted,
            "graded": stem in graded,
        }

    correct = sum(page["correct"] for page in pages.values())
    graded_correct = sum(page["correct"] for page in pages.values() if page["graded"])
    return {
        "correct": correct,
        "total": len(pages),
        "accuracy": correct / len(pages) if pages else 0.0,
        "graded_correct": graded_correct,
        "graded_total": len(graded),
        "graded_accuracy": graded_correct / len(graded) if graded else 0.0,
        "pages": pages,
    }


# show the exact picked category and accepted paths for every miss
def print_report(result: dict) -> None:
    print(f"CATEGORY ACCURACY {result['correct']}/{result['total']} "
          f"({result['accuracy']:.1%})")
    print(f"GRADED PAGES     {result['graded_correct']}/{result['graded_total']} "
          f"({result['graded_accuracy']:.1%})")
    misses = [(stem, page) for stem, page in result["pages"].items() if not page["correct"]]
    if misses:
        print("\nmisses:")
        for stem, page in misses:
            print(f"  {stem}: {page['predicted'] or '(missing category/output)'}")
            print(f"    accepted: {' | '.join(page['accepted'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    parser.add_argument("--strict", action="store_true", help="exit 1 if any category is wrong")
    args = parser.parse_args()

    result = score_categories(args.output_dir)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result)
    return int(args.strict and result["correct"] != result["total"])


if __name__ == "__main__":
    raise SystemExit(main())
