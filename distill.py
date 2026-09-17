"""Distill: Evidence in, PromptContext out.

Three jobs: anchor everything to the page's own identity so other products
(recommendation rails, related items in JSON-LD) drop out, prune blobs down
to their commerce subtrees, and dedupe media into a small numbered candidate
table. Per-section character budgets keep any one channel from crowding out
the others.
"""

import json
import re

from harvest import _COMMERCE_KEYS
from models import Evidence, MediaCandidate, PromptContext

# Character budgets per prompt section. JSON gets the most: it is the densest
# signal per token. Tuned against eval/reachability.py, which fails loudly if
# a budget cut drops a ground-truth value.
_BUDGETS = {
    "json_ld": 20_000,
    "meta": 2_000,
    "blobs": 58_000,
    "scripts": 8_000,
    "text": 10_000,
}
_MAX_IMAGES = 40
_MAX_VIDEOS = 5

_STOPWORDS = {"the", "and", "for", "with", "men", "mens", "women", "womens", "s"}


# turn raw evidence into the budgeted prompt context the model will see
def distill(ev: Evidence) -> PromptContext:
    identity = _identity_tokens(ev)

    json_ld = _filter_json_ld(ev, identity)
    blobs = [_prune(b.data, identity) for b in ev.json_blobs[:3]]
    media = _resolve_media(ev)

    sections = [
        ("IDENTITY", _render_identity(ev)),
        ("JSON-LD", _fit(json.dumps(json_ld, ensure_ascii=False, default=str), _BUDGETS["json_ld"])),
        ("META", _fit("\n".join(f"{k}: {v}" for k, v in ev.meta.items()), _BUDGETS["meta"])),
        ("EMBEDDED JSON", _fit_blobs(blobs, _BUDGETS["blobs"])),
        ("SCRIPT TEXT", _fit("\n".join(s.text for s in ev.script_texts[:2]), _BUDGETS["scripts"])),
        ("VISIBLE TEXT", _fit(ev.visible_text, _BUDGETS["text"])),
        ("MEDIA CANDIDATES", _render_media(media)),
    ]
    text = "\n\n".join(f"== {name} ==\n{body}" for name, body in sections if body)
    return PromptContext(text=text, media=media, identity_tokens=identity)


# fingerprint of what this page sells from h1 og:title and title
def _identity_tokens(ev: Evidence) -> set[str]:
    parts = [ev.h1 or "", ev.meta.get("og:title", ""), ev.title or ""]
    tokens = set()
    for p in parts:
        for tok in re.findall(r"[a-z0-9]+", p.lower()):
            if len(tok) > 2 and tok not in _STOPWORDS:
                tokens.add(tok)
    return tokens


# does a product name share enough tokens with the page fingerprint
def _name_matches_identity(name: str, identity: set[str]) -> bool:
    toks = {t for t in re.findall(r"[a-z0-9]+", name.lower()) if len(t) > 2}
    if not toks or not identity:
        return True  # can't judge, keep
    return len(toks & identity) / len(toks) >= 0.3


# keep product blocks about this page plus breadcrumbs, drop related items
# (pages ship json-ld for recommendations and bundle components too)
def _filter_json_ld(ev: Evidence, identity: set[str]) -> list:
    kept = []
    for block in ev.json_ld:
        for node in _ld_nodes(block):
            t = node.get("@type", "")
            types = t if isinstance(t, list) else [t]
            if any(x in ("Product", "ProductGroup") for x in types):
                if _name_matches_identity(str(node.get("name", "")), identity):
                    kept.append(node)
            elif "BreadcrumbList" in types:
                kept.append(node)
    return kept


# flatten json-ld into individual nodes, unwrapping lists and @graph
def _ld_nodes(block) -> list[dict]:
    if isinstance(block, list):
        return [n for item in block for n in _ld_nodes(item)]
    if isinstance(block, dict):
        if "@graph" in block:
            return [n for item in block["@graph"] for n in _ld_nodes(item)]
        return [block]
    return []


