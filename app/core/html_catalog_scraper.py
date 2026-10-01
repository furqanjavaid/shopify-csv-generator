"""Playwright-based HTML catalog scraper for non-Shopify stores."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse

import requests
from playwright.sync_api import sync_playwright

from app.core.collection_crawler import (
    CollectionCrawlError,
    drafts_to_parsed_data,
)
from app.utils.helpers import slugify

ProgressCallback = Callable[[str], None]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATEGORY_DEBUG_DIR = PROJECT_ROOT / "output" / "debug"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

TITLE_SELECTORS = [
    "h1",
]
PRICE_SELECTORS = [
    '[itemprop="price"]',
    ".price",
    '[class*="price"]',
    '[class*="Price"]',
    "span.amount",
    '[class*="product-price"]',
]
IMAGE_SELECTORS = [
    ".product-image img",
    '[class*="product"] img',
    "article img",
    "li img",
    "main img",
    "img",
]
PRODUCT_LINK_SELECTORS = [
    'a[href*="/product"]',
    'a[href*="/shop"]',
    'a[href*="/item"]',
    'a[href*="/p/"]',
]
NEXT_LINK_SELECTORS = [
    'a[rel="next"]',
    'a[aria-label="Next"]',
    'a[aria-label="next"]',
    'a[class*="next"]',
    'a[class*="Next"]',
    'li.next a',
    '.pagination a.next',
    'a.page-link[rel="next"]',
]

# Candidate product-card selectors for probe / YAML-debug logging
PRODUCT_CARD_CANDIDATE_SELECTORS = [
    ".product",
    ".product-item",
    ".product-card",
    "article",
    ".grid-item",
    ".grid__item",
    "li.product",
    ".woocommerce ul.products li.product",
    "[class*='product-card']",
    "[class*='product-item']",
    "[class*='product_tile']",
    ".card",
]

# Paths / labels that are not product categories
_CATEGORY_SKIP_FRAGMENTS = (
    "/blog",
    "/cart",
    "/checkout",
    "/account",
    "/my-account",
    "/login",
    "/register",
    "/wishlist",
    "/contact",
    "/about",
    "/faq",
    "/help",
    "/privacy",
    "/terms",
    "/conditions",
    "/shipping",
    "/delivery",
    "/returns",
    "/policy",
    "/cookie",
    "/cookies",
    "/news",
    "/advice",
    "/sustainability",
    "/applications",
    "/material-names",
    "/customer",
    "/search",
    "/sample",
    "/samples",
    "/data-sheet",
    "/datasheet",
    "/data_sheet",
    "/tolerances",
    "/tolerance",
    "/cutting",
    "/cutting-service",
)

# Obvious CMS / system paths — never treat as categories
_CMS_PATH_FRAGMENTS = (
    "/wp-admin",
    "/wp-content",
    "/wp-includes",
    "/wp-json",
    "/wp-login",
    "/cgi-bin",
    "/xmlrpc",
    "/cdn-cgi",
    "/admin",
    "/administrator",
    "/cms/",
    "/node/",
    "/taxonomy/",
    "/tag/",
    "/tags/",
    "/author/",
    "/user/",
    "/users/",
    "/feed",
    "/rss",
    "/sitemap",
    "/static/",
    "/assets/",
    "/media/",
    "/uploads/",
    "/_next/",
    "/api/",
    "/graphql",
    "/cart/",
    "/checkout/",
    "/pages/",  # CMS static pages (not product categories)
    "/page/",   # often CMS single pages when not /page/N pagination
)

_CATEGORY_SKIP_LABELS = (
    "blog",
    "contact",
    "shipping",
    "delivery",
    "delivery charges",
    "faq",
    "help",
    "about",
    "terms",
    "terms and conditions",
    "conditions",
    "privacy",
    "privacy policy",
    "cookie",
    "cookies",
    "cookie policy",
    "login",
    "account",
    "my account",
    "cart",
    "checkout",
    "home",
    "search",
    "sustainability",
    "advice",
    "inspiration",
    "customer service",
    "returns",
    "news",
    "cutting",
    "cutting service",
    "service",
    "sample",
    "samples",
    "data sheets",
    "data sheet",
    "datasheet",
    "datasheets",
    "tolerances",
    "tolerance",
)

# Substring keywords matched against label + path (informational pages)
_INFORMATIONAL_KEYWORDS = (
    "delivery",
    "returns",
    "shipping",
    "privacy",
    "cookie",
    "cookies",
    "contact",
    "about",
    "blog",
    "news",
    "faq",
    "help",
    "terms",
    "conditions",
    "cutting",
    "service",
    "sample",
    "samples",
    "data-sheet",
    "datasheet",
    "data sheet",
    "data_sheet",
    "tolerances",
    "tolerance",
    "login",
    "register",
    "wishlist",
    "cart",
    "checkout",
    "account",
)

# URL path hints that look like real product categories
_CATEGORY_URL_HINTS = (
    "/collections/",
    "/collection/",
    "/product-category/",
    "/product_category/",
    "/categories/",
    "/category/",
    "/shop/",
    "/catalog/",
    "/catalogue/",
    "/departments/",
    "/department/",
    "/c/",
)


def _emit(progress: Optional[ProgressCallback], message: str) -> None:
    if progress:
        try:
            progress(message)
        except Exception:
            pass


def detect_shopify(base_url: str) -> bool:
    """True if /products.json returns JSON with a products key."""
    probe = f"{base_url.rstrip('/')}/products.json?limit=1"
    try:
        resp = requests.get(
            probe,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=12,
            allow_redirects=True,
        )
        if resp.status_code >= 400:
            return False
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "json" not in ctype and not resp.text.strip().startswith("{"):
            return False
        data = resp.json()
        return isinstance(data, dict) and "products" in data
    except Exception:
        return False


def detect_woocommerce(html: str, base_url: str = "") -> bool:
    """Heuristic WooCommerce detection from HTML or common endpoints."""
    low = (html or "").lower()
    if any(
        token in low
        for token in (
            "woocommerce",
            "wp-content/plugins/woocommerce",
            "wc-block",
            "product-type-simple",
            'generator" content="woocommerce',
        )
    ):
        return True
    if base_url:
        try:
            resp = requests.get(
                f"{base_url.rstrip('/')}/wp-json/wc/store/products?per_page=1",
                headers={"User-Agent": USER_AGENT},
                timeout=8,
                allow_redirects=True,
            )
            if resp.status_code == 200 and resp.text.strip().startswith("["):
                return True
        except Exception:
            pass
    return False


def detect_platform(url: str) -> str:
    """
    Return 'Shopify', 'WooCommerce', or 'Custom'.
    Lightweight — safe to call from UI after URL entry.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Custom"
    parsed = urlparse(url)
    if not parsed.netloc:
        return "Custom"
    base = f"{parsed.scheme}://{parsed.netloc}"

    if detect_shopify(base):
        return "Shopify"

    html = ""
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=12,
            allow_redirects=True,
        )
        html = resp.text[:200_000] if resp.ok else ""
    except Exception:
        html = ""

    if detect_woocommerce(html, base):
        return "WooCommerce"
    return "Custom"


def _normalize_price(text: str) -> str:
    """Return numeric price only (no currency symbol). Prefer last amount."""
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    matches = re.findall(r"\d[\d,]*\.?\d*", cleaned.replace(",", ""))
    if not matches:
        return ""
    raw = matches[-1]
    try:
        return f"{float(raw):.2f}"
    except ValueError:
        return raw


def _strip_price_from_option(val: str) -> str:
    """Remove currency amounts glued onto option labels (e.g. '… 3 mm£22.45')."""
    if not val:
        return ""
    cleaned = re.sub(r"[£$€]\s*\d[\d,]*(?:\.\d+)?", "", val)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _is_bad_title(title: str) -> bool:
    """True when title is empty, a section heading, or an error page."""
    t = (title or "").strip()
    if not t:
        return True
    low = t.lower()
    if "description" in low:
        return True
    if low in {"unfortunately", "error", "404", "not found"}:
        return True
    if low.startswith("unfortunately"):
        return True
    if low.startswith("not found") or low.startswith("404"):
        return True
    if low.startswith("error"):
        return True
    return False


def _unwrap_image_url(src: str, base_url: str) -> str:
    """
    Resolve image src. If Next.js ``_next/image/?url=`` wrapper, use decoded url=.
    """
    if not src or src.startswith("data:"):
        return ""
    absolute = urljoin(base_url, src)
    if "_next/image" not in absolute and "/_next/image" not in absolute:
        return absolute
    try:
        parsed = urlparse(absolute)
        qs = parse_qs(parsed.query)
        raw = (qs.get("url") or [""])[0]
        if not raw:
            return absolute
        real = unquote(raw)
        if real.startswith("//"):
            real = "https:" + real
        elif real.startswith("/"):
            real = urljoin(base_url, real)
        return real
    except Exception:
        return absolute


def _first_text(page_or_el, selectors: list[str]) -> str:
    for sel in selectors:
        try:
            el = page_or_el.query_selector(sel)
            if el and el.is_visible():
                text = (el.inner_text() or "").strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


def _first_image(page_or_el, selectors: list[str], base_url: str) -> str:
    for sel in selectors:
        try:
            el = page_or_el.query_selector(sel)
            if not el:
                continue
            src = (
                el.get_attribute("src")
                or el.get_attribute("data-src")
                or el.get_attribute("data-lazy-src")
                or ""
            )
            if not src:
                srcset = el.get_attribute("srcset") or ""
                if srcset:
                    src = srcset.split(",")[0].strip().split(" ")[0]
            resolved = _unwrap_image_url(src, base_url)
            if resolved:
                return resolved
        except Exception:
            continue
    return ""


def _extract_title_h1(page) -> str:
    """Title from first visible h1 only — never h2 / section headings."""
    try:
        for el in page.query_selector_all("h1"):
            try:
                if not el.is_visible():
                    continue
            except Exception:
                pass
            text = re.sub(r"\s+", " ", (el.inner_text() or "").strip())
            if text:
                return text[:300]
    except Exception:
        pass
    return ""


def _extract_description(page) -> str:
    """
    Plain text from the longest <p> or [class*="description"] block.
    Strip HTML and wrap in <p>…</p> for CSV. Empty string if nothing found.
    """
    candidates: list[str] = []
    try:
        for el in page.query_selector_all(
            'p, [class*="description"], [itemprop="description"]'
        ):
            try:
                html = el.inner_html() or ""
                text = _strip_html(html) if html else _strip_html(el.inner_text() or "")
            except Exception:
                continue
            text = text.strip()
            if len(text) < 20:
                continue
            # Skip tiny labels / nav crumbs
            if text.lower() in {"description", "product description"}:
                continue
            candidates.append(text)
    except Exception:
        return ""

    if not candidates:
        return ""
    best = max(candidates, key=len)
    # Escape minimal HTML specials so wrapping is safe
    best = best.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<p>{best}</p>"


def _extract_price_from_page(page) -> tuple[str, str]:
    """
    Price + compare-at from price selectors / currency near title.
    Numeric values only (no £/$).
    """
    price_raw = ""

    # itemprop content attribute first
    try:
        for el in page.query_selector_all('[itemprop="price"]'):
            content = (el.get_attribute("content") or "").strip()
            if content and re.search(r"\d", content):
                price_raw = content
                break
            t = (el.inner_text() or "").strip()
            if t and re.search(r"[£$€]|\d", t):
                price_raw = t
                break
    except Exception:
        pass

    if not price_raw:
        for sel in PRICE_SELECTORS:
            try:
                els = page.query_selector_all(sel)
                for el in els[:8]:
                    try:
                        if not el.is_visible():
                            continue
                    except Exception:
                        pass
                    t = (el.inner_text() or "").strip()
                    if t and re.search(r"[£$€]|\d", t):
                        price_raw = t
                        break
                if price_raw:
                    break
            except Exception:
                continue

    # Fallback: any element with £/$ near the h1
    if not price_raw:
        try:
            near = page.evaluate(
                """() => {
                    const h1 = document.querySelector('h1');
                    if (!h1) return '';
                    const root = h1.closest('main, article, [class*="product"], body') || document.body;
                    const walk = root.querySelectorAll('*');
                    for (const el of walk) {
                        const t = (el.childNodes.length && el.childElementCount === 0)
                            ? (el.textContent || '').trim()
                            : '';
                        if (!t || t.length > 40) continue;
                        if (/[£$€]\\s*\\d/.test(t) || /^\\d[\\d,.]*$/.test(t)) return t;
                    }
                    return '';
                }"""
            )
            if near:
                price_raw = str(near)
        except Exception:
            pass

    price, compare_at = _parse_price_pair(price_raw)

    # Prefer explicit sale vs regular
    try:
        sale_el = page.query_selector(
            '[class*="sale"] [class*="price"], [class*="price"][class*="sale"], '
            ".price--on-sale .price-item--sale, .special-price, ins .amount, ins"
        )
        regular_el = page.query_selector(
            '[class*="compare"] [class*="price"], [class*="price"][class*="compare"], '
            ".price--on-sale .price-item--regular, del .amount, del, s"
        )
        if sale_el and sale_el.is_visible():
            sale_p = _normalize_price(sale_el.inner_text() or "")
            if sale_p:
                price = sale_p
        if regular_el and regular_el.is_visible():
            reg_p = _normalize_price(regular_el.inner_text() or "")
            if reg_p and reg_p != price:
                compare_at = reg_p
    except Exception:
        pass

    return price, compare_at


