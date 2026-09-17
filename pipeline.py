"""Orchestration: html -> Product, with retries, escalation, and provenance checks.

Failure path: cheap model -> one repair retry with the validation error ->
stronger model -> raise. No silent partial success.
"""

import logging
import re

from pydantic import ValidationError

import taxonomy
from distill import distill
from extract import ESCALATION_MODEL, EXTRACT_MODEL, extract_draft, resolve_draft
from harvest import harvest
from models import Draft, Product, PromptContext

logger = logging.getLogger(__name__)


# the whole pipeline for one page: harvest, distill, draft, categorize, assemble
async def extract_product(raw_html: str) -> Product:
    ctx = distill(harvest(raw_html))

    draft = await _draft_with_retries(ctx)

    breadcrumb = _breadcrumb_hint(ctx)
    category = await taxonomy.resolve(
        " ".join(draft.candidate_categories), draft.name, draft.brand, draft.description,
        breadcrumb=breadcrumb)

    return resolve_draft(draft, ctx, category)


# cheap model first, repair retry with the error, escalate, then raise
async def _draft_with_retries(ctx: PromptContext) -> Draft:
    attempts = [
        (EXTRACT_MODEL, None),
        (EXTRACT_MODEL, "repair"),
        (ESCALATION_MODEL, None),
    ]
    last_error: str | None = None
    for model, mode in attempts:
        try:
            draft = await extract_draft(
                ctx, model=model, repair_error=last_error if mode == "repair" else None)
            problems = _provenance_problems(draft, ctx)
            if problems:
                raise ValueError("; ".join(problems))
            return draft
        except (ValidationError, ValueError) as e:
            last_error = str(e)[:2000]
            logger.warning("draft attempt failed on %s: %s", model, last_error[:200])
    raise RuntimeError(f"extraction failed after escalation: {last_error}")


# facts the model asserted must literally appear in the evidence
def _provenance_problems(draft: Draft, ctx: PromptContext) -> list[str]:
    problems = []
    for label, value in [("price", draft.price), ("compare_at_price", draft.compare_at_price)]:
        if value is not None and not _number_on_page(value, ctx.text):
            problems.append(f"{label} {value} does not appear anywhere in the page evidence")
    n_images = sum(1 for m in ctx.media if m.kind == "image")
    bad = [i for i in draft.image_ids if not 0 <= i < n_images]
    if bad:
        problems.append(f"image_ids {bad} out of range (0..{n_images - 1})")
    if not draft.image_ids and n_images:
        problems.append("no images selected although image candidates exist")
    return problems


# is this number anywhere on the page: 129 129.0 129.00 129,00 or 12900
# (eu decimal commas and minor-unit prices in state blobs)
def _number_on_page(value: float, text: str) -> bool:
    forms = {f"{value:g}", f"{value:.2f}", f"{value:.2f}".replace(".", ","),
             f"{int(round(value * 100))}"}
    if value == int(value):
        forms.add(str(int(value)))
    return any(f in text for f in forms)


# pull breadcrumb names out of the context to seed the category query
def _breadcrumb_hint(ctx: PromptContext) -> str:
    m = re.search(r'"BreadcrumbList".{0,2000}', ctx.text, re.S)
    if not m:
        return ""
    names = re.findall(r'"name"\s*:\s*"([^"]{2,60})"', m.group(0))
    return " > ".join(names[:8])
