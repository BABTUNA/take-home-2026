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
    "blobs": 45_000,
    "scripts": 8_000,
    "text": 10_000,
}
_MAX_IMAGES = 40
_MAX_VIDEOS = 5

_STOPWORDS = {"the", "and", "for", "with", "men", "mens", "women", "womens", "s"}


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


def _identity_tokens(ev: Evidence) -> set[str]:
    parts = [ev.h1 or "", ev.meta.get("og:title", ""), ev.title or ""]
    tokens = set()
    for p in parts:
        for tok in re.findall(r"[a-z0-9]+", p.lower()):
            if len(tok) > 2 and tok not in _STOPWORDS:
                tokens.add(tok)
    return tokens


def _name_matches_identity(name: str, identity: set[str]) -> bool:
    toks = {t for t in re.findall(r"[a-z0-9]+", name.lower()) if len(t) > 2}
    if not toks or not identity:
        return True  # can't judge, keep
    return len(toks & identity) / len(toks) >= 0.3


def _filter_json_ld(ev: Evidence, identity: set[str]) -> list:
    """Keep Product blocks about THIS page plus breadcrumbs (category signal).

    Pages ship JSON-LD for related items and bundle components too; a Product
    block whose name shares almost no tokens with the page identity is about
    a different product.
    """
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
    r"|analytics|tracking|gtm|experiment|featureflag|abtest|consent|cookie"
    r"|router|routes|routing|webpack|chunks|assets|styles|warehouses?)(?:$|_)", re.I)


def _prune(node, identity: set[str], depth: int = 0, summary: bool = False):
    """Keep subtrees that carry commerce keys or mention the page identity.

    Under a related/recommended-products key, `summary` mode kicks in: sibling
    products only matter for their identity (name, color, price, URL), so
    their nested media/variant/stock structures are dropped instead of
    letting one rail eat the whole blob budget.
    """
    if depth > 25:
        return None
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            kl = str(k).lower()
            if _NOISE_KEY.search(kl.replace("-", "_")):
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
        # Long opaque strings (base64, inlined CSS/JS) are token sinks.
        if len(node) > 500:
            return node[:500] + "…"
        return node
    return node


def _subtree_is_relevant(node, identity: set[str]) -> bool:
    keys: set[str] = set()
    _collect_keys(node, keys, 0)
    if any(c in k.lower() for k in keys for c in _COMMERCE_KEYS):
        return True
    text = json.dumps(node, default=str)[:2000].lower()
    return any(tok in text for tok in list(identity)[:10])


def _collect_keys(node, out: set, depth: int) -> None:
    if depth > 4 or len(out) > 200:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            out.add(str(k))
            _collect_keys(v, out, depth + 1)
    elif isinstance(node, list):
        for item in node[:20]:
            _collect_keys(item, out, depth + 1)


# ---------------------------------------------------------------------------
# Media resolution
# ---------------------------------------------------------------------------

# Path segments that encode a rendition size, e.g. /2890x1500/ or _400x.jpg
_SIZE_SEGMENT = re.compile(r"/\d{2,4}x\d{0,4}/|_\d{2,4}x\d{0,4}(?=\.)|w_\d+|h_\d+")


_RENDITION_WORDS = {"mini", "thumb", "thumbnail", "standard", "full", "max", "square",
                    "small", "large", "medium", "micro", "zoom", "default", "original"}

# Framework-generic key-path vocabulary for media ranking.
_OTHER_PRODUCT_PATH = re.compile(
    r"related|recommend|upsell|cross|similar|recently|alsolike|youmay", re.I)
_SELECTED_PATH = re.compile(r"selected|current|active", re.I)


def _asset_key(url: str) -> str:
    """Identity of the underlying asset, ignoring rendition/size differences.

    Some CDNs (Centra-style) give every rendition of the same shot its own
    content hash AND a size word in the filename, so we normalize the
    filename: drop hash-looking tokens (hex with letters), rendition words,
    and WxH tokens, keep the rest.
    """
    base = url.split("?")[0].split("#")[0]
    base = _SIZE_SEGMENT.sub("/", base)
    segments = base.rstrip("/").split("/")[-2:]
    name = segments[-1].rsplit(".", 1)[0]
    kept = []
    for tok in re.split(r"[^a-z0-9]+", name.lower()):
        if not tok or tok in _RENDITION_WORDS:
            continue
        if len(tok) >= 6 and re.fullmatch(r"[0-9a-f]+", tok) and re.search(r"[a-f]", tok):
            continue  # content hash, not identity
        if re.fullmatch(r"\d{2,4}x\d{0,4}", tok):
            continue
        kept.append(tok)
    folder = segments[0].lower() if len(segments) > 1 else ""
    return f"{folder}/{'-'.join(kept)}" if kept else "/".join(segments).lower()


def _quality(m: MediaCandidate) -> tuple:
    # Prefer blob/JSON-LD origins (usually the full-res set), then the clean
    # canonical URL over sized renditions (?w=320 style), then srcset width.
    origin_rank = {"blob": 3, "json_ld": 3, "meta": 1, "dom": 2}.get(m.origin, 0)
    has_size_params = 1 if re.search(r"[?&](?:w|wid|width|h|hei|sw|size)=\d", m.url) else 0
    return (origin_rank, -has_size_params, m.width or 0, len(m.url.split("?")[0]))


_GENERIC_PATH_WORDS = {"image", "images", "product", "products", "media", "photo",
                       "photos", "files", "default", "thumb", "large", "small", "assets"}


def _hero_stems(ev: Evidence) -> set[str]:
    """Identifier tokens from the page's own hero image URL (og/twitter image).

    Product galleries share an asset id or sku token with the hero, either in
    the filename (224626_0_44 -> 224626) or a path segment (/SKU25289/). That
    separates them from same-CDN content like size guides and cross-sells.
    """
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


def media_by_index(media: list[MediaCandidate]) -> tuple[list[str], list[str]]:
    """Parallel lists: image URLs by IMG index, video URLs by VID index."""
    return ([m.url for m in media if m.kind == "image"],
            [m.url for m in media if m.kind == "video"])


def _render_identity(ev: Evidence) -> str:
    lines = []
    if ev.h1:
        lines.append(f"h1: {ev.h1}")
    if ev.title:
        lines.append(f"title: {ev.title}")
    if ev.canonical_url:
        lines.append(f"canonical: {ev.canonical_url}")
    return "\n".join(lines)


def _fit(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    return text[:budget] + "\n…[truncated]"


def _fit_blobs(blobs: list, budget: int) -> str:
    """Waterfall the budget: blobs arrive sorted by commerce score, and the
    best one gets whatever it needs before the next sees a byte. One joined
    _fit would let a low-value 300KB state dump truncate the product blob."""
    parts, remaining = [], budget
    for b in blobs:
        if b is None or remaining < 2_000:
            break
        text = json.dumps(b, ensure_ascii=False, default=str)
        parts.append(_fit(text, remaining))
        remaining -= min(len(text), remaining)
    return "\n".join(parts)
