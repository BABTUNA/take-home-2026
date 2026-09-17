"""The extraction LLM call: PromptContext in, Draft out, indices resolved.

The system prompt is a numbered rule spec. Every rule is a site-agnostic
pattern of how e-commerce pages encode data; none references a specific
site, and no examples come from the assignment data.
"""

import logging

import ai
from distill import media_by_index
from models import Draft, Product, PromptContext, Price, Variant

logger = logging.getLogger(__name__)

EXTRACT_MODEL = "google/gemini-2.5-flash-lite"
ESCALATION_MODEL = "google/gemini-3-flash-preview"

_SYSTEM = """You extract structured product data from the distilled contents of one e-commerce product detail page. The input has labeled sections (IDENTITY, JSON-LD, META, EMBEDDED JSON, SCRIPT TEXT, VISIBLE TEXT, MEDIA CANDIDATES). Fill the output schema. Rules:

1. Extract only the product the page is about (see IDENTITY). Ignore recommended, related, and recently-viewed products.
2. Never invent values. Every price, sku, label, and feature must appear somewhere in the input. If a field is not on the page, leave it null or empty. Empty is correct for a page that has no variants.
3. Price: `price` is what a buyer pays now, `compare_at_price` is the crossed-out original (null if not on sale). Structured data often has several price fields; pairs like currentPrice/initialPrice, price/priceBeforeDiscount, or a price plus an "instant savings" amount mean the lower value is `price` and the higher is `compare_at_price`. Never compute a price from a percentage. Never swap the two.
4. Currency: use an explicit currency code from the page (structured data field, or the locale in the canonical URL, e.g. /gb/ with £ symbols means GBP). Never guess from a bare symbol alone. Keep tax-inclusive prices as displayed.
5. Description: prefer, in order, the product description in structured data, the meta description if it is product-specific, then descriptive prose near the title in the visible text. Never use reviews, model-fit notes, or copy about other products. Keep the page's own language; do not translate.
6. key_features: short factual bullets stated on the page (materials, specs, dimensions, care). Not marketing slogans, not shipping/returns policies.
7. Images: choose from MEDIA CANDIDATES by index only. Pick the product's own gallery photos, typically several, not just the first. Exclude logos, icons, payment badges, size charts, and other products' photos. video_id likewise, only if the video is about this product.
8. brand: the manufacturer/label as stated on the page. The store name is only the brand when the store sells its own product.
9. colors: the color names this page offers for this product, including colorways that link to sibling pages.
10. options vs variants: `options` are the axes the page offers (Size, Color, Fit) with the values listed. A `variant` is one concrete purchasable configuration the page ties to a sku, price, or stock signal, and must have at least one selection. A product with no selectable axes has zero variants. Only emit variants the page actually asserts; never combine axes into configurations the page does not state. If choosing a value navigates to a different product URL, it is a sibling product: record it in colors/options, not as a variant.
11. candidate_category: a short phrase for what this product IS, plus common synonyms and the US retail term if it differs (e.g. "trousers pants", "floor lamp lighting"). Use the page's own breadcrumb or type wording if present.
12. availability: mark a variant available only on an explicit in-stock signal (stock count, "in stock" text, InStock offer). If the page shows no stock signal, `available` must be null, never true. A variant merely being listed does not mean it is in stock.
"""


async def extract_draft(ctx: PromptContext, model: str = EXTRACT_MODEL,
                        repair_error: str | None = None) -> Draft:
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": ctx.text},
    ]
    if repair_error:
        messages.append({"role": "user", "content":
                         f"Your previous answer failed validation:\n{repair_error}\n"
                         "Produce a corrected answer. Do not change fields that were valid."})
    return await ai.responses(model, messages, text_format=Draft)


def resolve_draft(draft: Draft, ctx: PromptContext, category) -> Product:
    """Map the draft's media indices back to URLs and assemble the Product."""
    image_urls, video_urls = media_by_index(ctx.media)

    def imgs(ids: list[int]) -> list[str]:
        return [image_urls[i] for i in ids if 0 <= i < len(image_urls)]

    video_url = None
    if draft.video_id is not None and 0 <= draft.video_id < len(video_urls):
        video_url = video_urls[draft.video_id]

    # Belt and braces on top of prompt rule 10: a variant without selections
    # is just the product itself restated.
    variants = [
        Variant(selections=v.selections, sku=v.sku, price=v.price,
                available=v.available, image_urls=imgs(v.image_ids))
        for v in draft.variants if v.selections
    ]

    return Product(
        name=draft.name,
        price=Price(price=draft.price, currency=draft.currency,
                    compare_at_price=draft.compare_at_price),
        description=draft.description,
        key_features=draft.key_features,
        image_urls=imgs(draft.image_ids),
        video_url=video_url,
        category=category,
        brand=draft.brand,
        colors=draft.colors,
        options=draft.options,
        variants=variants,
    )
