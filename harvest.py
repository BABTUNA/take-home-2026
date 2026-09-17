"""Harvest: raw HTML in, Evidence out.

Five generic channels, in decreasing order of structure: JSON-LD, meta tags,
embedded JSON blobs, raw script text, visible text. Nothing here knows about
any specific site; blobs are found by shape (large JSON with commerce-looking
keys), never by name.
"""

import html as html_lib
import json
import math
import re

from bs4 import BeautifulSoup

from models import Evidence, JsonBlob, MediaCandidate, ScriptText

# Keys that indicate a JSON object is about a product. Used to score blobs so
# we keep the product state and drop router/config/analytics JSON.
_COMMERCE_KEYS = {
    "price", "prices", "sku", "skus", "variant", "variants", "offers",
    "image", "images", "media", "stock", "availability", "currency",
    "brand", "color", "colors", "size", "sizes", "product", "products",
}

_IMAGE_EXT = re.compile(r"\.(?:jpe?g|png|webp|avif|gif)(?:\?|$)", re.I)
_VIDEO_EXT = re.compile(r"\.(?:mp4|webm|m3u8)(?:\?|$)", re.I)
# Generic "this is an image CDN path" signal for extensionless URLs
# (Scene7 / imgix / DAM style), e.g. cdni.llbean.net/is/image/wim/224626_0_44
# or cdn-tp6.mozu.com/.../files/<uuid>
_IMAGE_PATH_HINT = re.compile(r"/(?:is/image|images?|media|photos?|files?)/", re.I)
# Tracking beacons render as <img> tags too; these tokens identify analytics
# vendors/endpoints, not any particular store.
_NOISE_URL = re.compile(r"analytics|beacon|pixel|track|telemetry|metrics|/api/|doubleclick|facebook\.com/tr", re.I)


# run all five channels over one page and return the evidence bundle
def harvest(raw_html: str) -> Evidence:
    soup = BeautifulSoup(raw_html, "lxml")
    ev = Evidence()

    _extract_page_identity(soup, ev)
    _extract_json_ld(soup, ev)
    _extract_meta(soup, ev)
    _extract_scripts(soup, ev)
    _collect_media(soup, ev)
    # Visible text goes last: it mutates the tree (decomposes chrome).
    ev.visible_text = _extract_visible_text(soup)
    return ev


# grab title h1 and canonical url
def _extract_page_identity(soup: BeautifulSoup, ev: Evidence) -> None:
    if soup.title and soup.title.string:
        ev.title = soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        ev.h1 = h1.get_text(" ", strip=True)
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        ev.canonical_url = canonical["href"].strip()


# parse every ld+json block with a retry ladder for cdata and escaped bodies
def _extract_json_ld(soup: BeautifulSoup, ev: Evidence) -> None:
    for tag in soup.find_all("script", type="application/ld+json"):
        body = tag.string or tag.get_text()
        if not body:
            continue
        # Retry ladder: some sites double-escape or wrap in CDATA.
        for candidate in (body, body.strip().removeprefix("<![CDATA[").removesuffix("]]>"),
                          html_lib.unescape(body)):
            try:
                parsed = json.loads(candidate)
                ev.json_ld.append(parsed)
                break
            except (json.JSONDecodeError, ValueError):
                continue


# collect og twitter product and description meta tags
def _extract_meta(soup: BeautifulSoup, ev: Evidence) -> None:
    for tag in soup.find_all("meta"):
        key = tag.get("property") or tag.get("name")
        content = tag.get("content")
        if not key or not content:
            continue
        key = key.strip().lower()
        if key.startswith(("og:", "twitter:", "product:")) or key in ("description", "keywords"):
            # First occurrence wins; some pages repeat og:image for galleries,
            # but those URLs are also picked up by the media collector.
            ev.meta.setdefault(key, content.strip())