def is_homepage_url(url: str) -> bool:
    """True when the URL path is empty or '/' (store root)."""
    try:
        path = (urlparse(url).path or "").strip()
        return path in ("", "/")
    except Exception:
        return False


def _listing_key(url: str) -> str:
    """Normalize listing URL for visited-set (keep ?page= query)."""
    parts = urlparse(url)
    path = parts.path.rstrip("/") or "/"
    return urlunparse(
        (parts.scheme.lower(), parts.netloc.lower(), path, "", parts.query, "")
    )


def _current_page_number(url: str) -> int:
    qs = parse_qs(urlparse(url).query)
    for key in ("page", "p", "paged", "pg"):
        if key in qs and qs[key]:
            try:
                return max(1, int(qs[key][0]))
            except (TypeError, ValueError):
                pass
    # /page/2/ style
    m = re.search(r"/page/(\d+)/?", urlparse(url).path, re.I)
    if m:
        try:
            return max(1, int(m.group(1)))
        except ValueError:
            pass
    return 1


def _with_page_number(url: str, page_num: int) -> str:
    """Set/replace ?page=N on a listing URL."""
    parts = urlparse(url)
    # Prefer query-string pagination for plasticsheetsshop-style sites
    qs = parse_qs(parts.query, keep_blank_values=True)
    # Drop other page keys then set page
    for key in ("page", "p", "paged", "pg"):
        qs.pop(key, None)
    if page_num <= 1:
        query = urlencode({k: v[0] if len(v) == 1 else v for k, v in qs.items()}, doseq=True)
        return urlunparse((parts.scheme, parts.netloc, parts.path, "", query, ""))
    qs["page"] = [str(page_num)]
    query = urlencode({k: v[0] if len(v) == 1 else v for k, v in qs.items()}, doseq=True)
    path = parts.path or "/"
    # If URL used /page/N/, rewrite path; else use ?page=
    if re.search(r"/page/\d+/?", path, re.I):
        path = re.sub(r"/page/\d+/?", f"/page/{page_num}/", path, flags=re.I)
        return urlunparse((parts.scheme, parts.netloc, path, "", "", ""))
    return urlunparse((parts.scheme, parts.netloc, path, "", query, ""))


def _normalize_informational_blob(label: str, path: str) -> str:
    text = f"{label or ''} {path or ''}".lower()
    text = text.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def _is_cms_path(path: str) -> bool:
    low = (path or "/").lower()
    # Allow /page/N pagination only; bare /page/foo is CMS
    if re.search(r"/page/\d+/?$", low):
        return False
    if re.search(r"/pages?/[^/]+", low) and not re.search(r"/page/\d+", low):
        # /page/about or /pages/shipping
        if any(
            kw in low
            for kw in (
                "about",
                "contact",
                "shipping",
                "delivery",
                "privacy",
                "terms",
                "faq",
                "help",
                "sample",
                "cutting",
            )
        ):
            return True
    return any(frag in low for frag in _CMS_PATH_FRAGMENTS)


def _is_informational_category(label: str, path: str) -> bool:
    """True if label/path looks like an informational / non-product page."""
    label_clean = re.sub(r"\s+", " ", (label or "").strip()).lower()
    if label_clean in _CATEGORY_SKIP_LABELS:
        return True
    low_path = (path or "/").lower()
    if any(frag in low_path for frag in _CATEGORY_SKIP_FRAGMENTS):
        return True
    if _is_cms_path(low_path):
        return True
    blob = _normalize_informational_blob(label_clean, low_path)
    path_compact = low_path.replace("_", "-")
    for kw in _INFORMATIONAL_KEYWORDS:
        kw_l = kw.lower()
        kw_spaced = kw_l.replace("-", " ").replace("_", " ")
        if kw_spaced in blob or kw_l in path_compact:
            return True
    return False


def _looks_like_category_url(path: str) -> bool:
    low = (path or "/").lower()
    return any(hint in low for hint in _CATEGORY_URL_HINTS)


def _count_card_selectors_in_html(html: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "lxml")
        for sel in PRODUCT_CARD_CANDIDATE_SELECTORS:
            try:
                counts[sel] = len(soup.select(sel))
            except Exception:
                counts[sel] = 0
    except Exception:
        for sel in PRODUCT_CARD_CANDIDATE_SELECTORS:
            counts[sel] = 0
    return counts


def _count_card_selectors_on_page(page) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sel in PRODUCT_CARD_CANDIDATE_SELECTORS:
        try:
            counts[sel] = len(page.query_selector_all(sel))
        except Exception:
            counts[sel] = 0
    return counts


def _log_selector_probe(
    category_url: str,
    counts: dict[str, int],
    progress: Optional[ProgressCallback] = None,
) -> None:
    lines = ["", f"Category:", category_url, ""]
    for sel, n in counts.items():
        lines.append(f'Selector "{sel}"')
        lines.append(f"{n} matches")
        lines.append("")
    text = "\n".join(lines)
    print(text, flush=True)
    for line in lines:
        if line:
            _emit(progress, line)


def _looks_like_product_href(href: str) -> bool:
    """True if an href looks like a product detail / listing product URL."""
    if not href:
        return False
    low = href.lower().split("#")[0].split("?")[0]
    if any(
        frag in low
        for frag in (
            "/product/",
            "/products/",
            "/product-",
            "/shop/",
            "/item/",
            "/p/",
            "/sku/",
            "/catalogue/",
            "/catalog/",
        )
    ):
        # Exclude pure category roots when possible
        if low.rstrip("/").endswith(("/shop", "/products", "/catalog", "/catalogue")):
            return False
        return True
    # Numeric or slug PDP patterns: /something/abc-123.html style shop paths
    if re.search(r"/[^/]+-p-\d+", low) or re.search(r"/p-\d+", low):
        return True
    return False


def _detect_platform_from_html(html: str, url: str = "") -> str:
    """Best-effort ecommerce platform from rendered/static HTML."""
    low = (html or "").lower()
    parsed = urlparse(url or "")
    base = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""

    if "cdn.shopify.com" in low or "shopify.theme" in low or "myshopify.com" in low:
        return "Shopify"
    if 'id="shopify-features"' in low or "shopify-section" in low:
        return "Shopify"
    if base and detect_shopify(base):
        return "Shopify"
    if detect_woocommerce(html, base):
        return "WooCommerce"
    if "__next_data__" in low or "/_next/" in low:
        return "Next.js"
    if "magento" in low or "mage/" in low or "catalog/product" in low:
        return "Magento"
    if "bigcommerce" in low:
        return "BigCommerce"
    if "squarespace" in low:
        return "Squarespace"
    if "wix.com" in low or "wixstatic" in low:
        return "Wix"
    if "prestashop" in low:
        return "PrestaShop"
    return "Custom"


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_script_json_by_id(soup, script_id: str) -> Any:
    el = soup.find("script", id=script_id)
    if not el:
        return None
    raw = (el.string or el.get_text() or "").strip()
    if not raw:
        return None
    return _safe_json_loads(raw)


def _extract_initial_state_from_html(html: str) -> Any:
    """Pull window.__INITIAL_STATE__ / similar assignments from inline scripts."""
    patterns = (
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?})\s*;?\s*</script>",
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?})\s*;",
        r"__INITIAL_STATE__\s*=\s*(\{.*?})\s*;",
    )
    for pat in patterns:
        m = re.search(pat, html or "", re.I | re.S)
        if not m:
            continue
        raw = m.group(1)
        # Cap extremely large blobs for regex safety — try parse; trim if needed
        if len(raw) > 2_000_000:
            raw = raw[:2_000_000]
        parsed = _safe_json_loads(raw)
        if parsed is not None:
            return parsed
    return None


def _count_product_like_anchors(hrefs: list[str]) -> int:
    return sum(1 for h in hrefs if _looks_like_product_href(h))


def build_page_analysis(
    *,
    category_url: str,
    html: str,
    selector_counts: dict[str, int] | None = None,
    raw_html: str | None = None,
    platform: str | None = None,
    initial_state: Any = None,
    final_url: str | None = None,
) -> dict[str, Any]:
    """
    Build a rich page analysis dict for YAML rule authoring without
    reopening the live site.
    """
    from bs4 import BeautifulSoup

    html = html or ""
    selector_counts = selector_counts or _count_card_selectors_in_html(html)
    soup = BeautifulSoup(html, "lxml")

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.title:
        title = soup.title.get_text(" ", strip=True)

    canonical = ""
    link_canon = soup.find("link", rel=lambda v: v and "canonical" in str(v).lower())
    if link_canon and link_canon.get("href"):
        canonical = str(link_canon["href"]).strip()

    h1_texts = [
        re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        for el in soup.find_all("h1")
        if el.get_text(strip=True)
    ]
    h2_texts = [
        re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        for el in soup.find_all("h2")
        if el.get_text(strip=True)
    ]

    all_anchors: list[dict[str, str]] = []
    all_hrefs: list[str] = []
    productish_anchors: list[dict[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        if not href:
            continue
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))[:200]
        abs_href = urljoin(category_url, href)
        all_hrefs.append(abs_href)
        entry = {"text": text, "href": abs_href, "raw_href": href}
        all_anchors.append(entry)
        if _looks_like_product_href(href) or _looks_like_product_href(abs_href):
            productish_anchors.append(entry)

    json_ld: list[Any] = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        parsed = _safe_json_loads(raw)
        json_ld.append(parsed if parsed is not None else {"_raw": raw[:5000]})

    next_data = _extract_script_json_by_id(soup, "__NEXT_DATA__")
    if initial_state is None:
        initial_state = _extract_initial_state_from_html(html)

    images = soup.find_all("img")
    total_images = len(images)

    rendered_product_signals = _count_product_like_anchors(all_hrefs)
    rendered_card_total = sum(int(v or 0) for v in selector_counts.values())

    js_only = False
    raw_product_signals = 0
    raw_card_total = 0
    if raw_html is not None:
        raw_counts = _count_card_selectors_in_html(raw_html)
        raw_card_total = sum(int(v or 0) for v in raw_counts.values())
        try:
            raw_soup = BeautifulSoup(raw_html, "lxml")
            raw_hrefs = [
                urljoin(category_url, str(a.get("href") or ""))
                for a in raw_soup.find_all("a", href=True)
                if a.get("href")
            ]
            raw_product_signals = _count_product_like_anchors(raw_hrefs)
        except Exception:
            raw_product_signals = 0
        js_only = (
            raw_product_signals == 0
            and raw_card_total == 0
            and (rendered_product_signals > 0 or rendered_card_total > 0)
        )

    detected_platform = platform or _detect_platform_from_html(html, category_url)

    analysis: dict[str, Any] = {
        "category_url": category_url,
        "final_url": final_url or category_url,
        "page_title": title,
        "canonical_url": canonical,
        "detected_ecommerce_platform": detected_platform,
        "products_appear_only_after_js_rendering": js_only,
        "total_dom_anchor_count": len(all_hrefs),
        "total_image_count": total_images,
        "h1_texts": h1_texts,
        "h2_texts": h2_texts,
        "selector_counts": selector_counts,
        "selector_match_total": rendered_card_total,
        "product_like_anchors": productish_anchors[:200],
        "product_like_anchor_count": len(productish_anchors),
        "first_100_anchor_hrefs": all_hrefs[:100],
        "json_ld_scripts": json_ld,
        "__NEXT_DATA__": next_data,
        "window.__INITIAL_STATE__": initial_state,
        "raw_html_product_like_anchor_count": raw_product_signals,
        "raw_html_selector_match_total": raw_card_total,
        "rendered_product_like_anchor_count": rendered_product_signals,
        "files": {
            "html": "category_debug.html",
            "screenshot": "category.png",
            "selectors": "selectors.txt",
            "analysis": "page_analysis.json",
            "generated_rule": "generated_site_rule.yaml",
            "product_urls": "product_urls.txt",
            "first_product_node": "first_product_node.html",
        },
        "notes": [
            "Use selector_counts + product_like_anchors to draft YAML product_card / product_url rules.",
            "If products_appear_only_after_js_rendering is true, enable Playwright for this domain.",
            "Inspect __NEXT_DATA__ / window.__INITIAL_STATE__ / json_ld_scripts for embedded product lists.",
            "See generated_site_rule.yaml for ranked draft selectors (debug only — not production).",
        ],
    }
    return analysis


