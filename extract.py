"""The extraction LLM call: PromptContext in, Draft out, indices resolved.

The system prompt is a numbered rule spec. Every rule is a site-agnostic
pattern of how e-commerce pages encode data; none references a specific
site, and no examples come from the assignment data.
"""

import logging
import os

import ai
from distill import media_by_index
from models import Draft, Product, PromptContext, Price, Variant

logger = logging.getLogger(__name__)

# Overridable so the eval harness can benchmark model configurations.
EXTRACT_MODEL = os.environ.get("EXTRACT_MODEL", "google/gemini-2.5-flash-lite")
ESCALATION_MODEL = os.environ.get("ESCALATION_MODEL", "google/gemini-3-flash-preview")

_SYSTEM = """You extract structured product data from the distilled contents of one e-commerce product detail page. The input has labeled sections: IDENTITY, JSON-LD, META, EMBEDDED JSON, SCRIPT TEXT, VISIBLE TEXT, MEDIA CANDIDATES. Fill the output schema by choosing values from that evidence.

## Ground rules

1. This page sells exactly one product, identified by IDENTITY and the canonical URL.
   Extract only that product. Ignore recommended, related, recently-viewed, and
   "complete the look" products wherever they appear.

2. Never invent values. Every price, sku, label, and feature must appear somewhere
   in the input. If a field is not on the page, leave it null or empty.
   Empty is a correct answer; a plausible guess is a wrong one.

## Price

3. `price` is what a buyer pays right now. `compare_at_price` is the crossed-out
   original, null when not on sale.
   - Structured data often carries several price fields under names like
     current/original, sale/list, or a price next to a promo-discount amount:
     the lower value -> `price`, the higher -> `compare_at_price`.
   - Never compute a price from a percentage-off callout.
   - Never swap the two, and never use a different sku's sale price.

4. Currency comes from an explicit code on the page: a structured-data field, or the
   locale in the canonical URL (a /gb/ path with £ symbols means GBP).
   - Never guess from a bare symbol alone.
   - Tax-inclusive prices stay as displayed; do not "correct" them.

## Text fields

5. Description source priority, first match wins:
   a. the product description in structured data, verbatim;
   b. the meta description, only if it is product-specific rather than generic SEO copy;
   c. descriptive prose near the title in VISIBLE TEXT.
   Never use reviews, model-fit captions, or copy about other products.
   Keep the page's own language; do not translate.

6. key_features are short factual bullets stated on the page: materials, specs,
   dimensions, care, construction. Not marketing slogans, not shipping or returns
   policies. If the description is itself a bullet list, copy every one of those
   bullets into key_features.

7. brand is the manufacturer or label as the page states it. The store name is only
   the brand when the store sells its own product.

## Media

8. Choose images from MEDIA CANDIDATES by index only. Candidates carry a
   provenance tag: [selected product] and [product data] entries are the
   product's own media; treat [related items rail] as another product's.
   - Select every distinct gallery photo of the product this page displays, not a
     representative few.
   - Scope: if color choices stay on this page (one product URL), each color's main
     photos belong to this product. If colorways live on separate product pages,
     include only the displayed colorway's photos.
   - Exclude logos, icons, payment badges, size charts, and other products' photos.

9. video_id is the integer N from a VID_N line in MEDIA CANDIDATES, or null.
   Never put any other identifier there; if there are no VID candidates, it is null.
   A product video on the store's media CDN belongs to this product unless it
   clearly shows a different product or colorway. If a VID candidate's URL contains
   the product's own name tokens, select it.

## Colors, options, variants

10. colors: every color name this page offers for this product, including colorways
    that link to sibling pages. Use the page's exact color names, never shortened.

11. options are the axes the page offers (Size, Color, Fit) with the values listed.
    A variant is one concrete purchasable configuration, and it must satisfy BOTH:
    a. it has at least one selection;
    b. the page ties that exact combination to a sku, price, or stock signal.
    Decision procedure for each choice the page offers:
    - Picking it stays on this product's URL (query param or in-page state)
      -> it is a variant axis.
    - Picking it navigates to a different product URL -> it is a sibling product:
      record its color in colors/options, never as a variant.
    Consequences:
    - Never combine axes into configurations the page does not state.
    - When colorways are separate pages, variants cover only the displayed colorway
      and selections carry only the axes selectable here (Size alone, no Color).
    - A page offering no selectable choice has variants: [] even if it names its
      single color or size.

12. available: only from an explicit stock signal (a stock count, "in stock" text,
    an InStock offer). Catalog flags like status ACTIVE/ENABLED/LISTED are not
    stock signals. No signal -> null, never true.

## Category hint

13. candidate_categories: 2-4 short phrases for what this product IS, written as
    alternatives: the page's own wording first, then common synonyms, then the
    US retail term when it differs (["jumper", "sweater"], ["cot", "crib", "baby bed"]).
    Prefer the page's breadcrumb or type wording for the first entry.
    Always write these phrases in English, even when the page is in another
    language (they feed an English-language category index). This is the one
    field where rule 5's keep-the-page-language rule does not apply.
"""


# the extraction call: distilled context in, structured draft out
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


# map the draft's media indices back to urls and assemble the final product
# e.g. image_ids=[0, 2] with IMG_0=a.jpg, IMG_2=b.jpg -> image_urls=[a.jpg, b.jpg]
def resolve_draft(draft: Draft, ctx: PromptContext, category) -> Product:
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