# channels c and d in one pass over every script tag
def _extract_scripts(soup: BeautifulSoup, ev: Evidence) -> None:
    for tag in soup.find_all("script"):
        stype = (tag.get("type") or "").lower()
        if stype == "application/ld+json":
            continue
        body = tag.string or tag.get_text()
        if not body or len(body) < 200:
            continue

        blobs = _json_objects_in_script(body, stype)
        kept_any = False
        for data in blobs:
            score = _commerce_score(data)
            if score > 0:
                source = tag.get("id") or stype or "inline"
                ev.json_blobs.append(JsonBlob(source=source, score=score, data=data))
                kept_any = True

        if not kept_any:
            # No usable JSON: keep the raw text if it smells like product
            # data (Flight payloads, JSON.parse strings we failed to decode).
            density = _keyword_density(body)
            if density > 0.5:
                ev.script_texts.append(ScriptText(score=density, text=body))

    ev.json_blobs.sort(key=lambda b: b.score, reverse=True)
    ev.script_texts.sort(key=lambda s: s.score, reverse=True)


# find json in a script body without knowing the site's naming
# handles the three shapes seen in the wild: a whole-body json script,
# `window.X = {...};` assignments (raw_decode, the statement continues after
# the object), and JSON.parse("...") with an escaped string argument
def _json_objects_in_script(body: str, stype: str) -> list:
    stripped = body.strip()
    results = []

    if stype in ("application/json", "text/json") or stripped.startswith(("{", "[")):
        try:
            results.append(json.loads(stripped))
            return results
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    # Assignment-style: start decoding at each `= {` offset.
    for m in re.finditer(r"=\s*(\{)", body):
        start = m.start(1)
        try:
            obj, _ = decoder.raw_decode(body, start)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and len(str(obj)) > 500:
            results.append(obj)
    # JSON.parse-style: the argument is a JS string literal containing JSON.
    for m in re.finditer(r"JSON\.parse\(\s*(['\"])", body):
        start = m.end() - 1
        try:
            literal, _ = decoder.raw_decode(body[start:].replace("\\'", "'"), 0)
            if isinstance(literal, str):
                results.append(json.loads(literal))
        except (json.JSONDecodeError, ValueError):
            continue
    return results


# score how product-like a json object's keys are, keeps blobs and drops config
def _commerce_score(data, _depth: int = 0) -> float:
    keys = set()
    _walk_keys(data, keys, 0)
    if not keys:
        return 0.0
    hits = sum(1 for k in keys if any(c in k.lower() for c in _COMMERCE_KEYS))
    return hits / math.sqrt(len(keys))


def _walk_keys(node, out: set, depth: int) -> None:
    if depth > 8 or len(out) > 2000:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            out.add(str(k))
            _walk_keys(v, out, depth + 1)
    elif isinstance(node, list):
        for item in node[:50]:
            _walk_keys(item, out, depth + 1)


# rough product-keyword density, decides if unparseable script text is kept
def _keyword_density(text: str) -> float:
    sample = text[:100_000].lower()
    hits = sum(sample.count(k) for k in ("price", "sku", "variant", "image", "product", "currency"))
    return hits / math.log(len(sample) + 10)


_CHROME_TAGS = ("script", "style", "noscript", "svg", "iframe", "nav", "header", "footer", "aside")
# Attributes whose values carry visible meaning (swatch names, price labels).
_INLINE_ATTRS = ("aria-label", "alt", "title")


# strip chrome then flatten to text with aria-label alt and title inlined
def _extract_visible_text(soup: BeautifulSoup) -> str:
    for role in ("navigation", "banner", "contentinfo", "complementary"):
        for el in soup.find_all(attrs={"role": role}):
            el.decompose()
    for tag_name in _CHROME_TAGS:
        for el in soup.find_all(tag_name):
            el.decompose()

    # Inline label-bearing attributes into the text stream so option names
    # and aria price labels survive the flattening.
    for el in soup.find_all(True):
        labels = [el.attrs[a] for a in _INLINE_ATTRS
                  if isinstance(el.attrs.get(a), str) and len(el.attrs[a]) > 2]
        if labels:
            el.append(" [" + " | ".join(dict.fromkeys(labels)) + "] ")

    text = soup.get_text("\n", strip=True)
    # Collapse runs of blank/duplicate lines.
    lines, prev = [], None
    for line in text.splitlines():
        line = line.strip()
        if line and line != prev:
            lines.append(line)
            prev = line
    return "\n".join(lines)