# Selectors that typically match nav/footer chrome — never recommend these.
_NAV_FOOTER_SELECTOR_FRAGMENTS = (
    "nav",
    "footer",
    "header",
    "menu",
    "breadcrumb",
    "breadcrumbs",
    "sidebar",
    "cookie",
    "modal",
    "popup",
    "toolbar",
    "pagination",
    "pager",
)

_HIGH_PRODUCT_SELECTORS = (
    ".product-item",
    ".product-card",
    "li.product",
    ".woocommerce ul.products li.product",
    "[class*='product-card']",
    "[class*='product-item']",
    "[class*='product_tile']",
    ".grid-product",
)


def parse_selectors_txt(text: str) -> dict[str, int]:
    """Parse selectors.txt into {selector: match_count}."""
    counts: dict[str, int] = {}
    current: str | None = None
    for line in (text or "").splitlines():
        line = line.strip()
        m = re.match(r'^Selector\s+"(.+)"\s*$', line)
        if m:
            current = m.group(1)
            continue
        # Prefer "N nodes" / "N valid product URLs" lines; keep legacy "N matches"
        m_nodes = re.match(r"^(\d+)\s+nodes?\s*$", line, re.I)
        if m_nodes and current and current not in counts:
            counts[current] = int(m_nodes.group(1))
            continue
        m2 = re.match(r"^(\d+)\s+matches?\s*$", line, re.I)
        if m2 and current:
            counts[current] = int(m2.group(1))
            current = None
    return counts


def _is_nav_footer_selector(selector: str) -> bool:
    low = (selector or "").lower()
    for frag in _NAV_FOOTER_SELECTOR_FRAGMENTS:
        if low == frag or low.startswith(f"{frag}.") or low.startswith(f"{frag} "):
            return True
        if f".{frag}" in low or f"#{frag}" in low or f"{frag}-" in low:
            if frag in ("nav", "footer", "header", "menu", "breadcrumb", "breadcrumbs"):
                if re.search(rf"(^|[^\w-]){re.escape(frag)}([^\w-]|$)", low):
                    return True
    return False


def _is_homepage_href(href: str, base_url: str = "") -> bool:
    if not href:
        return False
    abs_url = urljoin(base_url or "https://example.com/", href)
    parsed = urlparse(abs_url)
    path = (parsed.path or "/").rstrip("/") or "/"
    return path == "/" and not (parsed.query or "").strip()


def _is_category_href(href: str, base_url: str = "") -> bool:
    """True if href looks like a category/listing page (not a product PDP)."""
    if not href:
        return False
    abs_url = urljoin(base_url or "https://example.com/", href)
    parsed = urlparse(abs_url)
    path = (parsed.path or "/").lower()
    if _looks_like_category_url(path):
        # /shop/foo-product-slug with deeper path may still be PDP under /shop/
        depth = len([p for p in path.split("/") if p])
        # Pure category roots / shallow category indexes
        if depth <= 2 and not _looks_like_product_href(href):
            return True
        if path.rstrip("/").endswith(
            ("/shop", "/products", "/catalog", "/catalogue", "/collections", "/category")
        ):
            return True
    if _is_informational_category("", path):
        return True
    return False


def _href_validity_flags(href: str, base_url: str = "") -> dict[str, bool]:
    """Measure individual href quality flags for debug / scoring."""
    raw = (href or "").strip()
    low = raw.lower()
    exists = bool(raw)
    return {
        "href_exists": exists,
        "not_javascript": exists and not low.startswith("javascript:"),
        "not_hash_only": exists and low not in ("#",) and not low.startswith("#"),
        "not_mailto": exists and not low.startswith("mailto:"),
        "not_tel": exists and not low.startswith("tel:"),
        "not_category_page": exists and not _is_category_href(raw, base_url),
        "not_homepage": exists and not _is_homepage_href(raw, base_url),
    }


def _is_valid_product_href(href: str, base_url: str = "") -> bool:
    """
    Valid product URL for selector scoring:
    - href exists
    - not javascript: / # / mailto: / tel:
    - not category page / homepage
    - looks like a product URL
    """
    flags = _href_validity_flags(href, base_url)
    if not all(
        (
            flags["href_exists"],
            flags["not_javascript"],
            flags["not_hash_only"],
            flags["not_mailto"],
            flags["not_tel"],
            flags["not_category_page"],
            flags["not_homepage"],
        )
    ):
        return False
    return _looks_like_product_href(href)


def _element_product_hrefs(el, base_url: str = "") -> list[str]:
    """Collect hrefs from element itself (if <a>) and descendant anchors."""
    hrefs: list[str] = []
    try:
        if getattr(el, "name", None) == "a" and el.get("href"):
            hrefs.append(str(el.get("href") or "").strip())
        for a in el.find_all("a", href=True):
            hrefs.append(str(a.get("href") or "").strip())
    except Exception:
        return []
    # Absolute + de-dupe preserve order
    out: list[str] = []
    seen: set[str] = set()
    for h in hrefs:
        if not h:
            continue
        abs_h = urljoin(base_url or "https://example.com/", h).split("#")[0]
        key = abs_h.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(abs_h)
    return out


def measure_selector_product_density(
    html: str,
    selector: str,
    *,
    base_url: str = "",
) -> dict[str, Any]:
    """
    Measure product-URL density for a CSS selector:

        selector_score = elements_with_valid_product_url / total_matched_elements

    Also reports href quality flags and unique valid product URLs.
    """
    from bs4 import BeautifulSoup

    sel = (selector or "").strip()
    empty = {
        "selector": sel,
        "total_matched_elements": 0,
        "elements_with_valid_product_url": 0,
        "valid_product_urls": [],
        "valid_product_url_count": 0,
        "unique_valid_product_url_count": 0,
        "selector_score": 0.0,
        "confidence": 0,
        "href_stats": {
            "checked": 0,
            "href_exists": 0,
            "not_javascript": 0,
            "not_hash_only": 0,
            "not_mailto": 0,
            "not_tel": 0,
            "not_category_page": 0,
            "not_homepage": 0,
            "looks_like_product": 0,
            "unique": 0,
        },
        "ignored": False,
        "reasons": [],
    }
    if not sel or not html:
        empty["ignored"] = True
        empty["reasons"] = ["empty selector or html"]
        return empty
    if _is_nav_footer_selector(sel):
        empty["ignored"] = True
        empty["reasons"] = ["matches navigation/footer chrome"]
        return empty

    try:
        soup = BeautifulSoup(html or "", "lxml")
        nodes = soup.select(sel)
    except Exception as exc:
        empty["ignored"] = True
        empty["reasons"] = [f"selector query failed: {exc}"]
        return empty

    total = len(nodes)
    if total == 0:
        empty["ignored"] = True
        empty["reasons"] = ["zero matched elements"]
        return empty

    href_stats = empty["href_stats"]
    elements_with_valid = 0
    all_valid: list[str] = []
    seen_valid: set[str] = set()

    for el in nodes:
        hrefs = _element_product_hrefs(el, base_url)
        el_has_valid = False
        for href in hrefs:
            href_stats["checked"] += 1
            flags = _href_validity_flags(href, base_url)
            for k, ok in flags.items():
                if ok:
                    href_stats[k] = int(href_stats.get(k) or 0) + 1
            if _looks_like_product_href(href):
                href_stats["looks_like_product"] = (
                    int(href_stats.get("looks_like_product") or 0) + 1
                )
            if _is_valid_product_href(href, base_url):
                el_has_valid = True
                key = href.rstrip("/").lower()
                if key not in seen_valid:
                    seen_valid.add(key)
                    all_valid.append(href)
                    href_stats["unique"] = int(href_stats.get("unique") or 0) + 1
        if el_has_valid:
            elements_with_valid += 1

    ratio = elements_with_valid / total if total else 0.0
    confidence = int(round(ratio * 100))
    reasons = [
        f"{total} nodes",
        f"{elements_with_valid} elements with valid product URLs",
        f"selector_score={elements_with_valid}/{total}={ratio:.4f}",
        f"{len(all_valid)} unique valid product URLs",
    ]
    return {
        "selector": sel,
        "total_matched_elements": total,
        "elements_with_valid_product_url": elements_with_valid,
        "valid_product_urls": all_valid[:200],
        "valid_product_url_count": elements_with_valid,
        "unique_valid_product_url_count": len(all_valid),
        "selector_score": ratio,
        "confidence": confidence,
        "href_stats": href_stats,
        "ignored": False,
        "reasons": reasons,
        # Back-compat keys used by older YAML consumers
        "matches": total,
        "score": confidence,
    }


def _score_product_selector(
    selector: str,
    matches: int = 0,
    *,
    html: str = "",
    base_url: str = "",
    product_like_anchor_count: int = 0,
    total_anchors: int = 0,
) -> dict[str, Any]:
    """
    Score a candidate product-card selector by product-URL density:

        selector_score = elements_with_valid_product_url / total_matched_elements

    Confidence is that ratio as a percent — NOT raw DOM match count.
    """
    sel = (selector or "").strip()
    if html:
        measured = measure_selector_product_density(html, sel, base_url=base_url)
        if measured.get("ignored") and measured.get("total_matched_elements", 0) == 0:
            # Fall through only when HTML query found nothing but legacy count exists
            if matches <= 0:
                return measured
        else:
            return measured

    # Fallback without HTML: cannot compute density — do not invent match-count scores
    return {
        "selector": sel,
        "total_matched_elements": int(matches or 0),
        "elements_with_valid_product_url": 0,
        "valid_product_urls": [],
        "valid_product_url_count": 0,
        "unique_valid_product_url_count": 0,
        "selector_score": 0.0,
        "confidence": 0,
        "matches": int(matches or 0),
        "score": 0,
        "ignored": True,
        "reasons": [
            "HTML unavailable — cannot score by product-URL density "
            f"(legacy node count={matches})"
        ],
        "href_stats": {},
    }


def _infer_product_url_selectors(analysis: dict[str, Any]) -> list[str]:
    hrefs: list[str] = []
    for item in analysis.get("product_like_anchors") or []:
        if isinstance(item, dict):
            hrefs.append(str(item.get("href") or item.get("raw_href") or ""))
        else:
            hrefs.append(str(item))
    hrefs.extend(str(h) for h in (analysis.get("first_100_anchor_hrefs") or [])[:100])

    counter: dict[str, int] = {}
    for href in hrefs:
        low = href.lower()
        if "/products/" in low:
            key = 'a[href*="/products/"]'
            counter[key] = counter.get(key, 0) + 1
        elif "/product/" in low or "/product-" in low:
            key = 'a[href*="/product"]'
            counter[key] = counter.get(key, 0) + 1
        elif "/shop/" in low:
            key = 'a[href*="/shop/"]'
            counter[key] = counter.get(key, 0) + 1
        elif "/item/" in low:
            key = 'a[href*="/item/"]'
            counter[key] = counter.get(key, 0) + 1
        elif "/p/" in low:
            key = 'a[href*="/p/"]'
            counter[key] = counter.get(key, 0) + 1
    ranked = sorted(counter.items(), key=lambda x: (-x[1], x[0]))
    return [sel for sel, _ in ranked[:5]]


