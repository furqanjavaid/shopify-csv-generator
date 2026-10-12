"""WooCommerce product-image filtering (gallery-first, strict JSON-LD fallback)."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import unquote, urlparse

from sentivo_extractor.core.utils import absolute_url
from sentivo_extractor.core.woocommerce_title import looks_like_woocommerce_url

# Hard caps
WOO_MAX_IMAGES = 10
WOO_MAX_JSONLD_IMAGES = 5

# Prefer specific gallery slides; broader gallery container last.
GALLERY_SELECTORS = (
    ".woocommerce-product-gallery__image img[src]",
    ".woocommerce-product-gallery__image img",
    "figure.woocommerce-product-gallery__wrapper img[src]",
    "figure.woocommerce-product-gallery__wrapper img",
    "div.woocommerce-product-gallery img[src]",
    "div.woocommerce-product-gallery img",
    ".woocommerce-product-gallery__image a",
)

_STOP_SLUG_PARTS = frozenset(
    {
        "product",
        "products",
        "shop",
        "store",
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "for",
        "with",
        "to",
        "in",
        "mm",
        "cm",
        "x",
        "www",
        "http",
        "https",
        "html",
        "htm",
        "jpg",
        "jpeg",
        "png",
        "webp",
        "gif",
    }
)

_EMOJI_SVG_RE = re.compile(r"s\.w\.org|/emoji|twemoji|wp-includes/images/smilies", re.I)


def product_slug_from_url(url: str) -> str:
    path = urlparse(url or "").path.rstrip("/")
    if not path:
        return ""
    return unquote(path.split("/")[-1] or "").strip().lower()


def slug_keywords(url: str = "", title: str = "") -> list[str]:
    """Meaningful tokens from product slug / title for image URL matching."""
    parts: list[str] = []
    slug = product_slug_from_url(url)
    if slug:
        parts.extend(re.split(r"[-_+/]+", slug))
    if title:
        t = str(title).split(" | ")[0]
        parts.extend(re.split(r"[^a-zA-Z0-9]+", t.lower()))
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        tok = (p or "").strip().lower()
        if len(tok) < 3 or tok in _STOP_SLUG_PARTS or tok.isdigit():
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def is_woocommerce_junk_image_url(url: str) -> bool:
    """Filter emoji SVGs, nav chrome, and other non-product assets."""
    if not url:
        return True
    low = url.lower()
    if _EMOJI_SVG_RE.search(low):
        return True
    if low.startswith("data:"):
        return True
    if any(
        token in low
        for token in (
            "/theme/menu/",
            "favicon",
            "sprite",
            "placeholder",
            "1x1",
            "pixel.",
            "spinner",
            "/avatar/",
        )
    ):
        return True
    # Magento-style non-catalog /media/ (allow WP uploads).
    if "/media/" in low and "/media/catalog/product/" not in low:
        if "/wp-content/" not in low and "/uploads/" not in low:
            return True
    return False


def _img_src_from_node(node: Any, base_url: str) -> str:
    if node is None:
        return ""
    if getattr(node, "name", "") == "a":
        href = str(node.get("href") or "").strip()
        if href and not href.startswith("#") and not href.lower().endswith(".html"):
            abs_href = absolute_url(base_url, href)
            if abs_href and not is_woocommerce_junk_image_url(abs_href):
                # Prefer linked full-size image when it looks like an image URL.
                if re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", abs_href, re.I):
                    return abs_href
        img = node.find("img")
        if img is not None:
            return _img_src_from_node(img, base_url)
        return ""

    src = (
        node.get("data-large_image")
        or node.get("data-src")
        or node.get("data-lazy-src")
        or node.get("src")
        or ""
    )
    if not src and node.get("srcset"):
        src = (node.get("srcset") or "").split(",")[0].strip().split(" ")[0]
    src = str(src or "").strip()
    if not src or src.startswith("data:"):
        return ""
    return absolute_url(base_url, src)


def _normalize_gallery_rows(
    rows: list[dict[str, Any]],
    base_url: str = "",
    *,
    max_images: int = WOO_MAX_IMAGES,
    min_px: int = 50,
) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        src = absolute_url(base_url, str(row.get("src") or "").strip())
        if not src or src in seen:
            continue
        if is_woocommerce_junk_image_url(src):
            continue
        w = int(row.get("width") or 0)
        h = int(row.get("height") or 0)
        # Drop tiny icons when dimensions known (naturalWidth/Height from Playwright).
        if (w and w < min_px) or (h and h < min_px):
            continue
        seen.add(src)
        images.append(
            {
                "src": src,
                "alt": str(row.get("alt") or ""),
                "position": len(images) + 1,
                "source": str(row.get("source") or "woocommerce_gallery"),
            }
        )
        if len(images) >= max_images:
            break
    return images


def extract_woocommerce_gallery_from_page(
    page: Any,
    base_url: str = "",
    *,
    max_images: int = WOO_MAX_IMAGES,
    logger: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    """
    Extract gallery image URLs from an already-loaded Playwright page.

    Must use the same page/browser that fetched the PDP (proxy cookies/session).
    Does not launch a new browser or issue a separate navigation.
    """
    log = logger or logging.getLogger("sentivo_extractor")
    if page is None:
        return []

    selectors = list(GALLERY_SELECTORS)
    try:
        raw = page.evaluate(
            """(sels) => {
                const out = [];
                const seen = new Set();
                const push = (img, src) => {
                    if (!src || seen.has(src)) return;
                    if (String(src).startsWith('data:')) return;
                    seen.add(src);
                    out.push({
                        src: src,
                        alt: img.getAttribute('alt') || '',
                        width: img.naturalWidth || parseInt(img.getAttribute('width') || '0', 10) || 0,
                        height: img.naturalHeight || parseInt(img.getAttribute('height') || '0', 10) || 0,
                    });
                };
                for (const sel of sels) {
                    let nodes;
                    try { nodes = document.querySelectorAll(sel); } catch (e) { continue; }
                    for (const node of nodes) {
                        let img = node;
                        if (node.tagName === 'A') {
                            const href = node.getAttribute('href') || '';
                            const child = node.querySelector('img');
                            if (href && /\\.(jpe?g|png|webp|gif)(\\?|$)/i.test(href)) {
                                push(child || node, href);
                                continue;
                            }
                            img = child;
                        }
                        if (!img) continue;
                        const src = img.getAttribute('data-large_image')
                            || img.getAttribute('data-src')
                            || img.getAttribute('data-lazy-src')
                            || img.currentSrc
                            || img.getAttribute('src')
                            || '';
                        push(img, src);
                    }
                    if (out.length) break;
                }
                return out;
            }""",
            selectors,
        )
    except Exception as exc:
        log.warning(
            "[WooCommerce] Gallery extract from Playwright page failed for %s: %s",
            base_url,
            exc,
        )
        return []

    images = _normalize_gallery_rows(
        list(raw or []),
        base_url,
        max_images=max_images,
    )
    for img in images:
        img["source"] = "woocommerce_gallery_playwright"
    log.info(
        "[WooCommerce] Gallery images from Playwright: %s found for %s",
        len(images),
        base_url,
    )
    return images


def extract_woocommerce_gallery_images(
    html: str,
    base_url: str = "",
    *,
    max_images: int = WOO_MAX_IMAGES,
) -> list[dict[str, Any]]:
    """Extract WooCommerce product gallery images from static HTML."""
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sel in GALLERY_SELECTORS:
        try:
            nodes = soup.select(sel)
        except Exception:
            continue
        for node in nodes:
            src = _img_src_from_node(node, base_url)
            if not src or src in seen:
                continue
            seen.add(src)
            alt = ""
            width = 0
            height = 0
            img_node = node
            if getattr(node, "name", "") != "img" and getattr(node, "find", None):
                img_node = node.find("img") or node
            if getattr(img_node, "name", "") == "img":
                alt = str(img_node.get("alt") or "")
                try:
                    width = int(img_node.get("width") or 0)
                except Exception:
                    width = 0
                try:
                    height = int(img_node.get("height") or 0)
                except Exception:
                    height = 0
            rows.append(
                {
                    "src": src,
                    "alt": alt,
                    "width": width,
                    "height": height,
                    "source": "woocommerce_gallery",
                }
            )
            if len(rows) >= max_images * 2:
                break
        if rows:
            break
    return _normalize_gallery_rows(rows, base_url, max_images=max_images)


def _url_matches_slug_keywords(image_url: str, keywords: list[str]) -> bool:
    if not keywords:
        return False
    path = unquote(urlparse(image_url or "").path or "").lower()
    strong = [k for k in keywords if len(k) >= 4]
    check = strong or keywords
    return any(k in path for k in check)


def filter_woocommerce_jsonld_images(
    images: list[Any],
    *,
    url: str = "",
    title: str = "",
    max_images: int = WOO_MAX_JSONLD_IMAGES,
) -> list[dict[str, Any]]:
    """
    Keep JSON-LD images that look product-specific:
    - URL contains product slug keywords, OR
    - first image in the JSON-LD array (primary product image)
    Cap at max_images (default 5).
    """
    keywords = slug_keywords(url, title)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(images or []):
        if isinstance(item, dict):
            src = str(item.get("src") or item.get("url") or "").strip()
            alt = str(item.get("alt") or "")
        else:
            src = str(item or "").strip()
            alt = ""
        if not src:
            continue
        src = absolute_url(url, src)
        if src in seen or is_woocommerce_junk_image_url(src):
            continue
        keep = idx == 0 or _url_matches_slug_keywords(src, keywords)
        if not keep:
            continue
        seen.add(src)
        out.append(
            {
                "src": src,
                "alt": alt,
                "position": len(out) + 1,
                "source": "jsonld_filtered",
            }
        )
        if len(out) >= max_images:
            break
    return out


def resolve_woocommerce_product_images(
    product: dict[str, Any],
    html: str = "",
    *,
    gallery_images: list[dict[str, Any]] | None = None,
    logger: logging.Logger | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    Gallery-first image resolution for WooCommerce products.

    1) Pre-extracted Playwright gallery images (same proxied page), else HTML gallery
    2) If gallery empty → first JSON-LD / existing image only
    Always caps at WOO_MAX_IMAGES.
    """
    if not isinstance(product, dict):
        return product

    src_url = str(product.get("source_url") or "")
    platform = str(product.get("platform") or "")
    method = str(product.get("extraction_method") or "").lower()
    is_woo = (
        force
        or platform == "WooCommerce"
        or "woocommerce" in method
        or looks_like_woocommerce_url(src_url)
    )
    if not is_woo:
        return product

    log = logger or logging.getLogger("sentivo_extractor")
    title = str(product.get("title") or "")
    existing = list(product.get("images") or [])

    gallery: list[dict[str, Any]] = []
    if gallery_images:
        gallery = _normalize_gallery_rows(
            list(gallery_images), src_url, max_images=WOO_MAX_IMAGES
        )
        for img in gallery:
            img.setdefault("source", "woocommerce_gallery_playwright")
    if not gallery:
        gallery = extract_woocommerce_gallery_images(
            html, src_url, max_images=WOO_MAX_IMAGES
        )

    if gallery:
        product["images"] = _reposition(gallery[:WOO_MAX_IMAGES])
        log.info(
            "[WooCommerce] Using product gallery images: %s (was %s)",
            len(product["images"]),
            len(existing),
        )
        return product

    # No gallery — fall back to first JSON-LD / existing image only.
    filtered = filter_woocommerce_jsonld_images(
        existing,
        url=src_url,
        title=title,
        max_images=WOO_MAX_JSONLD_IMAGES,
    )
    if filtered:
        product["images"] = _reposition(filtered[:1])
        log.info(
            "[WooCommerce] Gallery empty — keeping first JSON-LD image only "
            "(dropped %s)",
            max(0, len(existing) - 1),
        )
    elif existing:
        first = existing[0]
        if isinstance(first, dict):
            product["images"] = _reposition(
                [
                    {
                        "src": absolute_url(
                            src_url, str(first.get("src") or first.get("url") or "")
                        ),
                        "alt": str(first.get("alt") or ""),
                        "source": "jsonld_primary",
                    }
                ]
            )
        else:
            product["images"] = _reposition(
                [
                    {
                        "src": absolute_url(src_url, str(first)),
                        "alt": "",
                        "source": "jsonld_primary",
                    }
                ]
            )
        log.info(
            "[WooCommerce] Gallery empty — keeping first existing image only "
            "(dropped %s)",
            max(0, len(existing) - 1),
        )
    else:
        product["images"] = []

    return product


def apply_woocommerce_image_cap(
    product: dict[str, Any],
    *,
    max_images: int = WOO_MAX_IMAGES,
) -> dict[str, Any]:
    """Hard-cap product images (used after merge / image engine)."""
    if not isinstance(product, dict):
        return product
    images = list(product.get("images") or [])
    if len(images) > max_images:
        product["images"] = _reposition(images[:max_images])
    else:
        product["images"] = _reposition(images)
    return product


def _reposition(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, img in enumerate(images or [], start=1):
        if not isinstance(img, dict):
            continue
        row = dict(img)
        row["position"] = i
        out.append(row)
    return out