# gather every image and video url from dom meta and blobs with provenance
def _collect_media(soup: BeautifulSoup, ev: Evidence) -> None:
    seen: dict[str, MediaCandidate] = {}

    def add(url: str, kind: str, origin: str, width: int | None = None, path_hint: str = ""):
        url = url.strip()
        if not url.startswith(("http://", "https://", "//")):
            return
        if url.startswith("//"):
            url = "https:" + url
        # 1x1 pixels, svg icons, and tracking beacons are never product media.
        if "1x1" in url or url.lower().endswith(".svg") or _NOISE_URL.search(url):
            return
        existing = seen.get(url)
        if existing is not None:
            # Same URL sighted again: keep the best width and accumulate the
            # key paths (an asset can appear under both a product-group path
            # and a selectedProduct path, and ranking needs to see both).
            if path_hint and path_hint not in existing.path_hint:
                existing.path_hint = f"{existing.path_hint} {path_hint}".strip()
            if (width or 0) > (existing.width or 0):
                existing.width = width
        else:
            seen[url] = MediaCandidate(url=url, kind=kind, origin=origin, width=width,
                                       path_hint=path_hint)
        # For sized renditions (?w=320 style) also offer the bare asset URL,
        # which CDNs serve at original resolution. It joins the same dedupe
        # group and wins only if nothing else is cleaner.
        if kind == "image" and re.search(r"[?&](?:w|wid|width|h|hei|sw|size|q|quality|max)=\d", url):
            base = url.split("?")[0]
            if base not in seen and _IMAGE_EXT.search(base + "?"):
                seen[base] = MediaCandidate(url=base, kind=kind, origin=origin, width=None)

    for img in soup.find_all("img"):
        if img.get("src"):
            add(img["src"], "image", "dom")
        for url, width in _parse_srcset(img.get("srcset") or img.get("data-srcset") or ""):
            add(url, "image", "dom", width)
    for source in soup.find_all("source"):
        kind = "video" if (source.get("type") or "").startswith("video") else "image"
        if source.get("src"):
            add(source["src"], kind, "dom")
        for url, width in _parse_srcset(source.get("srcset") or ""):
            add(url, kind, "dom", width)
    for link in soup.find_all("link", rel="preload"):
        if link.get("as") == "image" and link.get("href"):
            add(link["href"], "image", "dom")
    for video in soup.find_all("video"):
        if video.get("src"):
            add(video["src"], "video", "dom")

    for key in ("og:image", "og:image:secure_url", "twitter:image"):
        if key in ev.meta:
            add(ev.meta[key], "image", "meta")
    if "og:video" in ev.meta:
        add(ev.meta["og:video"], "video", "meta")

    # URLs inside JSON-LD and blobs: walk every string value, remembering the
    # key path so ranking can tell a product gallery from a related-items rail.
    def walk_for_urls(node, origin, path="", depth=0):
        # State blobs nest deep (Next.js pageProps chains sit ~10 levels down),
        # so the cap is generous; the per-list cap is what bounds the walk.
        if depth > 25:
            return
        if isinstance(node, str) and node.startswith(("http", "//")):
            if _VIDEO_EXT.search(node):
                add(node, "video", origin, path_hint=path)
            elif _IMAGE_EXT.search(node) or _IMAGE_PATH_HINT.search(node):
                add(node, "image", origin, path_hint=path)
        elif isinstance(node, dict):
            for k, v in node.items():
                walk_for_urls(v, origin, f"{path}.{k}"[-120:], depth + 1)
        elif isinstance(node, list):
            for item in node[:200]:
                walk_for_urls(item, origin, path, depth + 1)

    for block in ev.json_ld:
        walk_for_urls(block, "json_ld")
    for blob in ev.json_blobs:
        walk_for_urls(blob.data, "blob")

    ev.media = list(seen.values())


# split a srcset attribute into url and width pairs
def _parse_srcset(srcset: str) -> list[tuple[str, int | None]]:
    out = []
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        pieces = part.split()
        url = pieces[0]
        width = None
        if len(pieces) > 1 and pieces[1].endswith("w"):
            try:
                width = int(pieces[1][:-1])
            except ValueError:
                pass
        out.append((url, width))
    return out