def generate_draft_site_rule(
    analysis: dict[str, Any],
    *,
    selector_counts: dict[str, int] | None = None,
    html: str = "",
    progress: Optional[ProgressCallback] = None,
) -> dict[str, Any]:
    """
    Rank/score selectors by product-URL density against rendered HTML.
    Never writes production YAML.
    """
    counts = dict(selector_counts or {})
    if not counts:
        counts = dict(analysis.get("selector_counts") or {})

    base_url = str(
        analysis.get("final_url")
        or analysis.get("category_url")
        or analysis.get("canonical_url")
        or ""
    )
    # Ensure we evaluate every known candidate selector, even if count was 0
    selectors = list(counts.keys()) or list(PRODUCT_CARD_CANDIDATE_SELECTORS)

    ranked: list[dict[str, Any]] = []
    for sel in selectors:
        ranked.append(
            _score_product_selector(
                sel,
                int(counts.get(sel) or 0),
                html=html,
                base_url=base_url,
            )
        )
    ranked.sort(
        key=lambda r: (
            -float(r.get("selector_score") or 0),
            -int(r.get("elements_with_valid_product_url") or 0),
            -int(r.get("unique_valid_product_url_count") or 0),
        )
    )

    usable = [
        r
        for r in ranked
        if not r.get("ignored")
        and float(r.get("selector_score") or 0) > 0
        and int(r.get("elements_with_valid_product_url") or 0) > 0
    ]
    best = usable[0] if usable else None
    confidence = int(best["confidence"]) if best else 0
    recommended = str(best["selector"]) if best else ""

    # Small boost only when density is already meaningful and structured data agrees
    boost = 0
    if confidence >= 20:
        if analysis.get("__NEXT_DATA__"):
            boost += 2
        if analysis.get("json_ld_scripts"):
            boost += 2
        if int(best.get("unique_valid_product_url_count") or 0) >= 5:
            boost += 3
    if analysis.get("products_appear_only_after_js_rendering") and confidence < 50:
        boost -= 3
    confidence = max(0, min(100, confidence + boost))
    if best is not None:
        best["confidence"] = confidence
        best["score"] = confidence

    review_required = confidence <= 90
    status_message = (
        "Manual review recommended"
        if review_required
        else "Auto rule likely usable"
    )

    product_url_sels = _infer_product_url_selectors(analysis)
    if not product_url_sels and best and best.get("valid_product_urls"):
        product_url_sels = _infer_product_url_selectors(
            {
                "product_like_anchors": [
                    {"href": u} for u in (best.get("valid_product_urls") or [])
                ]
            }
        )
    if not product_url_sels:
        product_url_sels = ['a[href*="/product"]']

    category_url = str(analysis.get("category_url") or analysis.get("final_url") or "")
    host = urlparse(category_url).netloc.lower()
    bare = host[4:] if host.startswith("www.") else host
    hosts = [bare, f"www.{bare}"] if bare else []

    platform = (
        analysis.get("detected_ecommerce_platform")
        or analysis.get("platform")
        or "Custom"
    )

    card_list = [r["selector"] for r in usable[:5]]
    if recommended and recommended not in card_list:
        card_list.insert(0, recommended)

    payload: dict[str, Any] = {
        "suggestions_only": True,
        "confidence": confidence,
        "recommended_selector": recommended,
        "review_required": review_required,
        "status_message": status_message,
        "hosts": hosts,
        "platform_hint": platform,
        "source_category_url": category_url,
        "scoring_method": (
            "selector_score = elements_with_valid_product_url / total_matched_elements"
        ),
        "selectors": {
            "product_card": card_list,
            "product_url": product_url_sels,
            "title": ["h1"],
            "price": [".price", '[itemprop="price"]', '[class*="price"]'],
            "images": [".product-image img", '[class*="product"] img', "img"],
        },
        "ranked_selectors": ranked,
        "rules": {
            "use_playwright": bool(
                analysis.get("products_appear_only_after_js_rendering")
            ),
            "strip_price_from_options": True,
        },
        "notes": [
            status_message,
            "AUTO-GENERATED DRAFT from output/debug/page_analysis.json + selectors.txt.",
            "Scored by valid product-URL density — NOT raw DOM match count.",
            "Do NOT copy blindly into production configs/site_rules/.",
            "Validate recommended_selector against category_debug.html / category.png.",
        ],
    }
    if best:
        _emit(
            progress,
            f"{recommended}: {best.get('total_matched_elements', 0)} nodes, "
            f"{best.get('elements_with_valid_product_url', 0)} valid product URLs, "
            f"confidence {confidence}%",
        )
    _emit(progress, f"Draft rule confidence: {confidence}% — {status_message}")
    if recommended:
        _emit(progress, f"Recommended selector: {recommended}")
    return payload


def write_generated_site_rule_yaml(
    payload: dict[str, Any],
    output_path: Path | None = None,
    progress: Optional[ProgressCallback] = None,
) -> Path:
    """
    Write draft YAML under output/debug/ only.
    Never overwrites production configs/site_rules/*.yaml.
    """
    out = output_path or (CATEGORY_DEBUG_DIR / "generated_site_rule.yaml")
    out = Path(out)
    # Safety: refuse to write into production site_rules directories
    parts_lower = [p.lower() for p in out.parts]
    if "site_rules" in parts_lower and "debug" not in parts_lower:
        raise ValueError(
            f"Refusing to write draft rule into production path: {out}"
        )
    out.parent.mkdir(parents=True, exist_ok=True)

    header = (
        "# AUTO-GENERATED DRAFT — suggestions only.\n"
        "# Source: output/debug/page_analysis.json + selectors.txt\n"
        "# Do NOT overwrite production configs/site_rules/ with this file.\n"
        f"# status: {payload.get('status_message')}\n"
        f"# confidence: {payload.get('confidence')}\n"
        f"# review_required: {payload.get('review_required')}\n"
    )
    try:
        import yaml

        body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    except Exception:
        body = json.dumps(payload, indent=2, ensure_ascii=False, default=str)

    out.write_text(header + body, encoding="utf-8")
    _emit(progress, f"Wrote draft site rule → {out}")
    _emit(progress, str(payload.get("status_message") or ""))
    return out


def generate_site_rule_from_debug_dir(
    debug_dir: Path | None = None,
    progress: Optional[ProgressCallback] = None,
) -> dict[str, Any]:
    """Load page_analysis.json + selectors.txt + HTML and write generated_site_rule.yaml."""
    debug_dir = Path(debug_dir or CATEGORY_DEBUG_DIR)
    analysis_path = debug_dir / "page_analysis.json"
    selectors_path = debug_dir / "selectors.txt"
    html_path = debug_dir / "category_debug.html"

    analysis: dict[str, Any] = {}
    if analysis_path.exists():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        except Exception as exc:
            _emit(progress, f"Could not read page_analysis.json: {exc}")
            analysis = {}

    counts: dict[str, int] = {}
    if selectors_path.exists():
        counts = parse_selectors_txt(selectors_path.read_text(encoding="utf-8"))
    if not counts:
        counts = dict(analysis.get("selector_counts") or {})

    html = ""
    if html_path.exists():
        try:
            html = html_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            _emit(progress, f"Could not read category_debug.html: {exc}")

    payload = generate_draft_site_rule(
        analysis, selector_counts=counts, html=html, progress=progress
    )
    write_generated_site_rule_yaml(
        payload, output_path=debug_dir / "generated_site_rule.yaml", progress=progress
    )
    return payload


