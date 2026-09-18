"""HTTP surface on the extracted products. See SERVER_IMPLEMENTATION.md.

Run:
    uv run uvicorn server:app --reload
"""

import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from models import Price

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "output"


class ProductSummary(BaseModel):
    id: str
    name: str
    brand: str
    price: Price
    image_url: str | None
    hover_image_url: str | None
    category: str
    source: str  # "assignment" | "unseen"


# stem came from data/ -> it's one of the graded pages
def _source(stem: str) -> str:
    return "assignment" if (ROOT / "data" / f"{stem}.html").exists() else "unseen"


# load saved products into compact catalog cards and full records by id
def load_catalog() -> tuple[list[ProductSummary], dict[str, dict]]:
    summaries, details = [], {}
    for f in sorted(OUTPUT_DIR.glob("*.json")):
        # skip unreadable or invalid JSON without hiding the other products
        try:
            product = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("skipping %s: %s", f.name, e)
            continue
        stem = f.stem
        images = product.get("image_urls") or []
        # the grid needs only the first two images and a few product fields
        summaries.append(ProductSummary(
            id=stem,
            name=product["name"],
            brand=product["brand"],
            price=Price(**product["price"]),
            image_url=images[0] if images else None,
            hover_image_url=images[1] if len(images) > 1 else None,
            category=product["category"]["name"],
            source=_source(stem),
        ))
        # the detail page gets the whole product, keyed by its filename stem
        details[stem] = {**product, "id": stem, "source": _source(stem)}
    logger.info("loaded %d products", len(summaries))
    return summaries, details


SUMMARIES, DETAILS = load_catalog()

app = FastAPI(title="take-home catalog")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# send the compact cards used by the catalog grid
@app.get("/products")
def list_products() -> list[ProductSummary]:
    return SUMMARIES


# look up one full product by id; unknown ids return 404
@app.get("/products/{product_id}")
def get_product(product_id: str) -> dict:
    detail = DETAILS.get(product_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no product {product_id!r}")
    return detail
