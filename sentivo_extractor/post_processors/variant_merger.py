"""
Merge simple products that share a base title into one Shopify product
with Size variants (common on Magento catalogs where each size is a PDP).
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from sentivo_extractor.core.utils import stable_handle

logger = logging.getLogger(__name__)

# Shopify hard limit is 100 variants; keep a 1-slot buffer.
MAX_VARIANTS_PER_PRODUCT = 99

# Prefer this plain-text length before inheriting a sibling description.
MIN_RICH_DESCRIPTION_LEN = 500

_COLOR_WORDS = frozenset(
    {
        "black",
        "white",
        "natural",
        "clear",
        "blue",
        "green",
        "red",
        "yellow",
        "orange",
        "brown",
        "grey",
        "gray",
        "beige",
        "ivory",
        "transparent",
    }
)

_PART_SUFFIX_RE = re.compile(r"\s*\(Part\s+\d+\)\s*$", re.I)
_SAMPLE_WORD_RE = re.compile(r"\bsamples?\b", re.I)
_LEADING_SIZE_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*(?:mm|cm|m)\b\s*",
    re.I,
)

# Multi-part dimensions: 6mm dia x 500mm | 6mm × 500mm | 250 x 250 x 2mm
_MULTI_DIM_RE = re.compile(
    r"""
    (?P<dim>
        \d+(?:\.\d+)?\s*(?:mm|cm|m)?
        (?:\s*(?:dia(?:meter)?|ø)\b)?
        (?:\s*[x×]\s*\d+(?:\.\d+)?\s*(?:mm|cm|m)?(?:\s*(?:dia(?:meter)?|ø)\b)?)+
    )
    """,
    re.I | re.X,
)

# Lone size token: 6mm, 500mm (used when stripping leftovers after multi-dim removal)
_SINGLE_DIM_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mm|cm|m)\b",
    re.I,
)

# Trailing size segment on URL slugs:
# acetal-black-rod-6mm-dia-x-500mm → acetal-black-rod
_SLUG_DIM_TAIL_RE = re.compile(
    r"""
    -(?:
        \d+(?:\.\d+)?(?:mm|cm|m)
        (?:-dia(?:meter)?)?
        (?:-x-\d+(?:\.\d+)?(?:mm|cm|m)(?:-dia(?:meter)?)?)+
      |
        \d+(?:\.\d+)?(?:mm|cm|m)
    )
    (?:-.*)?$
    """,
    re.I | re.X,
)

_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")


def extract_size_value(title: str) -> str:
    """
    Return the dimension fragment from a product title, normalized for Shopify Option1.

    Examples:
      'Acetal Black Rod 6mm dia x 500mm' → '6mm × 500mm'
      'Acetal Black Rod 8mm × 1000mm' → '8mm × 1000mm'
    """
    text = (title or "").strip()
    if not text:
        return ""
    match = _MULTI_DIM_RE.search(text)
    if not match:
        singles = list(_SINGLE_DIM_RE.finditer(text))
        if not singles:
            return ""
        return _normalize_size_label(singles[-1].group(0))
    return _normalize_size_label(match.group("dim"))


def extract_base_title(title: str) -> str:
    """
    Strip dimension patterns from a title to get the shared parent name.

    'Acetal Black Rod 6mm dia x 500mm' → 'Acetal Black Rod'
    """
    text = (title or "").strip()
    if not text:
        return ""
    stripped = _MULTI_DIM_RE.sub(" ", text)
    stripped = _SINGLE_DIM_RE.sub(" ", stripped)
    stripped = _WS_RE.sub(" ", stripped).strip(" -–—|,;")
    return stripped


def _normalize_size_label(raw: str) -> str:
    label = _WS_RE.sub(" ", (raw or "").strip())
    if not label:
        return ""
    label = re.sub(r"\s*(?:dia(?:meter)?|ø)\s*", " ", label, flags=re.I)
    label = label.replace("x", "×").replace("X", "×")
    label = re.sub(r"\s*×\s*", " × ", label)
    label = _WS_RE.sub(" ", label).strip()
    label = re.sub(r"(\d+(?:\.\d+)?)\s+(mm|cm|m)\b", r"\1\2", label, flags=re.I)
    return label


def _domain_key(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _image_dedupe_key(src: str) -> str:
    """Collapse Magento cache-path / size variants of the same file."""
    if not src:
        return ""
    parsed = urlparse(str(src).strip().lower())
    path = parsed.path or ""
    path = re.sub(r"/cache/[a-f0-9]+/", "/", path)
    path = re.sub(r"/cache/[^/]+/", "/", path)
    path = re.sub(r"[-_](\d{2,4})x(\d{2,4})(?=\.|/|$)", "", path)
    path = re.sub(r"[-_](\d{2,4})w(?=\.|$)", "", path)
    return f"{parsed.netloc}{path}".rstrip("/")


def _is_simple_mergeable_product(product: dict[str, Any]) -> bool:
    """True for single-variant simple PDPs (Default Title or lone option)."""
    variants = [v for v in (product.get("variants") or []) if isinstance(v, dict)]
    if len(variants) != 1:
        return False
    v0 = variants[0]
    if str(v0.get("option2") or "").strip() or str(v0.get("option3") or "").strip():
        return False
    options = [o for o in (product.get("options") or []) if isinstance(o, dict)]
    if len(options) > 1:
        return False
    return True


def _pick_variant_seed(product: dict[str, Any]) -> dict[str, Any]:
    variants = [v for v in (product.get("variants") or []) if isinstance(v, dict)]
    if variants:
        return deepcopy(variants[0])
    return {
        "sku": str(product.get("sku") or ""),
        "barcode": "",
        "option1": "",
        "option2": "",
        "option3": "",
        "price": str(product.get("price") or ""),
        "compare_at_price": str(product.get("compare_at_price") or ""),
        "inventory_qty": "",
        "available": True,
        "weight_grams": "",
        "variant_image": "",
    }


def _merge_image_lists(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    images: list[dict[str, Any]] = []
    for product in products:
        for img in product.get("images") or []:
            if not isinstance(img, dict):
                continue
            src = str(img.get("src") or "").strip()
            if not src:
                continue
            key = _image_dedupe_key(src)
            if not key or key in seen:
                continue
            seen.add(key)
            images.append(
                {
                    "src": src,
                    "alt": img.get("alt") or "",
                    "position": len(images) + 1,
                }
            )
    return images


def _longest_description(products: list[dict[str, Any]]) -> str:
    best = ""
    for product in products:
        html = str(product.get("description_html") or "")
        if len(html) > len(best):
            best = html
    return best


def _plain_text_length(html_or_text: str) -> int:
    text = html_or_text or ""
    try:
        plain = BeautifulSoup(text, "lxml").get_text(" ", strip=True)
    except Exception:
        plain = _TAG_RE.sub(" ", text)
    return len(_WS_RE.sub(" ", plain).strip())


def category_slug_candidates(url: str) -> list[str]:
    """
    Derive category-page slug candidates from a size PDP URL.

    '…/acetal-black-rod-6mm-dia-x-500mm' → ['acetal-black-rod', 'acetal-rod']
    """
    path = urlparse(url or "").path.rstrip("/")
    if not path:
        return []
    slug = path.rsplit("/", 1)[-1].strip().lower()
    if not slug:
        return []

    candidates: list[str] = []
    stripped = _SLUG_DIM_TAIL_RE.sub("", slug).strip("-")
    if stripped and stripped != slug:
        candidates.append(stripped)
        parts = [p for p in stripped.split("-") if p]
        if len(parts) >= 3:
            short = f"{parts[0]}-{parts[-1]}"
            if short not in candidates:
                candidates.append(short)
    return candidates


def _slugify_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower())
    return slug.strip("-")


def strip_sample_size_color_title(title: str, *, strip_color: bool = False) -> str:
    """
    Clean a title for category/sibling matching.

    '20mm Sample Acetal Black Rod' → 'Acetal Black Rod'
    (with strip_color=True → 'Acetal Rod')
    """
    text = _PART_SUFFIX_RE.sub("", title or "").strip()
    text = extract_base_title(text) or text
    text = _SAMPLE_WORD_RE.sub(" ", text)
    text = _LEADING_SIZE_RE.sub("", text.strip())
    text = _MULTI_DIM_RE.sub(" ", text)
    text = _SINGLE_DIM_RE.sub(" ", text)
    if strip_color:
        words = [
            w
            for w in _WS_RE.split(text.strip())
            if w and w.lower() not in _COLOR_WORDS
        ]
        text = " ".join(words)
    text = _WS_RE.sub(" ", text).strip(" -–—|,;")
    return text


def _title_category_slugs(title: str) -> list[str]:
    """Slug candidates from a cleaned product title."""
    seen: set[str] = set()
    out: list[str] = []
    for cleaned in (
        strip_sample_size_color_title(title, strip_color=False),
        strip_sample_size_color_title(title, strip_color=True),
    ):
        if not cleaned:
            continue
        slug = _slugify_title(cleaned)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
    return out


def _category_url_candidates(
    source_urls: list[str],
    *,
    title: str = "",
    include_title_fallback: bool = True,
) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    origin = ""
    for raw in source_urls:
        url = str(raw or "").strip()
        if not url:
            continue
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            continue
        if not origin:
            origin = f"{parsed.scheme}://{parsed.netloc}"
        base = f"{parsed.scheme}://{parsed.netloc}"
        for slug in category_slug_candidates(url):
            candidate = f"{base}/{slug}"
            if candidate in seen:
                continue
            seen.add(candidate)
            out.append(candidate)

    # When URL slug guesses are empty (or as extra fallback), derive from title.
    if include_title_fallback and origin and title:
        if not out:
            for slug in _title_category_slugs(title):
                candidate = f"{origin}/{slug}"
                if candidate in seen:
                    continue
                seen.add(candidate)
                out.append(candidate)
        else:
            # Keep URL-based first; append title-based only if those fail later.
            pass
    return out


def _title_fallback_category_urls(source_urls: list[str], title: str) -> list[str]:
    """Category URLs derived only from cleaned title (Sample/size/color stripped)."""
    origin = ""
    for raw in source_urls:
        parsed = urlparse(str(raw or "").strip())
        if parsed.scheme and parsed.netloc:
            origin = f"{parsed.scheme}://{parsed.netloc}"
            break
    if not origin or not title:
        return []
    return [f"{origin}/{slug}" for slug in _title_category_slugs(title)]


def _extract_category_description(html: str) -> str:
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return ""
    el = soup.select_one(".category-description")
    if not el:
        return ""
    try:
        content = str(el.decode_contents()).strip()
    except Exception:
        content = el.get_text(" ", strip=True)
    return content if _plain_text_length(content) > 0 else ""


def _try_category_urls(
    parent: dict[str, Any],
    candidates: list[str],
    *,
    http: Any,
    log: logging.Logger,
    fetch_cache: dict[str, str | None],
) -> bool:
    """Apply first longer category description from candidates. True if updated."""
    current = str(parent.get("description_html") or "")
    current_len = _plain_text_length(current)
    for cat_url in candidates:
        if cat_url in fetch_cache:
            desc = fetch_cache[cat_url]
        else:
            try:
                html = http.get_text(cat_url)
                desc = _extract_category_description(html)
            except Exception as exc:  # noqa: BLE001
                log.debug("Category description fetch failed for %s: %s", cat_url, exc)
                desc = None
            fetch_cache[cat_url] = desc

        if not desc:
            continue
        desc_len = _plain_text_length(desc)
        if desc_len > current_len:
            parent["description_html"] = desc
            parent["category_description_url"] = cat_url
            log.info(
                "Category description applied for '%s' from %s (%s → %s chars)",
                parent.get("title"),
                cat_url,
                current_len,
                desc_len,
            )
            return True
    return False


def _enrich_description_from_category(
    parent: dict[str, Any],
    *,
    http: Any | None,
    log: logging.Logger,
    cache: dict[str, str | None] | None = None,
) -> None:
    """
    Fetch category/parent page and replace description when .category-description
    is longer than the current (usually short meta) body.

    If URL slug guesses fail completely, retry using a cleaned title
    (Sample / size prefixes / color words stripped).
    """
    if http is None or not hasattr(http, "get_text"):
        return

    urls = list(parent.get("merged_from_urls") or [])
    if not urls:
        src = str(parent.get("source_url") or "")
        if src:
            urls = [src]
    title = str(parent.get("title") or "")
    base_title = str(
        (parent.get("variant_merge") or {}).get("base_title") or title
    )
    fetch_cache = cache if cache is not None else {}

    url_candidates = _category_url_candidates(
        urls, title=title, include_title_fallback=False
    )
    applied = False
    if url_candidates:
        applied = _try_category_urls(
            parent, url_candidates, http=http, log=log, fetch_cache=fetch_cache
        )

    # URL guess empty or produced no usable description → cleaned-title retry.
    if not applied and _plain_text_length(
        str(parent.get("description_html") or "")
    ) < MIN_RICH_DESCRIPTION_LEN:
        title_candidates = _title_fallback_category_urls(urls, base_title or title)
        # Avoid re-trying URLs already attempted.
        title_candidates = [u for u in title_candidates if u not in url_candidates]
        if title_candidates:
            log.debug(
                "Category URL guess failed for '%s'; retrying cleaned title slugs: %s",
                title,
                title_candidates,
            )
            _try_category_urls(
                parent,
                title_candidates,
                http=http,
                log=log,
                fetch_cache=fetch_cache,
            )


def _related_title_keys(title: str) -> list[str]:
    """Normalized keys used to find sibling products with richer descriptions."""
    keys: list[str] = []
    seen: set[str] = set()
    for cleaned in (
        strip_sample_size_color_title(title, strip_color=False),
        strip_sample_size_color_title(title, strip_color=True),
    ):
        key = _WS_RE.sub(" ", cleaned).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def _inherit_descriptions_from_siblings(
    products: list[dict[str, Any]],
    *,
    log: logging.Logger,
) -> None:
    """
    For products still under MIN_RICH_DESCRIPTION_LEN chars, copy description
    from a related sibling (same domain) that already has a rich body.

    Example: '20mm Sample Acetal Black Rod' ← 'Acetal Black Rod'
    """
    if len(products) < 2:
        return

    # Index rich products by related title keys.
    rich_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for product in products:
        if not isinstance(product, dict):
            continue
        desc = str(product.get("description_html") or "")
        desc_len = _plain_text_length(desc)
        if desc_len < MIN_RICH_DESCRIPTION_LEN:
            continue
        domain = _domain_key(str(product.get("source_url") or ""))
        titles = [
            str(product.get("title") or ""),
            str((product.get("variant_merge") or {}).get("base_title") or ""),
        ]
        for title in titles:
            for key in _related_title_keys(title):
                index_key = (domain, key)
                existing = rich_by_key.get(index_key)
                if existing is None or desc_len > _plain_text_length(
                    str(existing.get("description_html") or "")
                ):
                    rich_by_key[index_key] = product

    if not rich_by_key:
        return

    for product in products:
        if not isinstance(product, dict):
            continue
        current = str(product.get("description_html") or "")
        current_len = _plain_text_length(current)
        if current_len >= MIN_RICH_DESCRIPTION_LEN:
            continue
        domain = _domain_key(str(product.get("source_url") or ""))
        titles = [
            str(product.get("title") or ""),
            str((product.get("variant_merge") or {}).get("base_title") or ""),
        ]
        donor: dict[str, Any] | None = None
        for title in titles:
            for key in _related_title_keys(title):
                candidate = rich_by_key.get((domain, key))
                if candidate is None or candidate is product:
                    continue
                # Prefer exact cleaned-title match (keeps color) over color-stripped.
                donor = candidate
                break
            if donor is not None:
                break
        if donor is None:
            continue
        donor_desc = str(donor.get("description_html") or "")
        donor_len = _plain_text_length(donor_desc)
        if donor_len <= current_len:
            continue
        product["description_html"] = donor_desc
        product["description_inherited_from"] = str(donor.get("title") or "")
        log.info(
            "Inherited description for '%s' from sibling '%s' (%s → %s chars)",
            product.get("title"),
            donor.get("title"),
            current_len,
            donor_len,
        )


def _split_over_variant_limit(product: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Shopify allows at most 100 variants. Split into Part 1 / Part 2 / …
    with at most MAX_VARIANTS_PER_PRODUCT (99) each.
    """
    variants = [v for v in (product.get("variants") or []) if isinstance(v, dict)]
    if len(variants) <= MAX_VARIANTS_PER_PRODUCT:
        return [product]

    base_title = str(
        (product.get("variant_merge") or {}).get("base_title") or product.get("title") or ""
    ).strip() or str(product.get("title") or "Product").strip()
    source_url = str(product.get("source_url") or "")
    total_parts = (len(variants) + MAX_VARIANTS_PER_PRODUCT - 1) // MAX_VARIANTS_PER_PRODUCT
    parts: list[dict[str, Any]] = []

    for part_idx in range(total_parts):
        start = part_idx * MAX_VARIANTS_PER_PRODUCT
        chunk_variants = deepcopy(
            variants[start : start + MAX_VARIANTS_PER_PRODUCT]
        )
        size_values = [str(v.get("option1") or "") for v in chunk_variants]
        part_num = part_idx + 1
        part = deepcopy(product)
        part_title = f"{base_title} (Part {part_num})"
        part["title"] = part_title
        part["handle"] = stable_handle(part_title, source_url or base_title)
        part["variants"] = chunk_variants
        part["options"] = [{"name": "Size", "values": size_values}]
        merge_meta = dict(part.get("variant_merge") or {})
        merge_meta.update(
            {
                "base_title": base_title,
                "variant_count": len(chunk_variants),
                "part": part_num,
                "part_count": total_parts,
            }
        )
        part["variant_merge"] = merge_meta
        parts.append(part)

    return parts


