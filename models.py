from dataclasses import dataclass, field
from typing import Any
from pathlib import Path
from pydantic import BaseModel, field_validator

# Load categories once at module level
CATEGORIES_FILE = Path(__file__).parent / "categories.txt"
VALID_CATEGORIES = set()
if CATEGORIES_FILE.exists():
    with open(CATEGORIES_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                VALID_CATEGORIES.add(line)

class Category(BaseModel):
    # A category from Google's Product Taxonomy
    # https://www.google.com/basepages/producttype/taxonomy.en-US.txt
    name: str

    @field_validator("name")
    @classmethod
    def validate_name_exists(cls, v: str) -> str:
        if v not in VALID_CATEGORIES:
            raise ValueError(f"Category '{v}' is not a valid category in categories.txt")
        return v

class Price(BaseModel):
    price: float
    currency: str
    # If a product is on sale, this is the original price
    compare_at_price: float | None = None

class Selection(BaseModel):
    # One axis choice, e.g. name="Size", value="UK 7". Modeled as name/value
    # pairs rather than a dict because structured-output schemas can't express
    # open dict keys.
    name: str
    value: str

class Option(BaseModel):
    # An axis the page offers (Size, Color, Fit) and the values it lists.
    # Kept separate from Variant: a page can show axes without ever tying
    # them together into purchasable combinations.
    name: str
    values: list[str]

class Variant(BaseModel):
    # One purchasable configuration the page actually asserts. We never
    # cartesian-product the option axes; a variant exists only if the page
    # ties these selections to a sku/price/stock signal.
    selections: list[Selection]
    sku: str | None = None
    price: float | None = None
    available: bool | None = None
    image_urls: list[str] = []

# This is the final product schema that you need to output.
# You may add additional models as needed.
class Product(BaseModel):
    name: str
    price: Price
    description: str
    key_features: list[str]
    image_urls: list[str]
    video_url: str | None = None
    category: Category
    brand: str
    colors: list[str]
    # The axes offered on the page. Additive field (defaults to empty) so the
    # schema stays compatible with the original.
    options: list[Option] = []
    variants: list[Variant]


# ---------------------------------------------------------------------------
# LLM-facing draft schema. The model works with media indices (IMG_3 -> 3)
# instead of URLs, so a hallucinated URL is unrepresentable. Indices are
# resolved against the distilled media table after the call.
# ---------------------------------------------------------------------------

class DraftVariant(BaseModel):
    selections: list[Selection]
    sku: str | None = None
    price: float | None = None
    available: bool | None = None
    image_ids: list[int] = []

class Draft(BaseModel):
    name: str
    price: float
    currency: str
    compare_at_price: float | None = None
    description: str
    key_features: list[str]
    image_ids: list[int]
    video_id: int | None = None
    # Free-text guess ("cordless drills"); resolved to a real taxonomy path
    # in a separate step.
    candidate_category: str
    brand: str
    colors: list[str]
    options: list[Option] = []
    variants: list[DraftVariant] = []


# ---------------------------------------------------------------------------
# Internal pipeline shapes (not LLM-facing, so plain dataclasses).
# ---------------------------------------------------------------------------

@dataclass
class JsonBlob:
    # A parsed JSON object found in a script tag, with the commerce-keyword
    # score that got it kept.
    source: str
    score: float
    data: Any

@dataclass
class ScriptText:
    # Raw text of a script that would not parse as JSON but scored high on
    # product keywords (e.g. Next.js Flight payloads).
    score: float
    text: str

@dataclass
class MediaCandidate:
    url: str
    kind: str  # "image" | "video"
    origin: str  # channel that found it: "json_ld" | "meta" | "blob" | "dom"
    width: int | None = None
    # For blob-origin media: the JSON key path it was found under, e.g.
    # "selectedProduct.contentImages". Lets ranking demote related-product
    # subtrees without knowing any site's schema.
    path_hint: str = ""

@dataclass
class Evidence:
    json_ld: list[Any] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)
    title: str | None = None
    canonical_url: str | None = None
    h1: str | None = None
    json_blobs: list[JsonBlob] = field(default_factory=list)
    script_texts: list[ScriptText] = field(default_factory=list)
    visible_text: str = ""
    media: list[MediaCandidate] = field(default_factory=list)

@dataclass
class PromptContext:
    # What the extraction model actually sees, plus the media table needed to
    # resolve its index-based answers.
    text: str
    media: list[MediaCandidate]
    identity_tokens: set[str] = field(default_factory=set)