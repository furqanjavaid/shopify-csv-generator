"""Playwright-based HTML catalog scraper for non-Shopify stores."""

from __future__ import annotations

import re
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from playwright.sync_api import sync_playwright

from app.core.collection_crawler import (
    CollectionCrawlError,
    drafts_to_parsed_data,
)
from app.utils.helpers import slugify

ProgressCallback = Callable[[str], None]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

TITLE_SELECTORS = [
    "h1",
    "h2",
    '[class*="product-title"]',
    '[class*="product-name"]',
    '[class*="product__title"]',
]
PRICE_SELECTORS = [
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
    "/privacy",
    "/terms",
    "/shipping",
    "/returns",
    "/policy",
    "/news",
    "/advice",
    "/sustainability",
    "/applications",
    "/material-names",
    "/customer",
    "/search",
)
_CATEGORY_SKIP_LABELS = (
    "blog",
    "contact",
    "shipping",
    "faq",
    "about",
    "terms",
    "privacy",
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
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    matches = re.findall(r"\d[\d,]*\.?\d*", cleaned.replace(",", ""))
    if not matches:
        return ""
    return matches[-1]


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
            if src and not src.startswith("data:"):
                return urljoin(base_url, src)
        except Exception:
            continue
    return ""


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


def discover_category_links(url: str) -> list[dict[str, str]]:
    """
    Extract category/collection links from homepage nav.
    Returns [{"label": str, "url": str}, ...]
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
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        anchors = soup.select(
            "nav a[href], header a[href], .menu a[href], .navigation a[href], "
            "#menu a[href], .navbar a[href], .main-nav a[href], footer a[href]"
        )
        if not anchors:
            anchors = soup.select("a[href]")
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
        low_path = path.lower()
        if any(frag in low_path for frag in _CATEGORY_SKIP_FRAGMENTS):
            continue
        label_clean = re.sub(r"\s+", " ", (label or "").strip())
        if not label_clean or len(label_clean) > 80:
            # fallback from path
            label_clean = path.rstrip("/").split("/")[-1].replace("-", " ").title()
        if label_clean.lower() in _CATEGORY_SKIP_LABELS:
            continue
        # Prefer category-like depth (1–3 segments), allow product category pages
        depth = len([p for p in path.split("/") if p])
        if depth > 4:
            continue
        key = full.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        results.append({"label": label_clean, "url": full})

    # Prefer shorter labels / shallower paths first, cap list
    results.sort(key=lambda x: (len(urlparse(x["url"]).path.split("/")), x["label"].lower()))
    return results[:40]


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


def _extract_from_pdp(page, base_url: str) -> dict[str, str]:
    title = _first_text(page, TITLE_SELECTORS)
    price = _normalize_price(_first_text(page, PRICE_SELECTORS))
    image = _first_image(page, IMAGE_SELECTORS, base_url)
    return {"title": title, "price": price, "image": image}


def _to_draft(title: str, price: str, image: str, product_url: str) -> dict[str, str]:
    handle = slugify(title) or slugify(urlparse(product_url).path.split("/")[-1]) or "product"
    return {
        "title": title,
        "url_handle": handle,
        "description": "",
        "vendor": "",
        "product_category": "",
        "type": "",
        "tags": "",
        "sku": "",
        "barcode": "",
        "option1_name": "Title",
        "option1_value": "Default Title",
        "option2_name": "",
        "option2_value": "",
        "option3_name": "",
        "option3_value": "",
        "price": price,
        "compare_at_price": "",
        "inventory_quantity": "",
        "product_image_url": image,
        "image_position": "1" if image else "",
        "status": "Active",
    }


class HtmlCatalogScraper:
    """Scrape non-Shopify catalog / shop pages with Playwright."""

    MAX_PAGES = 25
    MAX_PRODUCTS = 200

    def scrape(
        self,
        url: str,
        progress: Optional[ProgressCallback] = None,
        platform: str = "Custom",
    ) -> dict[str, Any]:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise CollectionCrawlError("URL must start with http:// or https://")

        parsed = urlparse(url)
        if not parsed.netloc:
            raise CollectionCrawlError("Invalid URL.")
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        _emit(progress, f"Non-Shopify site detected ({platform}) — using Playwright…")
        drafts: list[dict[str, str]] = []
        seen_products: set[str] = set()
        visited_pages: set[str] = set()
        page_url = url
        pages_visited = 0
        empty_pages_in_a_row = 0

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

                    links = _product_links_on_page(page, base_url)
                    _emit(progress, f"Found {len(links)} product link(s) on page {pages_visited}")

                    # Prefer card-level extraction from listing page first
                    link_els = []
                    for sel in PRODUCT_LINK_SELECTORS:
                        try:
                            link_els.extend(page.query_selector_all(sel))
                        except Exception:
                            continue

                    card_by_url: dict[str, dict[str, str]] = {}
                    for link_el in link_els:
                        try:
                            href = link_el.get_attribute("href") or ""
                            if not href:
                                continue
                            full = urljoin(base_url, href).split("#")[0].split("?")[0]
                            if full not in links:
                                continue
                            if full in card_by_url:
                                continue
                            card_by_url[full] = _extract_from_card(link_el, base_url)
                        except Exception:
                            continue

                    new_on_page = 0
                    for product_url in links:
                        if len(drafts) >= self.MAX_PRODUCTS:
                            break
                        if product_url in seen_products:
                            continue
                        seen_products.add(product_url)

                        card = card_by_url.get(product_url) or {}
                        title = (card.get("title") or "").strip()
                        price = (card.get("price") or "").strip()
                        image = (card.get("image") or "").strip()

                        # Visit PDP when listing card is incomplete
                        if not title or not price:
                            _emit(progress, f"Fetching product: {product_url}")
                            pdp = browser.new_page(viewport={"width": 1440, "height": 900})
                            try:
                                try:
                                    pdp.goto(
                                        product_url,
                                        wait_until="domcontentloaded",
                                        timeout=25000,
                                    )
                                except Exception:
                                    pdp.goto(product_url, wait_until="load", timeout=25000)
                                pdp.wait_for_timeout(800)
                                detail = _extract_from_pdp(pdp, base_url)
                                title = title or detail.get("title") or ""
                                price = price or detail.get("price") or ""
                                image = image or detail.get("image") or ""
                            except Exception as exc:
                                _emit(progress, f"Skipped product ({exc})")
                                continue
                            finally:
                                pdp.close()

                        if not title:
                            # Fallback: last path segment
                            title = urlparse(product_url).path.rstrip("/").split("/")[-1]
                            title = title.replace("-", " ").replace("_", " ").title()

                        drafts.append(_to_draft(title, price, image, product_url))
                        new_on_page += 1
                        _emit(
                            progress,
                            f"Collected {len(drafts)} product(s)…",
                        )

                    if new_on_page == 0:
                        empty_pages_in_a_row += 1
                        _emit(progress, "No new products on this page — stopping pagination")
                        if empty_pages_in_a_row >= 1:
                            break
                    else:
                        empty_pages_in_a_row = 0

                    if len(drafts) >= self.MAX_PRODUCTS:
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

        if not drafts:
            raise CollectionCrawlError(
                "No products found on this page. Try a category/shop URL with product listings."
            )

        parsed = drafts_to_parsed_data(drafts)
        parsed["strategy_used"] = f"{platform} HTML (Playwright)"
        parsed["platform"] = platform
        parsed["errors"] = []
        _emit(progress, f"Done — {parsed['row_count']} products via Playwright")
        return parsed