# Framework-generic noise keys: navigation trees, translation bundles,
# analytics configs. These are patterns of web frameworks, not of any site.
_NOISE_KEY = re.compile(
    r"(?:^|_)(?:nav|menu|footer|header|i18n|translation|messages|locale|dictionary"
    r"|analytics|tracking|gtm|experiment|featureflag|abtest|consent|cookies?"
    r"|router|routes|routing|webpack|chunks|assets|styles|registry|warehouses?)(?:$|_)", re.I)


# cut noise keys, shrink urls, and summarize related-product subtrees
# summary mode keeps sibling products to shallow scalars (name color price
# url) so one rail can't eat the whole blob budget
# camelCase keys must be split before noise matching or consentPolicy,
# cookiesManager etc. slip past the word-boundary regex
def _key_norm(k: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(k)).lower().replace("-", "_")


# js or html stored as a json string value is never product data
_CODE_STRING = re.compile(r"<script|<iframe|\bfunction\s*\(|window\.|gtag\(|=>|</\w+>")


def _prune(node, identity: set[str], depth: int = 0, summary: bool = False):
    if depth > 25:
        return None
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            kl = _key_norm(k)
            if _NOISE_KEY.search(kl):
                continue
            child_summary = summary or bool(_OTHER_PRODUCT_PATH.search(kl))
            if summary and isinstance(v, (dict, list)) and depth > 0:
                # In summary mode keep scalars only, one level of nesting.
                if not (isinstance(v, list) and all(not isinstance(i, (dict, list)) for i in v)):
                    continue
            pruned = _prune(v, identity, depth + 1, child_summary)
            # Empty strings and empty containers carry no evidence; False and
            # 0 do (available: false, stock: 0), so those stay.
            if pruned is not None and pruned != {} and pruned != [] and pruned != "":
                out[k] = pruned
        return out or None
    if isinstance(node, list):
        limit = 30 if summary else 100
        out = [p for item in node[:limit]
               if (p := _prune(item, identity, depth + 1, summary)) is not None]
        return out or None
    if isinstance(node, str):
        # URLs inside blobs are the biggest token sink, and the media table
        # already carries them in full. Keep just the tail as an identifier
        # so labels stay joinable to media entries.
        if node.startswith(("http://", "https://", "//")) and len(node) > 60:
            return ".../" + node.split("?")[0].rstrip("/").rsplit("/", 1)[-1][-48:]
        # Embedded code (tracking snippets, widget html) is never product data.
        if len(node) > 80 and _CODE_STRING.search(node):
            return "[code]"
        # Long opaque strings (base64, inlined CSS/JS) are token sinks.
        if len(node) > 500:
            return node[:500] + "…"
        return node
    return node


# ---------------------------------------------------------------------------
# Media resolution
# ---------------------------------------------------------------------------

# Path segments that encode a rendition size, e.g. /2890x1500/ or _400x.jpg
_SIZE_SEGMENT = re.compile(r"/\d{2,4}x\d{0,4}/|_\d{2,4}x\d{0,4}(?=\.)|w_\d+|h_\d+")


_RENDITION_WORDS = {"mini", "thumb", "thumbnail", "standard", "full", "max", "square",
                    "small", "large", "medium", "micro", "zoom", "default", "original"}

# framework-generic key-path vocabulary for media ranking
_OTHER_PRODUCT_PATH = re.compile(
    r"related|recommend|upsell|cross|similar|recently|alsolike|youmay", re.I)
_SELECTED_PATH = re.compile(r"selected|current|active", re.I)