def write_category_debug_package(
    *,
    category_url: str,
    html: str,
    selector_counts: dict[str, int],
    screenshot: bytes | None = None,
    progress: Optional[ProgressCallback] = None,
    raw_html: str | None = None,
    platform: str | None = None,
    initial_state: Any = None,
    final_url: str | None = None,
) -> Path:
    """
    Write output/debug/ debug package for YAML rule authoring:
      - category_debug.html  (final rendered HTML)
      - category.png         (full-page screenshot)
      - selectors.txt        (nodes + valid product URL density)
      - page_analysis.json   (rich page signals)
      - generated_site_rule.yaml (draft ranked selectors — not production)
    """
    CATEGORY_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    html_path = CATEGORY_DEBUG_DIR / "category_debug.html"
    png_path = CATEGORY_DEBUG_DIR / "category.png"
    sel_path = CATEGORY_DEBUG_DIR / "selectors.txt"
    analysis_path = CATEGORY_DEBUG_DIR / "page_analysis.json"

    html_path.write_text(html or "", encoding="utf-8", errors="replace")
    if screenshot:
        png_path.write_bytes(screenshot)
    elif not png_path.exists():
        png_path.write_bytes(b"")

    base_url = final_url or category_url
    density_rows: list[dict[str, Any]] = []
    for sel in list(selector_counts.keys()) or list(PRODUCT_CARD_CANDIDATE_SELECTORS):
        measured = measure_selector_product_density(
            html or "", sel, base_url=base_url
        )
        density_rows.append(measured)

    density_rows.sort(
        key=lambda r: (
            -float(r.get("selector_score") or 0),
            -int(r.get("elements_with_valid_product_url") or 0),
        )
    )

    lines = [
        f"Category URL: {category_url}",
        "",
        "Scoring: selector_score = elements_with_valid_product_url / total_matched_elements",
        "Confidence is product-URL density — NOT raw DOM match count.",
        "",
        "Detected candidate selectors:",
        "",
    ]
    for measured in density_rows:
        sel = measured.get("selector") or ""
        nodes = int(measured.get("total_matched_elements") or 0)
        valid = int(measured.get("elements_with_valid_product_url") or 0)
        conf = int(measured.get("confidence") or 0)
        lines.append(f'Selector "{sel}"')
        lines.append(f"{nodes} nodes")
        lines.append(f"{valid} valid product URLs")
        lines.append(f"confidence: {conf}%")
        if measured.get("ignored"):
            lines.append(f"ignored: {', '.join(measured.get('reasons') or [])}")
        lines.append("")
        # Keep logging in GUI/console consistent with file
        print(
            f'\nSelector "{sel}"\n{nodes} nodes\n{valid} valid product URLs\n'
            f"confidence: {conf}%\n",
            flush=True,
        )
        _emit(
            progress,
            f'{sel}: {nodes} nodes, {valid} valid product URLs, confidence {conf}%',
        )

    lines.extend(
        [
            "",
            "Tips for YAML rules:",
            "- Rank by valid-product-URL density, not raw node count.",
            "- Ignore selectors that match nav/footer chrome.",
            "- Prefer selectors where most matched elements contain a valid product href.",
            "- Valid href: exists, not javascript/#/mailto/tel, not category/homepage, unique product URL.",
            "- See page_analysis.json and generated_site_rule.yaml (debug only — not production).",
        ]
    )
    sel_path.write_text("\n".join(lines), encoding="utf-8")

    # Prefer density-derived counts for analysis.selector_counts
    density_counts = {
        str(r.get("selector")): int(r.get("total_matched_elements") or 0)
        for r in density_rows
        if r.get("selector")
    }

    analysis: dict[str, Any] = {}
    try:
        analysis = build_page_analysis(
            category_url=category_url,
            html=html,
            selector_counts=density_counts or selector_counts,
            raw_html=raw_html,
            platform=platform,
            initial_state=initial_state,
            final_url=final_url,
        )
        analysis["selector_density"] = [
            {
                "selector": r.get("selector"),
                "nodes": r.get("total_matched_elements"),
                "valid_product_urls": r.get("elements_with_valid_product_url"),
                "unique_valid_product_urls": r.get("unique_valid_product_url_count"),
                "selector_score": r.get("selector_score"),
                "confidence": r.get("confidence"),
                "href_stats": r.get("href_stats"),
                "ignored": r.get("ignored"),
            }
            for r in density_rows
        ]
        analysis_path.write_text(
            json.dumps(analysis, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        analysis = {
            "category_url": category_url,
            "error": f"page_analysis build failed: {exc}",
            "selector_counts": selector_counts,
            "selector_density": density_rows,
        }
        analysis_path.write_text(
            json.dumps(analysis, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        _emit(progress, f"page_analysis.json partial: {exc}")

    # Draft YAML from analysis + HTML density (never touches production rules)
    try:
        payload = generate_draft_site_rule(
            analysis,
            selector_counts=density_counts or selector_counts,
            html=html or "",
            progress=progress,
        )
        write_generated_site_rule_yaml(payload, progress=progress)
        analysis["draft_rule"] = {
            "confidence": payload.get("confidence"),
            "recommended_selector": payload.get("recommended_selector"),
            "review_required": payload.get("review_required"),
            "status_message": payload.get("status_message"),
            "scoring_method": payload.get("scoring_method"),
            "path": "generated_site_rule.yaml",
        }

        # Extract product URLs from the recommended card selector
        recommended = str(payload.get("recommended_selector") or "")
        if recommended and html:
            extraction = extract_product_urls_for_selector(
                html,
                recommended,
                base_url=final_url or category_url,
                progress=progress,
                write_debug=True,
            )
            analysis["product_url_extraction"] = {
                "selector": recommended,
                "matched_nodes": extraction.get("matched_nodes"),
                "nodes_with_valid_href": extraction.get("nodes_with_valid_href"),
                "extracted_count": len(extraction.get("extracted_product_urls") or []),
                "rejected_count": len(extraction.get("rejected_urls") or []),
                "sample_urls": extraction.get("sample_urls") or [],
                "success": extraction.get("success"),
            }
            if not extraction.get("success"):
                _emit(
                    progress,
                    "Product URL extraction failed — see output/debug/product_urls.txt "
                    "and first_product_node.html",
                )

        analysis_path.write_text(
            json.dumps(analysis, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        _emit(progress, f"generated_site_rule.yaml failed: {exc}")

    _emit(progress, f"Debug package written → {CATEGORY_DEBUG_DIR}")
    _emit(
        progress,
        f"  {html_path.name}, {png_path.name}, {sel_path.name}, "
        f"{analysis_path.name}, generated_site_rule.yaml, product_urls.txt",
    )
    return CATEGORY_DEBUG_DIR


def probe_category_product_cards(
    category_url: str,
    progress: Optional[ProgressCallback] = None,
    *,
    write_debug_if_empty: bool = True,
) -> dict[str, Any]:
    """
    Visit one category page, count candidate product-card selectors.
    If zero cards across all candidates, write the debug package.
    """
    result: dict[str, Any] = {
        "url": category_url,
        "counts": {},
        "total_matches": 0,
        "debug_written": False,
        "status": "ok",
    }
    url = (category_url or "").strip()
    if not url.startswith(("http://", "https://")):
        result["status"] = "invalid_url"
        return result

    html = ""
    raw_html = ""
    screenshot: bytes | None = None
    counts: dict[str, int] = {}
    initial_state: Any = None
    final_url = url
    platform: str | None = None

    # Static fetch for JS-vs-raw comparison
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=20,
            allow_redirects=True,
        )
        raw_html = resp.text if resp.ok else ""
    except Exception:
        raw_html = ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    page.goto(url, wait_until="load", timeout=30000)
                page.wait_for_timeout(1000)
                try:
                    final_url = page.url or url
                except Exception:
                    final_url = url
                html = page.content() or ""
                counts = _count_card_selectors_on_page(page)
                try:
                    initial_state = page.evaluate(
                        """() => {
                            try {
                                if (typeof window.__INITIAL_STATE__ !== 'undefined')
                                    return window.__INITIAL_STATE__;
                                if (typeof window.__PRELOADED_STATE__ !== 'undefined')
                                    return window.__PRELOADED_STATE__;
                                if (typeof window.__INITIAL_DATA__ !== 'undefined')
                                    return window.__INITIAL_DATA__;
                            } catch (e) {}
                            return null;
                        }"""
                    )
                except Exception:
                    initial_state = None
                try:
                    screenshot = page.screenshot(full_page=True, type="png")
                except Exception:
                    screenshot = None
            finally:
                browser.close()
    except Exception as exc:
        _emit(progress, f"Playwright probe failed ({exc}); falling back to HTTP")
        html = raw_html
        counts = _count_card_selectors_in_html(html)

    platform = _detect_platform_from_html(html or raw_html, url)
    total = sum(int(v or 0) for v in counts.values())
    result["counts"] = counts
    result["total_matches"] = total
    _log_selector_probe(url, counts, progress=progress)

    if total == 0 and write_debug_if_empty:
        write_category_debug_package(
            category_url=url,
            html=html,
            selector_counts=counts,
            screenshot=screenshot,
            progress=progress,
            raw_html=raw_html or None,
            platform=platform,
            initial_state=initial_state,
            final_url=final_url,
        )
        result["debug_written"] = True
        result["status"] = "Needs YAML Rules"
        _emit(progress, "Needs YAML Rules — zero product cards on probed category")

    return result


def discover_category_links(
    url: str,
    progress: Optional[ProgressCallback] = None,
    *,
    probe_first: bool = True,
) -> list[dict[str, str]]:
    """
    Extract product-category links from homepage nav (not informational pages).

    Filters out delivery/returns/samples/CMS paths etc. Optionally probes the
    first retained category for product cards and writes a debug package when
    zero cards are found.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return []
    parsed = urlparse(url)
    if not parsed.netloc:
        return []
    base = f"{parsed.scheme}://{parsed.netloc}"

    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=15,
            allow_redirects=True,
        )
        html = resp.text if resp.ok else ""
    except Exception:
        return []

    # Prefer BeautifulSoup if available (already a project dependency)
    # Intentionally exclude footer — informational links cluster there.
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        anchors = soup.select(
            "nav a[href], header a[href], .menu a[href], .navigation a[href], "
            "#menu a[href], .navbar a[href], .main-nav a[href], "
            ".main-menu a[href], .primary-nav a[href], "
            "[role='navigation'] a[href], .category-menu a[href], "
            ".sidebar a[href], aside a[href]"
        )
        if not anchors:
            anchors = soup.select("main a[href], #content a[href], a[href]")
        raw_links = []
        for a in anchors:
            href = a.get("href") or ""
            label = a.get_text(" ", strip=True) or ""
            raw_links.append((label, href))
    except Exception:
        raw_links = []
        for m in re.finditer(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            html,
            re.I | re.S,
        ):
            href, inner = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
            raw_links.append((re.sub(r"\s+", " ", inner).strip(), href))

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for label, href in raw_links:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base + "/", href).split("#")[0]
        if urlparse(full).netloc.lower() != parsed.netloc.lower():
            continue
        path = (urlparse(full).path or "/").rstrip("/") or "/"
        if path == "/":
            continue
        label_clean = re.sub(r"\s+", " ", (label or "").strip())
        if not label_clean or len(label_clean) > 80:
            label_clean = path.rstrip("/").split("/")[-1].replace("-", " ").title()
        if _is_informational_category(label_clean, path):
            continue
        # Prefer category-like depth (1–3 segments)
        depth = len([p for p in path.split("/") if p])
        if depth > 4:
            continue
        # Skip pagination URLs
        if re.search(r"/page/\d+/?$", path, re.I):
            continue
        if re.search(r"(?:^|&)page=\d+", urlparse(full).query or "", re.I):
            continue
        # Skip obvious file downloads
        if re.search(r"\.(pdf|docx?|xlsx?|zip|rar|csv|png|jpe?g|gif|webp)$", path, re.I):
            continue
        key = full.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        results.append({"label": label_clean, "url": full})

    # Prefer URLs that look like real category/catalog paths when available
    hinted = [r for r in results if _looks_like_category_url(urlparse(r["url"]).path)]
    if hinted:
        results = hinted

    results.sort(
        key=lambda x: (
            0 if _looks_like_category_url(urlparse(x["url"]).path) else 1,
            len(urlparse(x["url"]).path.split("/")),
            x["label"].lower(),
        )
    )
    results = results[:40]

    if probe_first and results:
        first = results[0]
        _emit(progress, f"Probing first category for product cards: {first['url']}")
        try:
            # Fast HTTP probe first; escalate to Playwright debug only if empty
            resp = requests.get(
                first["url"],
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=20,
                allow_redirects=True,
            )
            probe_html = resp.text if resp.ok else ""
            counts = _count_card_selectors_in_html(probe_html)
            _log_selector_probe(first["url"], counts, progress=progress)
            if sum(int(v or 0) for v in counts.values()) == 0:
                probe_category_product_cards(
                    first["url"], progress=progress, write_debug_if_empty=True
                )
        except Exception as exc:
            _emit(progress, f"Category probe skipped: {exc}")

    return results


def _normalize_extracted_href(href: str, base_url: str = "") -> str:
    raw = (href or "").strip()
    if not raw:
        return ""
    abs_url = urljoin(base_url or "https://example.com/", raw)
    return abs_url.split("#")[0].strip()


def _parse_onclick_locations(onclick: str) -> list[str]:
    """Extract URLs from onclick handlers (location=, href=, window.open)."""
    if not onclick:
        return []
    found: list[str] = []
    patterns = (
        r"location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]",
        r"window\.location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]",
        r"document\.location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]",
        r"window\.open\s*\(\s*['\"]([^'\"]+)['\"]",
        r"href\s*\(\s*['\"]([^'\"]+)['\"]",
        r"navigate\s*\(\s*['\"]([^'\"]+)['\"]",
    )
    for pat in patterns:
        for m in re.finditer(pat, onclick, re.I):
            val = (m.group(1) or "").strip()
            if val:
                found.append(val)
    return found


def _iter_descendants_limited(el, max_depth: int = 5):
    """Yield (element, depth) for el and descendants up to max_depth levels."""
    stack: list[tuple[Any, int]] = [(el, 0)]
    while stack:
        node, depth = stack.pop()
        yield node, depth
        if depth >= max_depth:
            continue
        try:
            children = list(getattr(node, "children", []) or [])
        except Exception:
            children = []
        for child in reversed(children):
            name = getattr(child, "name", None)
            if not name:
                continue
            stack.append((child, depth + 1))


def extract_candidate_hrefs_from_node(
    el,
    base_url: str = "",
    *,
    max_depth: int = 5,
    deep: bool = False,
) -> list[dict[str, str]]:
    """
    Try URL extraction methods in order on a product-card node:
      1. a[href] (node itself)
      2. nested a[href]
      3. data-href
      4. data-url
      5. onclick location
      6. JS event / framework target attributes

    When deep=True (or first pass finds nothing), walk children up to max_depth.
    Returns list of {href, method, raw}.
    """
    results: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(raw: str, method: str) -> None:
        raw = (raw or "").strip()
        if not raw:
            return
        full = _normalize_extracted_href(raw, base_url)
        key = full.rstrip("/").lower() if full else raw.lower()
        if key in seen:
            return
        seen.add(key)
        results.append({"href": full or raw, "method": method, "raw": raw})

    depth_limit = max_depth if deep else 1

    # Pass order matters for logging, but we collect all candidates then validate.
    # 1) node itself if <a href>
    try:
        if getattr(el, "name", None) == "a" and el.get("href"):
            _add(str(el.get("href") or ""), "a[href]")
    except Exception:
        pass

    # Walk node + descendants
    for node, depth in _iter_descendants_limited(el, max_depth=depth_limit):
        if node is el and depth == 0:
            # already handled self a[href]; still check attrs below
            pass
        try:
            name = getattr(node, "name", None)
            # 2) nested a[href]
            if name == "a" and node is not el and node.get("href"):
                _add(str(node.get("href") or ""), "nested a[href]")
            elif name == "a" and node is el and node.get("href"):
                pass  # already added

            # 3) data-href
            if node.get("data-href"):
                _add(str(node.get("data-href") or ""), "data-href")
            # 4) data-url
            if node.get("data-url"):
                _add(str(node.get("data-url") or ""), "data-url")

            # 5) onclick location
            onclick = node.get("onclick") or ""
            if onclick:
                for loc in _parse_onclick_locations(str(onclick)):
                    _add(loc, "onclick location")

            # 6) JS event / framework targets
            for attr, method in (
                ("data-product-url", "JS event targets"),
                ("data-product-href", "JS event targets"),
                ("data-link", "JS event targets"),
                ("data-path", "JS event targets"),
                ("data-navigate", "JS event targets"),
                ("ng-href", "JS event targets"),
                ("v-bind:href", "JS event targets"),
                (":href", "JS event targets"),
                ("routerlink", "JS event targets"),
                ("data-router", "JS event targets"),
                ("formaction", "JS event targets"),
            ):
                if node.get(attr):
                    _add(str(node.get(attr) or ""), method)
        except Exception:
            continue

    # If nothing found on shallow pass, automatically deep-inspect to 5 levels
    if not results and not deep:
        return extract_candidate_hrefs_from_node(
            el, base_url, max_depth=max_depth, deep=True
        )
    return results


def extract_product_urls_for_selector(
    html: str,
    selector: str,
    *,
    base_url: str = "",
    progress: Optional[ProgressCallback] = None,
    write_debug: bool = True,
) -> dict[str, Any]:
    """
    After choosing a product_card selector, inspect every matched node and
    extract product URLs with ordered fallback methods.
    """
    from bs4 import BeautifulSoup

    sel = (selector or "").strip()
    result: dict[str, Any] = {
        "selector": sel,
        "matched_nodes": 0,
        "nodes_with_valid_href": 0,
        "extracted_product_urls": [],
        "rejected_urls": [],
        "sample_urls": [],
        "per_node": [],
        "success": False,
        "first_node_html": "",
    }
    if not sel or not html:
        result["error"] = "missing selector or html"
        return result

    try:
        soup = BeautifulSoup(html or "", "lxml")
        nodes = soup.select(sel)
    except Exception as exc:
        result["error"] = f"selector query failed: {exc}"
        return result

    result["matched_nodes"] = len(nodes)
    if nodes:
        try:
            result["first_node_html"] = str(nodes[0])
        except Exception:
            result["first_node_html"] = ""

    accepted: list[str] = []
    accepted_seen: set[str] = set()
    rejected: list[dict[str, str]] = []
    nodes_with_valid = 0

    for idx, el in enumerate(nodes):
        candidates = extract_candidate_hrefs_from_node(
            el, base_url, max_depth=5, deep=False
        )
        # If still empty, force deep walk
        if not candidates:
            candidates = extract_candidate_hrefs_from_node(
                el, base_url, max_depth=5, deep=True
            )

        node_valid: list[str] = []
        for cand in candidates:
            href = cand.get("href") or ""
            raw = cand.get("raw") or href
            method = cand.get("method") or ""
            if _is_valid_product_href(href, base_url) or _is_valid_product_href(
                raw, base_url
            ):
                full = _normalize_extracted_href(href or raw, base_url)
                key = full.rstrip("/").lower()
                node_valid.append(full)
                if key not in accepted_seen:
                    accepted_seen.add(key)
                    accepted.append(full)
            else:
                rejected.append(
                    {
                        "href": href or raw,
                        "method": method,
                        "reason": "failed product-url validation",
                        "node_index": str(idx),
                    }
                )

        if node_valid:
            nodes_with_valid += 1
        result["per_node"].append(
            {
                "index": idx,
                "candidates": len(candidates),
                "valid_urls": node_valid,
            }
        )

    result["nodes_with_valid_href"] = nodes_with_valid
    result["extracted_product_urls"] = accepted
    result["rejected_urls"] = rejected
    result["sample_urls"] = accepted[:10]
    result["success"] = len(accepted) > 0

    # Required log format
    lines = [
        "",
        "Selector:",
        sel,
        "",
        "Matched nodes:",
        str(len(nodes)),
        "",
        "Nodes with valid href:",
        str(nodes_with_valid),
        "",
        "Extracted product URLs:",
        str(len(accepted)),
        "",
        "Rejected URLs:",
        str(len(rejected)),
        "",
        "Sample URLs:",
    ]
    for u in accepted[:10]:
        lines.append(u)
    if not accepted:
        lines.append("(none)")
    text = "\n".join(lines) + "\n"
    print(text, flush=True)
    for line in lines:
        if line:
            _emit(progress, line)

    if write_debug:
        write_product_url_extraction_debug(result, progress=progress)

    return result


def write_product_url_extraction_debug(
    extraction: dict[str, Any],
    progress: Optional[ProgressCallback] = None,
) -> Path:
    """Write product_urls.txt and first_product_node.html when needed."""
    CATEGORY_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    urls_path = CATEGORY_DEBUG_DIR / "product_urls.txt"
    node_path = CATEGORY_DEBUG_DIR / "first_product_node.html"

    urls = list(extraction.get("extracted_product_urls") or [])
    rejected = list(extraction.get("rejected_urls") or [])
    sample = list(extraction.get("sample_urls") or urls[:10])

    body_lines = [
        f"Selector: {extraction.get('selector') or ''}",
        f"Matched nodes: {extraction.get('matched_nodes') or 0}",
        f"Nodes with valid href: {extraction.get('nodes_with_valid_href') or 0}",
        f"Extracted product URLs: {len(urls)}",
        f"Rejected URLs: {len(rejected)}",
        "",
        "Sample URLs:",
        *(sample or ["(none)"]),
        "",
        "All extracted product URLs:",
    ]
    if urls:
        body_lines.extend(urls)
    else:
        body_lines.append("(none)")
    if rejected:
        body_lines.extend(["", "Rejected URLs:"])
        for item in rejected[:100]:
            if isinstance(item, dict):
                body_lines.append(
                    f"- {item.get('href') or ''} [{item.get('method') or ''}] "
                    f"({item.get('reason') or ''})"
                )
            else:
                body_lines.append(f"- {item}")
    urls_path.write_text("\n".join(body_lines) + "\n", encoding="utf-8")
    _emit(progress, f"Wrote {urls_path}")

    if not urls:
        html_node = str(extraction.get("first_node_html") or "")
        node_path.write_text(html_node, encoding="utf-8", errors="replace")
        _emit(progress, f"Zero product URLs — wrote {node_path}")
    elif node_path.exists():
        # Clear stale debug node when extraction succeeds
        try:
            node_path.unlink()
        except Exception:
            pass

    return urls_path


def extract_product_urls_from_best_card_selector(
    html: str,
    *,
    base_url: str = "",
    preferred_selector: str = "",
    selector_counts: dict[str, int] | None = None,
    progress: Optional[ProgressCallback] = None,
) -> dict[str, Any]:
    """
    Pick best product_card selector (or use preferred), then extract product URLs.
    Logs stats for every candidate selector that has matched nodes.
    """
    ranked = []
    selectors = list((selector_counts or {}).keys()) or list(
        PRODUCT_CARD_CANDIDATE_SELECTORS
    )
    if preferred_selector and preferred_selector not in selectors:
        selectors.insert(0, preferred_selector)

    extractions: dict[str, dict[str, Any]] = {}
    for sel in selectors:
        ranked.append(_score_product_selector(sel, html=html, base_url=base_url))
        measured = ranked[-1]
        if int(measured.get("total_matched_elements") or 0) > 0:
            extractions[sel] = extract_product_urls_for_selector(
                html,
                sel,
                base_url=base_url,
                progress=progress,
                write_debug=False,
            )

    ranked.sort(
        key=lambda r: (
            -float(r.get("selector_score") or 0),
            -int(r.get("elements_with_valid_product_url") or 0),
        )
    )
    best_sel = (preferred_selector or "").strip()
    if not best_sel:
        # Prefer selectors that both score well AND yield extracted URLs
        for r in ranked:
            sel = str(r.get("selector") or "")
            if r.get("ignored"):
                continue
            ext = extractions.get(sel) or {}
            if ext.get("success") and (ext.get("extracted_product_urls") or []):
                best_sel = sel
                break
        if not best_sel:
            usable = [
                r
                for r in ranked
                if not r.get("ignored")
                and int(r.get("elements_with_valid_product_url") or 0) > 0
            ]
            if usable:
                best_sel = str(usable[0].get("selector") or "")
            elif ranked:
                best_sel = str(ranked[0].get("selector") or "")

    if best_sel and best_sel not in extractions:
        extractions[best_sel] = extract_product_urls_for_selector(
            html,
            best_sel,
            base_url=base_url,
            progress=progress,
            write_debug=False,
        )

    extraction = dict(
        extractions.get(best_sel)
        or {
            "selector": best_sel,
            "matched_nodes": 0,
            "nodes_with_valid_href": 0,
            "extracted_product_urls": [],
            "rejected_urls": [],
            "sample_urls": [],
            "success": False,
            "first_node_html": "",
        }
    )
    extraction["ranked_selectors"] = ranked
    extraction["recommended_selector"] = best_sel
    write_product_url_extraction_debug(extraction, progress=progress)
    return extraction


def _product_links_on_page(page, base_url: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for sel in PRODUCT_LINK_SELECTORS:
        try:
            for link in page.query_selector_all(sel):
                href = link.get_attribute("href") or ""
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                full = urljoin(base_url, href).split("#")[0].split("?")[0]
                # Skip pure category/nav noise when possible
                path = urlparse(full).path.lower()
                if path in ("/", "") or path.endswith(("/cart", "/checkout", "/account")):
                    continue
                if full not in seen:
                    seen.add(full)
                    found.append(full)
        except Exception:
            continue
    return found


def _find_next_url(page, base_url: str, current_url: str) -> str | None:
    """
    Resolve next listing page. Supports:
    - rel/aria/class next links
    - visible 'Next' text links
    - explicit ?page=N / /page/N/ links (pick current+1)
    - synthesized ?page=current+1 fallback
    """
    current_n = _current_page_number(current_url)
    candidates: list[tuple[int, str]] = []

    # 1) Explicit next selectors
    for sel in NEXT_LINK_SELECTORS:
        try:
            for link in page.query_selector_all(sel):
                href = link.get_attribute("href") or ""
                if not href or href.startswith(("#", "javascript:")):
                    continue
                full = urljoin(base_url, href).split("#")[0]
                if _listing_key(full) == _listing_key(current_url):
                    continue
                n = _current_page_number(full)
                if n > current_n or "page=" in full.lower() or "/page/" in full.lower():
                    return full
                # text Next without page num — still follow
                try:
                    text = (link.inner_text() or "").strip().lower()
                except Exception:
                    text = ""
                if text in ("next", "›", "»", ">", "older"):
                    return full
        except Exception:
            continue

    # 2) Any anchor whose text is Next / › / »
    try:
        for link in page.query_selector_all("a[href]"):
            try:
                text = (link.inner_text() or "").strip().lower()
            except Exception:
                continue
            if text not in ("next", "›", "»", ">", "older", "next page"):
                continue
            href = link.get_attribute("href") or ""
            if not href or href.startswith(("#", "javascript:")):
                continue
            full = urljoin(base_url, href).split("#")[0]
            if _listing_key(full) != _listing_key(current_url):
                return full
    except Exception:
        pass

    # 3) Collect numeric page links (?page= / /page/)
    try:
        for link in page.query_selector_all("a[href*='page']"):
            href = link.get_attribute("href") or ""
            if not href:
                continue
            full = urljoin(base_url, href).split("#")[0]
            if "page=" not in full.lower() and not re.search(r"/page/\d+", full, re.I):
                continue
            n = _current_page_number(full)
            if n > current_n:
                candidates.append((n, full))
    except Exception:
        pass

    if candidates:
        candidates.sort(key=lambda x: x[0])
        # Prefer exact current+1
        for n, full in candidates:
            if n == current_n + 1:
                return full
        return candidates[0][1]

    # 4) Synthesize ?page=N+1 — caller validates by product yield
    return _with_page_number(current_url, current_n + 1)


def _extract_from_card(link_el, base_url: str) -> dict[str, str]:
    """Best-effort title/price/image from a product card around the link."""
    title = ""
    price = ""
    image = ""
    try:
        title = (link_el.inner_text() or "").strip()
        title = re.sub(r"\s+", " ", title)[:200]
    except Exception:
        title = ""

    try:
        data = link_el.evaluate(
            """(el, base) => {
                const card = el.closest(
                    'li, article, .product, .product-card, .product-item, '
                    + '.card, .grid__item, [class*="product"]'
                ) || el.parentElement;
                if (!card) return { title: '', price: '', image: '' };
                const titleEl = card.querySelector(
                    'h1, h2, [class*="product-title"], [class*="product-name"], [class*="product__title"]'
                );
                const priceEl = card.querySelector(
                    '[class*="price"], [class*="Price"], span.amount, [class*="product-price"]'
                );
                const imgEl = card.querySelector(
                    '.product-image img, [class*="product"] img, article img, li img, img'
                );
                let image = '';
                if (imgEl) {
                    image = imgEl.getAttribute('src')
                        || imgEl.getAttribute('data-src')
                        || imgEl.getAttribute('data-lazy-src')
                        || '';
                    if (!image && imgEl.getAttribute('srcset')) {
                        image = imgEl.getAttribute('srcset').split(',')[0].trim().split(' ')[0];
                    }
                    if (image && image.startsWith('/')) {
                        try { image = new URL(image, base).href; } catch (e) {}
                    } else if (image && image.startsWith('//')) {
                        image = 'https:' + image;
                    }
                }
                return {
                    title: titleEl ? (titleEl.innerText || '').trim() : '',
                    price: priceEl ? (priceEl.innerText || '').trim() : '',
                    image: image && !image.startsWith('data:') ? image : '',
                };
            }""",
            base_url,
        )
        if isinstance(data, dict):
            if data.get("title"):
                title = re.sub(r"\s+", " ", str(data["title"]))[:200]
            if data.get("price"):
                price = _normalize_price(str(data["price"]))
            if data.get("image"):
                image = str(data["image"])
    except Exception:
        pass

    return {"title": title, "price": price, "image": image}


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _handle_from_url(product_url: str, title: str = "") -> str:
    """URL handle from slugified product URL path (preferred) or title."""
    path = urlparse(product_url).path.rstrip("/")
    slug = path.split("/")[-1] if path else ""
    handle = slugify(slug) or slugify(title) or "product"
    return handle


def _parse_price_pair(price_text: str) -> tuple[str, str]:
    """
    From noisy price block text return (price, compare_at).
    If two amounts, treat higher as compare-at and lower as sale price.
    """
    if not price_text:
        return "", ""
    cleaned = re.sub(r"\s+", " ", price_text).strip()
    amounts = re.findall(r"\d[\d,]*\.?\d*", cleaned.replace(",", ""))
    nums: list[float] = []
    for a in amounts:
        try:
            nums.append(float(a.replace(",", "")))
        except ValueError:
            continue
    if not nums:
        return "", ""
    if len(nums) == 1:
        return f"{nums[0]:.2f}", ""
    low, high = min(nums), max(nums)
    if high > low:
        return f"{low:.2f}", f"{high:.2f}"
    return f"{low:.2f}", ""


def _extract_from_pdp(page, base_url: str, product_url: str) -> dict[str, Any]:
    """Rich PDP extraction aligned to DRAFT_HEADERS."""
    title = _extract_title_h1(page)
    if not (title or "").strip():
        return {
            "skip": True,
            "skip_reason": "no h1",
            "title": "",
            "product_url": product_url,
        }
    if _is_bad_title(title):
        return {
            "skip": True,
            "skip_reason": "bad title",
            "title": title,
            "product_url": product_url,
        }

    price, compare_at = _extract_price_from_page(page)
    image = _first_image(page, IMAGE_SELECTORS, base_url)

    try:
        description = _extract_description(page)
    except Exception:
        description = ""

    # Meta fields
    def _meta_text(selectors: list[str]) -> str:
        for sel in selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    t = re.sub(r"\s+", " ", (el.inner_text() or "").strip())
                    t = re.sub(
                        r"^(sku|brand|vendor|barcode|upc|ean|type|category)\s*[:#-]?\s*",
                        "",
                        t,
                        flags=re.I,
                    ).strip()
                    if t:
                        return t[:200]
            except Exception:
                continue
        return ""

    sku = _meta_text(
        [
            '[class*="sku"]',
            '[itemprop="sku"]',
            ".product-sku",
            "span.sku",
            ".sku_wrapper",
        ]
    )
    vendor = _meta_text(
        [
            '[class*="vendor"]',
            '[class*="brand"]',
            '[itemprop="brand"]',
            ".product-vendor",
            ".woocommerce-product-attributes-item--attribute_pa_brand",
        ]
    )
    product_type = _meta_text(
        [
            '[class*="product-type"]',
            ".product_meta .posted_in",
            '[itemprop="category"]',
        ]
    )
    barcode = _meta_text(
        [
            '[class*="barcode"]',
            '[class*="gtin"]',
            '[class*="upc"]',
            '[class*="ean"]',
            '[itemprop="gtin"]',
            '[itemprop="gtin13"]',
        ]
    )
    tags = _meta_text(
        [
            ".tagged_as",
            '[class*="product-tags"]',
            ".product_meta .tagged_as",
        ]
    )

    options = _extract_variant_options(page)

    return {
        "skip": False,
        "title": title,
        "price": price,
        "compare_at_price": compare_at,
        "image": image,
        "description": description,
        "sku": sku,
        "vendor": vendor,
        "type": product_type,
        "barcode": barcode,
        "tags": tags,
        "options": options,
        "product_url": product_url,
    }


def _extract_variant_options(page) -> list[dict[str, Any]]:
    """Detect up to 3 option axes (name + values) from selects/swatches."""
    options: list[dict[str, Any]] = []

    # <select> variant pickers
    try:
        for sel in page.query_selector_all(
            "form select, .variations select, select[name*='attribute'], "
            "select[id*='option'], select[name*='option']"
        ):
            if len(options) >= 3:
                break
            try:
                if not sel.is_visible():
                    continue
            except Exception:
                continue
            name = (
                sel.get_attribute("name")
                or sel.get_attribute("aria-label")
                or sel.get_attribute("id")
                or ""
            )
            name = re.sub(r"attribute_pa_|attribute_|option_?", "", name, flags=re.I)
            name = name.replace("-", " ").replace("_", " ").strip().title() or "Option"
            # Label nearby
            try:
                label = sel.evaluate(
                    """el => {
                        const id = el.getAttribute('id');
                        if (id) {
                          const lab = document.querySelector('label[for=\"'+id+'\"]');
                          if (lab) return (lab.innerText||'').trim();
                        }
                        const prev = el.closest('tr, .form-group, .selector-wrapper, div');
                        if (prev) {
                          const l = prev.querySelector('label, th, .label');
                          if (l) return (l.innerText||'').trim();
                        }
                        return '';
                    }"""
                )
                if label:
                    name = re.sub(r"\s+", " ", str(label)).strip().title()[:40]
            except Exception:
                pass
            values: list[str] = []
            try:
                for opt in sel.query_selector_all("option"):
                    val = (opt.inner_text() or opt.get_attribute("value") or "").strip()
                    val = _strip_price_from_option(val)
                    if not val or val.lower().startswith("choose") or val.lower() in (
                        "select",
                        "-",
                        "",
                    ):
                        continue
                    if val not in values:
                        values.append(val[:120])
            except Exception:
                continue
            if len(values) >= 1:
                options.append({"name": name, "values": values[:30]})
    except Exception:
        pass

    # Swatch / button groups
    if len(options) < 3:
        try:
            for group in page.query_selector_all(
                '[class*="swatch"], [class*="variant"], [role="radiogroup"], '
                ".product-form__input, .js-enabled fieldset"
            ):
                if len(options) >= 3:
                    break
                try:
                    if not group.is_visible():
                        continue
                except Exception:
                    continue
                try:
                    legend = group.query_selector(
                        "legend, label, .form__label, .label"
                    )
                    name = ""
                    if legend:
                        name = re.sub(r"\s+", " ", (legend.inner_text() or "").strip())
                    name = name.title()[:40] or "Option"
                    values = []
                    for btn in group.query_selector_all(
                        "button, [role='radio'], input[type='radio'] + label, "
                        "a[class*='swatch'], span[class*='swatch']"
                    ):
                        v = (btn.inner_text() or btn.get_attribute("value") or "").strip()
                        v = _strip_price_from_option(v)
                        if v and v not in values and len(v) < 80:
                            values.append(v)
                    if len(values) >= 2:
                        options.append({"name": name, "values": values[:30]})
                except Exception:
                    continue
        except Exception:
            pass

    return options[:3]


def _cartesian_variants(options: list[dict[str, Any]]) -> list[list[str]]:
    """Return list of value tuples for option combinations (max 3 axes)."""
    if not options:
        return [[]]
    axes = [opt.get("values") or ["Default Title"] for opt in options[:3]]
    combos: list[list[str]] = [[]]
    for axis in axes:
        combos = [prev + [v] for prev in combos for v in axis]
    # Cap explosion
    return combos[:100]


def _detail_to_drafts(
    detail: dict[str, Any],
    category_name: str = "",
) -> list[dict[str, str]]:
    """
    Convert PDP detail dict into one or more DRAFT_HEADERS rows
    (one row per variant combination).
    """
    title = (detail.get("title") or "").strip()
    product_url = detail.get("product_url") or ""
    handle = _handle_from_url(product_url, title)
    options = detail.get("options") or []
    names = [(o.get("name") or f"Option{i+1}") for i, o in enumerate(options[:3])]
    while len(names) < 3:
        names.append("")

    combos = _cartesian_variants(options)
    if not combos or combos == [[]]:
        combos = [["Default Title"]]
        names = ["Title", "", ""]

    drafts: list[dict[str, str]] = []
    for idx, values in enumerate(combos):
        vals = list(values) + ["", "", ""]
        vals = [_strip_price_from_option(v) for v in vals[:3]]
        # pad back to 3
        while len(vals) < 3:
            vals.append("")
        vals = vals[:3]
        is_first = idx == 0
        # Default single-variant naming
        opt_names = list(names)
        if len(combos) == 1 and vals[0] == "Default Title" and not options:
            opt_names = ["Title", "", ""]

        draft = {
            "title": title if is_first else "",
            "url_handle": handle,
            "description": (detail.get("description") or "") if is_first else "",
            "vendor": (detail.get("vendor") or "") if is_first else "",
            "product_category": category_name if is_first else "",
            "type": (detail.get("type") or "") if is_first else "",
            "tags": (detail.get("tags") or "") if is_first else "",
            "sku": detail.get("sku") or "",
            "barcode": (detail.get("barcode") or "") if is_first else "",
            "option1_name": opt_names[0] if is_first else "",
            "option1_value": vals[0] if opt_names[0] or vals[0] else "",
            "option2_name": opt_names[1] if is_first else "",
            "option2_value": vals[1] if opt_names[1] or vals[1] else "",
            "option3_name": opt_names[2] if is_first else "",
            "option3_value": vals[2] if opt_names[2] or vals[2] else "",
            "price": detail.get("price") or "",
            "compare_at_price": detail.get("compare_at_price") or "",
            "inventory_quantity": "",
            "product_image_url": (detail.get("image") or "") if is_first else "",
            "image_position": "1" if (is_first and detail.get("image")) else "",
            "status": "active",
        }
        # Clear empty option names when no value
        for i in (1, 2, 3):
            if not draft[f"option{i}_value"]:
                draft[f"option{i}_name"] = ""
        drafts.append(draft)
    return drafts


def _to_draft(
    title: str,
    price: str,
    image: str,
    product_url: str,
    category_name: str = "",
    compare_at: str = "",
    description: str = "",
    vendor: str = "",
    product_type: str = "",
    tags: str = "",
    sku: str = "",
    barcode: str = "",
) -> dict[str, str]:
    """Minimal fallback draft (listing-only, no PDP)."""
    handle = _handle_from_url(product_url, title)
    return {
        "title": title,
        "url_handle": handle,
        "description": description,
        "vendor": vendor,
        "product_category": category_name,
        "type": product_type,
        "tags": tags,
        "sku": sku,
        "barcode": barcode,
        "option1_name": "Title",
        "option1_value": "Default Title",
        "option2_name": "",
        "option2_value": "",
        "option3_name": "",
        "option3_value": "",
        "price": price,
        "compare_at_price": compare_at,
        "inventory_quantity": "",
        "product_image_url": image,
        "image_position": "1" if image else "",
        "status": "active",
    }


def _new_scrape_stats() -> dict[str, Any]:
    return {
        "urls_found": 0,
        "scraped": 0,
        "skipped_bad_title": 0,
        "skipped_no_h1": 0,
        "skipped_timeout": 0,
        "failed_network": 0,
        "failed_timeout": 0,
        "_found_urls": set(),
    }


def _note_url_found(stats: dict[str, Any], product_url: str) -> None:
    found = stats.setdefault("_found_urls", set())
    if product_url not in found:
        found.add(product_url)
        stats["urls_found"] = int(stats.get("urls_found") or 0) + 1


def _note_skip(stats: dict[str, Any], reason: str, title: str = "") -> None:
    """Classify a skipped product into bad title / no h1 / timeout."""
    low_reason = (reason or "").lower()
    title_stripped = (title or "").strip()
    if "timeout" in low_reason or "timed out" in low_reason:
        stats["skipped_timeout"] = int(stats.get("skipped_timeout") or 0) + 1
    elif not title_stripped or low_reason in {"no h1", "missing h1", "empty h1"}:
        stats["skipped_no_h1"] = int(stats.get("skipped_no_h1") or 0) + 1
    else:
        stats["skipped_bad_title"] = int(stats.get("skipped_bad_title") or 0) + 1


def _note_fail(stats: dict[str, Any], exc: BaseException | str) -> None:
    low = str(exc).lower()
    if "timeout" in low or "timed out" in low:
        stats["failed_timeout"] = int(stats.get("failed_timeout") or 0) + 1
    else:
        stats["failed_network"] = int(stats.get("failed_network") or 0) + 1


def _print_scrape_summary(
    stats: dict[str, Any],
    progress: Optional[ProgressCallback] = None,
) -> None:
    skipped = (
        int(stats.get("skipped_bad_title") or 0)
        + int(stats.get("skipped_no_h1") or 0)
        + int(stats.get("skipped_timeout") or 0)
    )
    failed = (
        int(stats.get("failed_network") or 0)
        + int(stats.get("failed_timeout") or 0)
    )
    lines = [
        "=== Scrape Summary ===",
        f"Total product URLs found: {int(stats.get('urls_found') or 0)}",
        f"Total successfully scraped: {int(stats.get('scraped') or 0)}",
        f"Total skipped: {skipped}",
        f"  - bad title: {int(stats.get('skipped_bad_title') or 0)}",
        f"  - no h1: {int(stats.get('skipped_no_h1') or 0)}",
        f"  - timeout: {int(stats.get('skipped_timeout') or 0)}",
        f"Total failed: {failed}",
        f"  - network error: {int(stats.get('failed_network') or 0)}",
        f"  - timeout: {int(stats.get('failed_timeout') or 0)}",
    ]
    text = "\n".join(lines)
    print(text, flush=True)
    for line in lines:
        _emit(progress, line)


class HtmlCatalogScraper:
    """Scrape non-Shopify catalog / shop pages with Playwright."""

    MAX_PAGES = 25
    MAX_PRODUCTS = 200

    def scrape(
        self,
        url: str,
        progress: Optional[ProgressCallback] = None,
        platform: str = "Custom",
        category_name: str = "",
        categories: Optional[list[dict[str, str]]] = None,
    ) -> dict[str, Any]:
        """
        Scrape one listing URL, or all categories when ``categories`` is set.

        When scraping multiple categories, products are deduplicated by URL handle.
        """
        url = (url or "").strip()
        if categories:
            return self._scrape_all_categories(
                categories, progress=progress, platform=platform
            )

        if not url.startswith(("http://", "https://")):
            raise CollectionCrawlError("URL must start with http:// or https://")

        parsed = urlparse(url)
        if not parsed.netloc:
            raise CollectionCrawlError("Invalid URL.")

        _emit(progress, f"Non-Shopify site detected ({platform}) — using Playwright…")
        stats = _new_scrape_stats()
        drafts = self._scrape_listing(
            url,
            progress=progress,
            category_name=category_name,
            seen_handles=set(),
            seen_product_urls=set(),
            stats=stats,
            write_debug_on_empty=True,
        )
        _print_scrape_summary(stats, progress=progress)

        if not drafts:
            raise CollectionCrawlError(
                "No products found on this page. "
                "See output/debug/ for selector probe (Needs YAML Rules), "
                "or try a category/shop URL with product listings."
            )

        parsed_data = drafts_to_parsed_data(drafts)
        parsed_data["strategy_used"] = f"{platform} HTML (Playwright)"
        parsed_data["platform"] = platform
        parsed_data["errors"] = []
        parsed_data["scrape_stats"] = {
            k: v for k, v in stats.items() if not str(k).startswith("_")
        }
        _emit(progress, f"Done — {parsed_data['row_count']} rows via Playwright")
        return parsed_data

    def _scrape_all_categories(
        self,
        categories: list[dict[str, str]],
        progress: Optional[ProgressCallback] = None,
        platform: str = "Custom",
    ) -> dict[str, Any]:
        _emit(
            progress,
            f"Scraping all {len(categories)} categories (dedupe by URL handle)…",
        )
        drafts: list[dict[str, str]] = []
        seen_handles: set[str] = set()
        seen_product_urls: set[str] = set()
        stats = _new_scrape_stats()
        consecutive_zero = 0
        needs_yaml_rules = False
        stopped_early = False

        for idx, cat in enumerate(categories, start=1):
            cat_url = (cat.get("url") or "").strip()
            cat_name = (cat.get("label") or "").strip()
            if not cat_url:
                continue
            if len(seen_handles) >= self.MAX_PRODUCTS:
                break
            _emit(progress, f"Category {idx}/{len(categories)}: {cat_name}")
            try:
                part = self._scrape_listing(
                    cat_url,
                    progress=progress,
                    category_name=cat_name,
                    seen_handles=seen_handles,
                    seen_product_urls=seen_product_urls,
                    stats=stats,
                    write_debug_on_empty=True,
                )
                if not part:
                    consecutive_zero += 1
                    _emit(
                        progress,
                        f"Zero products in “{cat_name}” "
                        f"({consecutive_zero} consecutive failure(s))",
                    )
                    if consecutive_zero >= 3:
                        needs_yaml_rules = True
                        stopped_early = True
                        _emit(
                            progress,
                            "Stopped after 3 consecutive zero-product categories.",
                        )
                        _emit(progress, "Needs YAML Rules")
                        break
                else:
                    consecutive_zero = 0
                    drafts.extend(part)
            except CollectionCrawlError as exc:
                consecutive_zero += 1
                _emit(progress, f"Skipped category “{cat_name}”: {exc}")
                _note_fail(stats, exc)
                if consecutive_zero >= 3:
                    needs_yaml_rules = True
                    stopped_early = True
                    _emit(
                        progress,
                        "Stopped after 3 consecutive category failures.",
                    )
                    _emit(progress, "Needs YAML Rules")
                    break
            except Exception as exc:
                consecutive_zero += 1
                _emit(progress, f"Skipped category “{cat_name}”: {exc}")
                _note_fail(stats, exc)
                if consecutive_zero >= 3:
                    needs_yaml_rules = True
                    stopped_early = True
                    _emit(
                        progress,
                        "Stopped after 3 consecutive category failures.",
                    )
                    _emit(progress, "Needs YAML Rules")
                    break

        _print_scrape_summary(stats, progress=progress)

        if not drafts:
            msg = "No products found across categories."
            if needs_yaml_rules:
                msg = (
                    "Needs YAML Rules — no products found after repeated "
                    "zero-product category pages. See output/debug/."
                )
            raise CollectionCrawlError(msg)

        parsed_data = drafts_to_parsed_data(drafts)
        parsed_data["strategy_used"] = f"{platform} HTML (Playwright · all categories)"
        parsed_data["platform"] = platform
        parsed_data["errors"] = []
        parsed_data["scrape_stats"] = {
            k: v for k, v in stats.items() if not str(k).startswith("_")
        }
        if needs_yaml_rules:
            parsed_data["status"] = "Needs YAML Rules"
            parsed_data["needs_yaml_rules"] = True
            if stopped_early:
                parsed_data["errors"] = [
                    "Stopped after 3 consecutive zero-product categories — Needs YAML Rules"
                ]
            _emit(progress, "Status: Needs YAML Rules")
        _emit(
            progress,
            f"Done — {parsed_data['row_count']} rows "
            f"({len(seen_handles)} unique products) via Playwright",
        )
        return parsed_data

    def _scrape_listing(
        self,
        url: str,
        *,
        progress: Optional[ProgressCallback],
        category_name: str,
        seen_handles: set[str],
        seen_product_urls: set[str],
        stats: Optional[dict[str, Any]] = None,
        write_debug_on_empty: bool = False,
    ) -> list[dict[str, str]]:
        """Scrape one category/listing URL into DRAFT_HEADERS rows."""
        if stats is None:
            stats = _new_scrape_stats()
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        drafts: list[dict[str, str]] = []
        visited_pages: set[str] = set()
        page_url = url
        pages_visited = 0
        debug_written = False
        best_card_selector: str = ""

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})

            try:
                while page_url and pages_visited < self.MAX_PAGES:
                    pages_visited += 1
                    listing_key = _listing_key(page_url)
                    if listing_key in visited_pages:
                        break
                    visited_pages.add(listing_key)

                    _emit(progress, f"Opening page {pages_visited}: {page_url}")
                    try:
                        page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        try:
                            page.goto(page_url, wait_until="load", timeout=30000)
                        except Exception as exc:
                            raise CollectionCrawlError(
                                f"Unable to open page: {exc}"
                            ) from exc
                    page.wait_for_timeout(1200)

                    html = ""
                    try:
                        html = page.content() or ""
                    except Exception:
                        html = ""

                    # Prefer card-selector URL extraction over generic link scans
                    links: list[str] = []
                    if pages_visited == 1:
                        card_counts = _count_card_selectors_on_page(page)
                        _log_selector_probe(page_url, card_counts, progress=progress)
                        extraction = extract_product_urls_from_best_card_selector(
                            html,
                            base_url=base_url,
                            preferred_selector=best_card_selector,
                            selector_counts=card_counts,
                            progress=progress,
                        )
                        best_card_selector = str(
                            extraction.get("recommended_selector") or ""
                        )
                        links = list(extraction.get("extracted_product_urls") or [])

                        if not links:
                            # Full debug package + first node HTML already written
                            if write_debug_on_empty and not debug_written:
                                try:
                                    try:
                                        shot = page.screenshot(
                                            full_page=True, type="png"
                                        )
                                    except Exception:
                                        shot = None
                                    write_category_debug_package(
                                        category_url=page_url,
                                        html=html,
                                        selector_counts=card_counts,
                                        screenshot=shot,
                                        progress=progress,
                                        platform=_detect_platform_from_html(
                                            html, page_url
                                        ),
                                        final_url=page.url or page_url,
                                    )
                                    debug_written = True
                                except Exception as exc:
                                    _emit(progress, f"Debug package failed: {exc}")
                            raise CollectionCrawlError(
                                "Product URL extraction failed for "
                                f"selector {best_card_selector or '(none)'} — "
                                "see output/debug/product_urls.txt and "
                                "first_product_node.html. Crawl stopped."
                            )
                    else:
                        # Subsequent pages: reuse best card selector
                        if best_card_selector:
                            extraction = extract_product_urls_for_selector(
                                html,
                                best_card_selector,
                                base_url=base_url,
                                progress=progress,
                                write_debug=False,
                            )
                            links = list(
                                extraction.get("extracted_product_urls") or []
                            )
                        if not links:
                            links = _product_links_on_page(page, base_url)

                    for link in links:
                        _note_url_found(stats, link)
                    _emit(
                        progress,
                        f"Found {len(links)} product link(s) on page {pages_visited}",
                    )

                    # Card fallbacks for title/price/image if PDP fails
                    link_els = []
                    for sel in PRODUCT_LINK_SELECTORS:
                        try:
                            link_els.extend(page.query_selector_all(sel))
                        except Exception:
                            continue
                    if best_card_selector:
                        try:
                            for card in page.query_selector_all(best_card_selector):
                                try:
                                    a = card.query_selector("a[href]")
                                    if a:
                                        link_els.append(a)
                                except Exception:
                                    continue
                        except Exception:
                            pass
                    card_by_url: dict[str, dict[str, str]] = {}
                    for link_el in link_els:
                        try:
                            href = link_el.get_attribute("href") or ""
                            if not href:
                                continue
                            full = urljoin(base_url, href).split("#")[0].split("?")[0]
                            if full not in links or full in card_by_url:
                                continue
                            card_by_url[full] = _extract_from_card(link_el, base_url)
                        except Exception:
                            continue

                    new_on_page = 0
                    for product_url in links:
                        if len(seen_handles) >= self.MAX_PRODUCTS:
                            break
                        if product_url in seen_product_urls:
                            continue
                        seen_product_urls.add(product_url)

                        card = card_by_url.get(product_url) or {}
                        detail: dict[str, Any] = {
                            "title": "",
                            "price": (card.get("price") or "").strip(),
                            "compare_at_price": "",
                            "image": _unwrap_image_url(
                                (card.get("image") or "").strip(), base_url
                            ),
                            "description": "",
                            "sku": "",
                            "vendor": "",
                            "type": "",
                            "barcode": "",
                            "tags": "",
                            "options": [],
                            "product_url": product_url,
                        }

                        # Always open PDP for full DRAFT_HEADERS extraction
                        _emit(progress, f"Fetching product: {product_url}")
                        pdp = browser.new_page(viewport={"width": 1440, "height": 900})
                        pdp_ok = False
                        try:
                            try:
                                pdp.goto(
                                    product_url,
                                    wait_until="domcontentloaded",
                                    timeout=25000,
                                )
                            except Exception:
                                pdp.goto(
                                    product_url, wait_until="load", timeout=25000
                                )
                            pdp.wait_for_timeout(800)
                            pdp_detail = _extract_from_pdp(pdp, base_url, product_url)
                            if pdp_detail.get("skip"):
                                reason = pdp_detail.get("skip_reason") or "bad title"
                                title = str(pdp_detail.get("title") or "")
                                _note_skip(stats, reason, title)
                                _emit(progress, f"Skipped product ({reason})")
                                continue
                            for key in (
                                "title",
                                "price",
                                "compare_at_price",
                                "image",
                                "description",
                                "sku",
                                "vendor",
                                "type",
                                "barcode",
                                "tags",
                            ):
                                if pdp_detail.get(key):
                                    detail[key] = pdp_detail[key]
                            if pdp_detail.get("options"):
                                detail["options"] = pdp_detail["options"]
                            # Title must come from h1 only — never card/path fallback
                            detail["title"] = (pdp_detail.get("title") or "").strip()
                            pdp_ok = True
                        except Exception as exc:
                            _note_fail(stats, exc)
                            _emit(progress, f"Failed product (PDP error: {exc})")
                            continue
                        finally:
                            pdp.close()

                        if not pdp_ok or _is_bad_title(str(detail.get("title") or "")):
                            title = str(detail.get("title") or "")
                            reason = (
                                "no h1"
                                if not title.strip()
                                else f"bad title: {title!r}"
                            )
                            _note_skip(stats, reason, title)
                            _emit(progress, f"Skipped product ({reason})")
                            continue

                        # Normalize listing card price if PDP had none
                        if detail.get("price"):
                            detail["price"] = _normalize_price(str(detail["price"]))
                        if detail.get("compare_at_price"):
                            detail["compare_at_price"] = _normalize_price(
                                str(detail["compare_at_price"])
                            )
                        if detail.get("image"):
                            detail["image"] = _unwrap_image_url(
                                str(detail["image"]), base_url
                            )

                        handle = _handle_from_url(
                            product_url, str(detail.get("title") or "")
                        )
                        if handle in seen_handles:
                            continue
                        seen_handles.add(handle)

                        rows = _detail_to_drafts(detail, category_name=category_name)
                        drafts.extend(rows)
                        stats["scraped"] = int(stats.get("scraped") or 0) + 1
                        new_on_page += 1
                        _emit(
                            progress,
                            f"Collected {len(seen_handles)} product(s) "
                            f"({len(drafts)} rows)…",
                        )

                    if new_on_page == 0:
                        _emit(
                            progress,
                            "No new products on this page — stopping pagination",
                        )
                        break

                    if len(seen_handles) >= self.MAX_PRODUCTS:
                        break

                    next_url = _find_next_url(page, base_url, page_url)
                    if not next_url:
                        break
                    next_key = _listing_key(next_url)
                    if next_key in visited_pages or next_key == listing_key:
                        break
                    page_n = _current_page_number(next_url)
                    _emit(progress, f"Next page → {page_n}: {next_url}")
                    page_url = next_url
            finally:
                browser.close()

        return drafts