def _merge_group(
    base_title: str,
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    parent = deepcopy(members[0])
    parent["title"] = base_title
    parent["handle"] = stable_handle(base_title, str(members[0].get("source_url") or ""))

    size_values: list[str] = []
    variants: list[dict[str, Any]] = []
    seen_sizes: set[str] = set()
    source_urls: list[str] = []

    for product in members:
        size = extract_size_value(str(product.get("title") or ""))
        if not size:
            raw = str(product.get("title") or "")
            remainder = raw.replace(base_title, "", 1).strip(" -–—|")
            size = _normalize_size_label(remainder) or raw
        size_key = size.lower()
        if size_key in seen_sizes:
            continue
        seen_sizes.add(size_key)
        size_values.append(size)

        seed = _pick_variant_seed(product)
        seed["option1"] = size
        seed["option2"] = ""
        seed["option3"] = ""
        imgs = product.get("images") or []
        if (
            isinstance(imgs, list)
            and imgs
            and isinstance(imgs[0], dict)
            and not seed.get("variant_image")
            and imgs[0].get("src")
        ):
            seed["variant_image"] = str(imgs[0]["src"])
        variants.append(seed)

        url = str(product.get("source_url") or "")
        if url:
            source_urls.append(url)

    parent["options"] = [{"name": "Size", "values": size_values}]
    parent["variants"] = variants
    parent["images"] = _merge_image_lists(members)
    parent["description_html"] = _longest_description(members)
    parent["source_url"] = source_urls[0] if source_urls else parent.get("source_url")
    parent["merged_from_urls"] = source_urls
    parent["variant_merge"] = {
        "base_title": base_title,
        "member_count": len(members),
        "variant_count": len(variants),
    }
    method = str(parent.get("extraction_method") or "").strip()
    if "VariantMerge" not in method:
        parent["extraction_method"] = (
            f"{method}+VariantMerge" if method else "VariantMerge"
        )
    try:
        parent["confidence_score"] = max(
            float(p.get("confidence_score") or 0) for p in members
        )
    except Exception:
        pass
    return parent


def merge_products_by_base_title(
    products: list[dict[str, Any]],
    *,
    log: logging.Logger | None = None,
    http: Any | None = None,
) -> list[dict[str, Any]]:
    """
    Group simple products that share a dimension-stripped base title into one
    Shopify product with Option1=Size variants.

    After merge, optionally enrich description from the category/parent page
    (``.category-description``). If still short, inherit from a related sibling
    product. Products with more than 99 variants are split into ``(Part N)`` groups.

    Products that already have multiple variants, or that do not share a base
    title with another simple product on the same domain, are left unchanged
    (but may still receive category/sibling description enrichment).
    """
    log = log or logger
    if not products:
        return list(products)

    groups: OrderedDict[tuple[str, str], list[dict[str, Any]]] = OrderedDict()
    for product in products:
        if not isinstance(product, dict):
            continue
        if not _is_simple_mergeable_product(product):
            continue
        title = str(product.get("title") or "").strip()
        base = extract_base_title(title)
        size = extract_size_value(title)
        if not base or not size or base.lower() == title.lower():
            continue
        domain = _domain_key(str(product.get("source_url") or ""))
        groups.setdefault((domain, base.lower()), []).append(product)

    mergeable_ids: dict[int, tuple[str, str]] = {}
    parents: dict[tuple[str, str], list[dict[str, Any]]] = {}
    merge_count = 0
    split_extra = 0
    category_cache: dict[str, str | None] = {}

    for key, members in groups.items():
        if len(members) < 2:
            continue
        base_title = extract_base_title(str(members[0].get("title") or ""))
        parent = _merge_group(base_title, members)
        _enrich_description_from_category(
            parent, http=http, log=log, cache=category_cache
        )
        parts = _split_over_variant_limit(parent)
        if len(parts) > 1:
            split_extra += len(parts) - 1
            log.info(
                "Variant split: '%s' → %s parts (%s variants total, max %s/part)",
                base_title,
                len(parts),
                len(parent.get("variants") or []),
                MAX_VARIANTS_PER_PRODUCT,
            )
        parents[key] = parts
        merge_count += 1
        for m in members:
            mergeable_ids[id(m)] = key
        domain, _ = key
        log.info(
            "Variant merge: '%s' ← %s simple PDPs → %s Size variants (domain=%s)",
            base_title,
            len(members),
            len(parent.get("variants") or []),
            domain or "unknown",
        )

    if merge_count == 0:
        result = list(products)
    else:
        result = []
        emitted: set[tuple[str, str]] = set()
        for product in products:
            key = mergeable_ids.get(id(product))
            if key is None:
                result.append(product)
                continue
            if key in emitted:
                continue
            emitted.add(key)
            result.extend(parents[key])

    # Unmerged / sample products: try category fetch (incl. cleaned-title fallback).
    for product in result:
        if not isinstance(product, dict):
            continue
        if _plain_text_length(str(product.get("description_html") or "")) >= (
            MIN_RICH_DESCRIPTION_LEN
        ):
            continue
        # Skip parents already enriched during merge (has category URL) unless still short.
        _enrich_description_from_category(
            product, http=http, log=log, cache=category_cache
        )

    _inherit_descriptions_from_siblings(result, log=log)

    log.info(
        "Variant merger complete: %s parent group(s) formed%s; output count %s → %s",
        merge_count,
        f", {split_extra} extra part product(s)" if split_extra else "",
        len(products),
        len(result),
    )
    return result