# identity of the underlying asset ignoring rendition and size differences
# uses the full path because some cdns (puma-style) keep a constant filename
# and encode the view code several directories up; per segment it drops
# transform dsl (segments with , : =), WxH sizes, rendition words, and
# hash-looking tokens (hex with letters), keeping everything else
def _asset_key(url: str) -> str:
    base = url.split("?")[0].split("#")[0]
    base = _SIZE_SEGMENT.sub("/", base)
    path = base.split("//", 1)[-1].split("/")[1:]  # drop scheme and host
    kept_segments = []
    for seg in path:
        if not seg or "," in seg or ":" in seg or "=" in seg:
            continue  # cloudinary/akamai transform segments, not identity
        toks = []
        for tok in re.split(r"[^a-z0-9]+", seg.lower()):
            if not tok or tok in _RENDITION_WORDS:
                continue
            if len(tok) >= 6 and re.fullmatch(r"[0-9a-f]+", tok) and re.search(r"[a-f]", tok):
                continue  # content hash, not identity
            if re.fullmatch(r"\d{2,4}x\d{0,4}", tok):
                continue
            toks.append(tok)
        if toks:
            kept_segments.append("-".join(toks))
    return "/".join(kept_segments) if kept_segments else base.lower()


# best url within an asset group: blob and json-ld origins first (usually
# the full-res set), then clean urls over sized renditions, then srcset width
def _quality(m: MediaCandidate) -> tuple:
    origin_rank = {"blob": 3, "json_ld": 3, "meta": 1, "dom": 2}.get(m.origin, 0)
    has_size_params = 1 if re.search(r"[?&](?:w|wid|width|h|hei|sw|size)=\d", m.url) else 0
    return (origin_rank, -has_size_params, m.width or 0, len(m.url.split("?")[0]))


_GENERIC_PATH_WORDS = {"image", "images", "product", "products", "media", "photo",
                       "photos", "files", "default", "thumb", "large", "small", "assets"}


# identifier tokens from the page's own hero image url (og and twitter image)
# product galleries share an asset id or sku token with the hero, either in
# the filename (224626_0_44 -> 224626) or a path segment (/SKU25289/), which
# separates them from same-cdn content like size guides and cross-sells
def _hero_stems(ev: Evidence) -> set[str]:
    stems = set()
    for key in ("og:image", "og:image:secure_url", "twitter:image"):
        url = ev.meta.get(key)
        if not url:
            continue
        segments = url.split("?")[0].rstrip("/").split("/")[-3:]
        for seg in segments:
            for tok in re.findall(r"[a-z0-9]{4,}", seg.lower()):
                if tok not in _GENERIC_PATH_WORDS and not re.fullmatch(r"\d{2,4}x\d{2,4}", tok):
                    stems.add(tok)
    return stems


# dedupe media by asset, rank by relevance to this product, number survivors
def _resolve_media(ev: Evidence) -> list[MediaCandidate]:
    groups: dict[str, list[MediaCandidate]] = {}
    for m in ev.media:
        groups.setdefault(f"{m.kind}:{_asset_key(m.url)}", []).append(m)

    stems = _hero_stems(ev)

    def relevance(key: str, group: list[MediaCandidate]) -> tuple:
        asset = key.split(":", 1)[1]
        # Count matching hero tokens, don't just test membership: on a page
        # with sibling colorways every colorway shares the product-name
        # tokens, but only the displayed one also matches its color tokens.
        stem_hits = sum(1 for s in stems if s in asset)
        # Blob key paths are framework vocabulary, not site vocabulary:
        # "selected"-ish paths mean the displayed product, "related"-ish
        # paths mean some other product.
        hints = " ".join(m.path_hint.lower() for m in group)
        demoted = bool(_OTHER_PRODUCT_PATH.search(hints)) and not _SELECTED_PATH.search(hints)
        boosted = bool(_SELECTED_PATH.search(hints))
        # Appearing in several channels (og + blob + DOM) is a product-image
        # signal; chrome and guides usually live in exactly one.
        spread = min(len({m.origin for m in group}), 3)
        return (not demoted, boosted, stem_hits, spread, _quality(max(group, key=_quality)))

    scored = [(relevance(k, g), k, g) for k, g in groups.items()]

    # Hard exclusions, not just ranking. Related-rail media is never this
    # product's; and when the blob explicitly marks a selected product's
    # media, everything outside that marking is another colorway/product.
    image_scores = [s for s, k, _ in scored if k.startswith("image:")]
    n_clean = sum(1 for s in image_scores if s[0])
    n_boosted = sum(1 for s in image_scores if s[1])
    keep = []
    for s, k, g in scored:
        if k.startswith("image:"):
            if not s[0] and n_clean >= 3:
                continue  # demoted (related/recommended) with enough clean ones
            if n_boosted >= 3 and not s[1] and all(m.origin in ("blob", "json_ld") for m in g):
                continue  # unselected blob media on a page that marks selection
        keep.append((s, k, g))

    keep.sort(key=lambda t: t[0], reverse=True)
    best = [max(g, key=_quality) for _, _, g in keep]
    images = [m for m in best if m.kind == "image"][:_MAX_IMAGES]
    videos = [m for m in best if m.kind == "video"][:_MAX_VIDEOS]
    return images + videos


