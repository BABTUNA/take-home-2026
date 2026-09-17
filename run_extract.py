"""Batch runner: extract every page in data/ (and optionally data_unseen/).

Usage:
    uv run python run_extract.py                # the 5 assignment pages
    uv run python run_extract.py --unseen      # also the unseen corpus
    uv run python run_extract.py data/nike.html  # specific files
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from pipeline import extract_product

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "output"))


async def run_one(path: Path) -> tuple[str, bool, float]:
    t0 = time.time()
    try:
        product = await extract_product(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception as e:  # noqa: BLE001 - a page failing must not kill the batch
        logging.error("%s FAILED: %s", path.stem, e)
        return path.stem, False, time.time() - t0
    out = OUTPUT_DIR / f"{path.stem}.json"
    out.write_text(product.model_dump_json(indent=2), encoding="utf-8")
    logging.info("%s -> %s (%.1fs)", path.stem, out, time.time() - t0)
    return path.stem, True, time.time() - t0


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        files = [Path(a) for a in args]
    else:
        files = sorted(Path("data").glob("*.html"))
        if "--unseen" in sys.argv:
            files += sorted(Path("data_unseen").glob("*.html"))

    OUTPUT_DIR.mkdir(exist_ok=True)
    results = await asyncio.gather(*(run_one(f) for f in files))

    ok = [name for name, success, _ in results if success]
    failed = [name for name, success, _ in results if not success]
    print(f"\n{len(ok)}/{len(results)} pages extracted -> {OUTPUT_DIR}/")
    if failed:
        print(f"failed: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