# print the numbered IMG_n and VID_n table the model picks from
def _render_media(media: list[MediaCandidate]) -> str:
    lines = []
    img_i = vid_i = 0
    for m in media:
        if m.kind == "image":
            lines.append(f"IMG_{img_i} {m.url}")
            img_i += 1
        else:
            lines.append(f"VID_{vid_i} {m.url}")
            vid_i += 1
    return "\n".join(lines)


# parallel lists so IMG_n and VID_n indices map straight back to urls
def media_by_index(media: list[MediaCandidate]) -> tuple[list[str], list[str]]:
    return ([m.url for m in media if m.kind == "image"],
            [m.url for m in media if m.kind == "video"])


# the identity section header lines
def _render_identity(ev: Evidence) -> str:
    lines = []
    if ev.h1:
        lines.append(f"h1: {ev.h1}")
    if ev.title:
        lines.append(f"title: {ev.title}")
    if ev.canonical_url:
        lines.append(f"canonical: {ev.canonical_url}")
    return "\n".join(lines)


# cap a section at its character budget
def _fit(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    return text[:budget] + "\n…[truncated]"


# long lists of same-shaped dicts (sku records, size runs) serialize with the
# keys repeated per row; rendering them as one header plus value rows fits
# ~2-3x more records in the same budget, which is what lets all 83 of a
# page's sku records reach the model instead of 57
def _tabulate(node, depth: int = 0):
    if depth > 25:
        return node
    if isinstance(node, dict):
        return {k: _tabulate(v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        if (len(node) >= 6 and all(isinstance(i, dict) for i in node)):
            keys = list(node[0].keys())
            key_set = set(keys)
            if all(set(i.keys()) <= key_set for i in node) and len(keys) <= 14:
                rows = [[_cell(i.get(k, "")) for k in keys] for i in node]
                if all(c is not None for r in rows for c in r):
                    return {"_columns": keys, "_rows": rows}
        return [_tabulate(i, depth + 1) for i in node]
    return node


# flatten a row value to a compact scalar; None means "too deep, don't tabulate"
def _cell(v):
    if isinstance(v, (str, int, float, bool)) or v is None and False:
        return v
    if v is None or v == [] or v == {}:
        return ""
    if isinstance(v, list) and all(isinstance(i, (str, int, float, bool)) for i in v):
        return "|".join(str(i) for i in v[:12])
    if isinstance(v, dict) and len(v) <= 4 and all(
            isinstance(x, (str, int, float, bool, type(None))) for x in v.values()):
        return ";".join(f"{k}={x}" for k, x in v.items())
    return None


# waterfall the blob budget: blobs arrive sorted by commerce score and the
# best one gets what it needs before the next sees a byte (one joined fit
# would let a low-value 300KB state dump truncate the product blob)
def _fit_blobs(blobs: list, budget: int) -> str:
    parts, remaining = [], budget
    for b in blobs:
        if b is None or remaining < 2_000:
            break
        text = json.dumps(_tabulate(b), ensure_ascii=False, default=str)
        parts.append(_fit(text, remaining))
        remaining -= min(len(text), remaining)
    return "\n".join(parts)
