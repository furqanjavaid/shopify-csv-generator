"""Discover product URLs from category pages, sitemaps, robots.txt, pagination."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

# Single shared discovery implementation used by Audit / Pilot / Full / Site Health.
PRODUCT_DISCOVERY_VERSION = "Unified"

_SHOPIFY_PRODUCT_SITEMAP_RE = re.compile(r"/sitemap_products_\d+\.xml(?:$|\?)", re.I)

# Per-run cache of sitemap URLs that already failed (404 / errors). Key: normalized URL.
_SITEMAP_FAILURE_CACHE: dict[str, str] = {}


def clear_sitemap_failure_cache() -> None:
    """Reset failure cache (tests / new runs)."""
    _SITEMAP_FAILURE_CACHE.clear()


def _sitemap_cache_key(url: str) -> str:
    return (url or "").strip().lower().rstrip("/")


def is_shopify_product_sitemap_url(url: str) -> bool:
    return bool(_SHOPIFY_PRODUCT_SITEMAP_RE.search(url or ""))


def _dedupe_urls(urls: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        key = _sitemap_cache_key(u)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(u.strip())
    return out


def infer_discovery_platform(html: str = "", explicit: str | None = None) -> str:
    """HTML-only platform hint for sitemap probing (no network probes)."""
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    low = (html or "").lower()
    if "woocommerce" in low or "wp-content/plugins/woocommerce" in low:
        return "WooCommerce"
    if any(
        t in low
        for t in ("cdn.shopify.com", "shopify-section", "myshopify.com", "shopify.theme")
    ):
        return "Shopify"
    from sentivo_extractor.core.platform_detector import detect_magento

    if detect_magento(html):
        return "Magento"
    return "Custom"

PRODUCT_HREF_HINTS = (
    "/product/",
    "/products/",
    "/shop/",
    "/item/",
    "/p/",
    "/katalog/",
    "/catalogue/",
    "/p-",
    "/catalog/product/view/id/",
)

SKIP_PATH_FRAGMENTS = (
    "/cart",
    "/checkout",
    "/account",
    "/login",
    "/wishlist",
    "/search",
    "/blog",
    "/policy",
    "/privacy",
    "/terms",
)

_MAGENTO_CART_PRODUCT_ID_RE = re.compile(
    r"/checkout/cart/add/[^\"'\s]*/product/(\d+)", re.I
)
_MAGENTO_VIEW_ID_RE = re.compile(r"/catalog/product/view/id/(\d+)", re.I)

GetTextFn = Callable[[str], str]


def canonicalize_product_url(url: str) -> str:
    """Strip tracking params, fragments; lowercase host+path; keep clean product URL."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    qs = parse_qs(parsed.query, keep_blank_values=False)
    drop = {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "variant",
        "currency",
    }
    kept = {k: v for k, v in qs.items() if k.lower() not in drop}
    path = (parsed.path or "/").lower()
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if any(h in path for h in ("/products/", "/product/")):
        query = ""
    else:
        from urllib.parse import urlencode

        query = urlencode({k: v[0] if len(v) == 1 else v for k, v in kept.items()}, doseq=True)
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, ""))


def unique_preserve(urls: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        cu = canonicalize_product_url(u) if u else ""
        if not cu or cu in seen:
            continue
        seen.add(cu)
        out.append(cu)
    return out


def _is_product_path(path: str) -> bool:
    low = (path or "").lower()
    if any(s in low for s in SKIP_PATH_FRAGMENTS):
        return False
    if any(h in low for h in PRODUCT_HREF_HINTS):
        return True
    # Shopify-style /collections/x/products/y
    if "/products/" in low:
        return True
    # Magento catalog view-by-id
    if _MAGENTO_VIEW_ID_RE.search(low):
        return True
    return False


def _origin_of(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def discover_magento_from_category_html(
    html: str,
    base_url: str,
    max_links: int = 500,
) -> list[str]:
    """
    Magento category/listing grids often omit product hrefs and only expose
    product IDs (data-product-id / add-to-cart forms). Build crawlable PDP URLs.
    """
    soup = BeautifulSoup(html or "", "lxml")
    origin = _origin_of(base_url)
    host = urlparse(base_url).netloc.lower()
    found: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> bool:
        cu = canonicalize_product_url(url)
        if not cu or cu in seen:
            return False
        if urlparse(cu).netloc.lower() != host:
            return False
        if not _is_product_path(urlparse(cu).path):
            return False
        seen.add(cu)
        found.append(cu)
        return len(found) >= max_links

    # 1) Real product links inside Magento listing cards (when present)
    for a in soup.select(
        "a.product-item-link, .product-item-name a[href], "
        ".product-item a.product-item-photo, .product.name a[href]"
    ):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base_url, href)
        path = urlparse(full).path.lower()
        if any(s in path for s in SKIP_PATH_FRAGMENTS):
            continue
        cu = canonicalize_product_url(full)
        if not cu or urlparse(cu).netloc.lower() != host or cu in seen:
            continue
        # Magento SEO URLs from product cards may lack /products/ hints.
        seen.add(cu)
        found.append(cu)
        if len(found) >= max_links:
            return found

    # 2) Product IDs from listing grid -> /catalog/product/view/id/{id}
    product_ids: list[str] = []
    seen_ids: set[str] = set()

    def _push_id(pid: str) -> None:
        pid = str(pid or "").strip()
        if not pid.isdigit() or pid in seen_ids:
            return
        seen_ids.add(pid)
        product_ids.append(pid)

    for el in soup.select(
        ".product-item [data-product-id], .product-item [data-price-box], "
        ".price-box[data-product-id], .product-item input[name='product']"
    ):
        pid = el.get("data-product-id") or ""
        if not pid:
            box = el.get("data-price-box") or ""
            m = re.search(r"product-id-(\d+)", box, re.I)
            if m:
                pid = m.group(1)
        if not pid and el.name == "input":
            pid = el.get("value") or ""
        _push_id(pid)

    for form in soup.select(
        ".product-item form[action], form[data-role='tocart-form'], "
        "form[action*='/checkout/cart/add']"
    ):
        action = form.get("action") or ""
        m = _MAGENTO_CART_PRODUCT_ID_RE.search(action)
        if m:
            _push_id(m.group(1))
        hidden = form.select_one("input[name='product']")
        if hidden is not None:
            _push_id(hidden.get("value") or "")

    for m in _MAGENTO_CART_PRODUCT_ID_RE.finditer(html or ""):
        _push_id(m.group(1))
    for m in _MAGENTO_VIEW_ID_RE.finditer(html or ""):
        _push_id(m.group(1))

    for pid in product_ids:
        view_url = f"{origin}/catalog/product/view/id/{pid}"
        if _add(view_url):
            break

    return found

def discover_from_html(
    html: str,
    base_url: str,
    max_links: int = 500,
    card_selector: str | None = None,
) -> list[str]:
    soup = BeautifulSoup(html or "", "lxml")
    found: list[str] = []
    seen: set[str] = set()
    origin = urlparse(base_url).netloc.lower()

    anchors = []
    if card_selector:
        try:
            for card in soup.select(card_selector):
                anchors.extend(card.select("a[href]"))
        except Exception:
            anchors = []
    if not anchors:
        # Prefer product-card-ish containers first
        for sel in (
            ".product-card a[href]",
            ".product-item a[href]",
            ".product a[href]",
            "li.product a[href]",
            ".grid-product a[href]",
            "a[href]",
        ):
            try:
                anchors = soup.select(sel)
            except Exception:
                continue
            if anchors and sel != "a[href]":
                break

    for a in anchors:
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = canonicalize_product_url(urljoin(base_url, href))
        if not full:
            continue
        if urlparse(full).netloc.lower() != origin:
            continue
        if not _is_product_path(urlparse(full).path):
            continue
        if full in seen:
            continue
        seen.add(full)
        found.append(full)
        if len(found) >= max_links:
            break
    return found


def discover_pagination_urls(html: str, base_url: str, max_pages: int = 25) -> list[str]:
    soup = BeautifulSoup(html or "", "lxml")
    origin = urlparse(base_url).netloc.lower()
    pages: list[str] = []
    seen = {canonicalize_product_url(base_url)}

    for a in soup.select(
        'a[rel="next"], a[class*="next"], .pagination a[href], '
        'a[href*="page="], a[href*="/page/"]'
    ):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        full = urljoin(base_url, href).split("#")[0]
        if urlparse(full).netloc.lower() != origin:
            continue
        key = canonicalize_product_url(full) or full
        # Keep page query for pagination
        if "page=" not in full.lower() and "/page/" not in full.lower() and 'rel="next"' not in str(
            a
        ):
            text = (a.get_text(" ", strip=True) or "").lower()
            if text not in ("next", "›", "»", ">", "older"):
                continue
        if key in seen:
            continue
        seen.add(key)
        pages.append(full)
        if len(pages) >= max_pages:
            break
    return pages


def is_sitemap_index(xml_text: str) -> bool:
    low = (xml_text or "").lower()
    return "<sitemapindex" in low or ("<sitemap>" in low and "<urlset" not in low)


def extract_locs(xml_text: str) -> list[str]:
    return [m.strip() for m in re.findall(r"<loc>\s*([^<]+)\s*</loc>", xml_text or "", flags=re.I)]


def extract_sitemap_url_entries(xml_text: str) -> list[dict[str, Any]]:
    """
    Parse <url> entries from a urlset, including optional <priority>.
    Magento XML sitemaps commonly use priority 1.0=product, 0.5=category, 0.2=CMS.
    """
    entries: list[dict[str, Any]] = []
    for block in re.findall(r"<url>(.*?)</url>", xml_text or "", flags=re.I | re.S):
        loc_m = re.search(r"<loc>\s*([^<]+)\s*</loc>", block, flags=re.I)
        if not loc_m:
            continue
        loc = loc_m.group(1).strip()
        if not loc:
            continue
        pr_m = re.search(r"<priority>\s*([^<]+)\s*</priority>", block, flags=re.I)
        priority: float | None = None
        if pr_m:
            try:
                priority = float(pr_m.group(1).strip())
            except ValueError:
                priority = None
        entries.append({"loc": loc, "priority": priority})
    if entries:
        return entries
    # Fallback for urlsets without discrete <url> wrappers
    return [{"loc": loc, "priority": None} for loc in extract_locs(xml_text)]


def _magento_priority_product_candidate(path: str, priority: float | None) -> bool:
    """
    Magento XML sitemaps commonly tag products with priority ≈ 1.0.
    Used only as an explicit fallback after path-hint discovery finds nothing.
    """
    low = (path or "").lower()
    if not low or low == "/":
        return False
    if any(s in low for s in SKIP_PATH_FRAGMENTS):
        return False
    if _is_product_path(low) or "/products/" in low:
        return False  # already handled by primary path rules
    if priority is None:
        return False
    return float(priority) >= 0.9


def discover_from_sitemap(
    xml_text: str,
    max_links: int = 2000,
    *,
    platform: str | None = None,
) -> list[str]:
    """Parse a urlset sitemap for product-like URLs (path-hint primary rules only)."""
    del platform  # platform-specific Magento priority is a separate fallback
    out: list[str] = []
    seen: set[str] = set()
    for entry in extract_sitemap_url_entries(xml_text):
        url = canonicalize_product_url(entry.get("loc") or "")
        if not url:
            continue
        path = urlparse(url).path
        low = url.lower()
        if not (_is_product_path(path) or "/products/" in low):
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
        if len(out) >= max_links:
            break
    return out


def discover_from_sitemap_priority_fallback(
    xml_text: str,
    max_links: int = 2000,
    *,
    logger: logging.Logger | None = None,
) -> list[str]:
    """
    Magento fallback: accept SEO locs with sitemap priority >= 0.9 when path hints
    found no products. Logs sitemap_priority_product_detection.
    """
    out: list[str] = []
    seen: set[str] = set()
    for entry in extract_sitemap_url_entries(xml_text):
        url = canonicalize_product_url(entry.get("loc") or "")
        if not url:
            continue
        path = urlparse(url).path
        if not _magento_priority_product_candidate(path, entry.get("priority")):
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
        if len(out) >= max_links:
            break
    if out:
        msg = (
            f"sitemap_priority_product_detection: accepted {len(out)} "
            "Magento SEO URL(s) via priority>=0.9 fallback"
        )
        if logger:
            logger.info(msg)
    return out


def discover_magento_category_urls_from_sitemap(
    xml_text: str,
    max_links: int = 200,
) -> list[str]:
    """Magento category locs (priority ~0.5) for optional category-HTML expansion."""
    out: list[str] = []
    seen: set[str] = set()
    for entry in extract_sitemap_url_entries(xml_text):
        url = canonicalize_product_url(entry.get("loc") or "")
        if not url:
            continue
        path = urlparse(url).path
        if not path or path == "/":
            continue
        if any(s in path.lower() for s in SKIP_PATH_FRAGMENTS):
            continue
        priority = entry.get("priority")
        # Categories typically 0.4–0.6; never treat high-priority product rows as categories.
        if priority is None or not (0.35 <= float(priority) < 0.9):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= max_links:
            break
    return out


def discover_sitemap_urls_from_robots(robots_txt: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for line in (robots_txt or "").splitlines():
        if line.lower().startswith("sitemap:"):
            sm = line.split(":", 1)[1].strip()
            if sm:
                urls.append(urljoin(base_url, sm))
    return _dedupe_urls(urls)


def shopify_product_sitemap_candidates(base_url: str, max_files: int = 20) -> list[str]:
    """Shopify-only product sitemap file guesses (does not include /sitemap.xml)."""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return [f"{origin}/sitemap_products_{i}.xml" for i in range(1, max_files + 1)]


def references_shopify_product_sitemaps(urls: Iterable[str]) -> bool:
    return any(is_shopify_product_sitemap_url(u) for u in urls)


def should_probe_shopify_product_sitemaps(
    platform: str | None,
    listed_sitemap_urls: Iterable[str],
) -> bool:
    """Invent sitemap_products_*.xml guesses only for confirmed Shopify stores."""
    return (platform or "").strip().lower() == "shopify"


def _fetch_sitemap_text(
    get_text: GetTextFn,
    url: str,
    *,
    logger: logging.Logger | None = None,
    allow_shopify_product_sitemaps: bool = False,
    allowlist: set[str] | None = None,
) -> str | None:
    """Fetch a sitemap URL once; cache failures per domain for the rest of the run."""
    from sentivo_extractor.core.utils import http_status_from_exception, is_permanent_http_failure

    key = _sitemap_cache_key(url)
    if not key:
        return None
    if is_shopify_product_sitemap_url(url):
        allowed = allow_shopify_product_sitemaps or (
            allowlist is not None and key in allowlist
        )
        if not allowed:
            if logger:
                logger.info(
                    "Blocked invented Shopify sitemap fetch: %s",
                    url,
                )
            return None
    if key in _SITEMAP_FAILURE_CACHE:
        if logger:
            logger.info("Skipping cached sitemap failure: %s", url)
        return None
    try:
        return get_text(url)
    except Exception as exc:  # noqa: BLE001
        status = http_status_from_exception(exc)
        marker = f"HTTP {status}" if status else str(exc)[:200]
        if is_permanent_http_failure(exc):
            marker = f"permanent:{marker}"
        _SITEMAP_FAILURE_CACHE[key] = marker
        if logger:
            logger.info("Sitemap fetch failed (cached): %s — %s", url, marker)
        return None


def _mark_remaining_shopify_product_sitemaps_unavailable(
    origin: str,
    *,
    logger: logging.Logger | None = None,
) -> None:
    for url in shopify_product_sitemap_candidates(origin):
        key = _sitemap_cache_key(url)
        _SITEMAP_FAILURE_CACHE.setdefault(key, "shopify_product_sitemap_unavailable")
    if logger:
        logger.info("Shopify product sitemap unavailable.")


def _is_sitemap_products_1(url: str) -> bool:
    return bool(re.search(r"/sitemap_products_1\.xml(?:$|\?)", url or "", flags=re.I))


def _shopify_products_1_permanent_failure(url: str) -> bool:
    if not _is_sitemap_products_1(url):
        return False
    marker = _SITEMAP_FAILURE_CACHE.get(_sitemap_cache_key(url)) or ""
    return marker.startswith("permanent:") and any(
        f"HTTP {c}" in marker for c in (400, 403, 404)
    )


def expand_sitemap_index(
    xml_text: str,
    get_text: GetTextFn,
    max_child_sitemaps: int = 30,
    max_links: int = 5000,
    *,
    logger: logging.Logger | None = None,
    allow_shopify_product_sitemaps: bool = False,
    platform: str | None = None,
) -> list[str]:
    """Follow sitemap index / child sitemaps listed in XML only."""
    collected: list[str] = []
    if is_sitemap_index(xml_text):
        children = extract_locs(xml_text)[:max_child_sitemaps]
    else:
        children = [
            loc
            for loc in extract_locs(xml_text)
            if loc.lower().endswith(".xml") or "sitemap" in loc.lower()
        ][:max_child_sitemaps]
        if not children:
            return discover_from_sitemap(
                xml_text, max_links=max_links, platform=platform
            )

    # Children appearing in the index itself are allowlisted (explicitly listed).
    child_allow = {_sitemap_cache_key(c) for c in children}

    def _get(child: str) -> str | None:
        return _fetch_sitemap_text(
            get_text,
            child,
            logger=logger,
            allow_shopify_product_sitemaps=allow_shopify_product_sitemaps,
            allowlist=child_allow,
        )

    for child in children:
        child_xml = _get(child)
        if not child_xml:
            continue
        if is_sitemap_index(child_xml):
            nested_locs = extract_locs(child_xml)[:max_child_sitemaps]
            nested_allow = {_sitemap_cache_key(n) for n in nested_locs}
            for nested in nested_locs:
                nested_xml = _fetch_sitemap_text(
                    get_text,
                    nested,
                    logger=logger,
                    allow_shopify_product_sitemaps=allow_shopify_product_sitemaps,
                    allowlist=nested_allow,
                )
                if not nested_xml:
                    continue
                collected.extend(
                    discover_from_sitemap(
                        nested_xml, max_links=max_links, platform=platform
                    )
                )
                if len(collected) >= max_links:
                    return unique_preserve(collected)[:max_links]
        else:
            collected.extend(
                discover_from_sitemap(
                    child_xml, max_links=max_links, platform=platform
                )
            )
        if len(collected) >= max_links:
            break
    return unique_preserve(collected)[:max_links]


def collect_sitemap_seed_urls(
    origin: str,
    get_text: GetTextFn,
    *,
    platform: str | None = None,
    max_shopify_files: int = 20,
    logger: logging.Logger | None = None,
) -> tuple[list[str], list[str]]:
    """
    Build sitemap seed list: robots.txt → /sitemap.xml → optional Shopify probes.
    Returns (sitemap_urls, notes).
    Invented sitemap_products_*.xml only when platform == Shopify.
    """
    notes: list[str] = []
    listed: list[str] = []
    robots_url = f"{origin}/robots.txt"
    try:
        robots = get_text(robots_url)
        listed.extend(discover_sitemap_urls_from_robots(robots, origin))
    except Exception as exc:  # noqa: BLE001
        notes.append(f"robots_txt_failed:{exc}")

    default_sitemap = f"{origin}/sitemap.xml"
    seed_urls = list(listed)
    listed_keys = {_sitemap_cache_key(u) for u in seed_urls}
    if _sitemap_cache_key(default_sitemap) not in listed_keys:
        seed_urls.append(default_sitemap)

    plat = infer_discovery_platform(explicit=platform)
    if should_probe_shopify_product_sitemaps(plat, listed):
        for cand in shopify_product_sitemap_candidates(origin, max_files=max_shopify_files):
            if _sitemap_cache_key(cand) not in {_sitemap_cache_key(u) for u in seed_urls}:
                seed_urls.append(cand)
        notes.append(f"Shopify sitemap probe enabled (platform={plat})")
        if logger:
            logger.info("Shopify sitemap probe enabled (platform=%s)", plat)
    else:
        # Strip any accidental invented Shopify product sitemaps from seed list
        seed_urls = [u for u in seed_urls if not is_shopify_product_sitemap_url(u)]
        # Keep only if robots explicitly listed them
        for u in listed:
            if is_shopify_product_sitemap_url(u) and u not in seed_urls:
                seed_urls.append(u)
        msg = f"Skipping Shopify sitemap probe (platform={plat})"
        notes.append(msg)
        if logger:
            logger.info(msg)

    return _dedupe_urls(seed_urls), notes


def discover_domain_products(
    seed_url: str,
    get_text: GetTextFn,
    *,
    max_products: int = 500,
    max_pages: int = 15,
    follow_sitemaps: bool = True,
    card_selector: str | None = None,
    platform: str | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """
    Full discovery for one seed URL / domain.
    Shared by Audit / Pilot / Full / Site Health.
    Returns {product_urls, pagination_urls, sitemap_urls, notes, platform, discovery_version}.
    """
    log = logger or logging.getLogger("sentivo_extractor")
    log.info("Product Discovery Version: %s", PRODUCT_DISCOVERY_VERSION)
    notes: list[str] = []
    product_urls: list[str] = []
    pagination_urls: list[str] = []
    sitemap_urls: list[str] = []
    parsed = urlparse(seed_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    domain = (parsed.netloc or "").lower().removeprefix("www.")
    html = ""

    # Resolve platform BEFORE discovery. Prefer explicit (from live detect in crawler).
    resolved_early = ""
    if platform and str(platform).strip():
        resolved_early = str(platform).strip()
    try:
        html = get_text(seed_url)
        if not resolved_early or resolved_early in ("Custom", "Unknown"):
            resolved_early = infer_discovery_platform(html, explicit=platform)
    except Exception as exc:
        notes.append(f"seed_html_failed:{exc}")
        if not resolved_early:
            resolved_early = str(platform or "Custom")

    log.info("Detected platform: %s", resolved_early or "Custom")

    # Magento: skip ALL sitemap/robots probes (stale cache must not block nav crawl).
    # Go straight to MagentoCategoryCrawler.discover().
    if resolved_early == "Magento":
        from sentivo_extractor.crawlers.magento_crawler import MagentoCategoryCrawler

        notes.append("magento_skip_sitemaps")
        notes.append("magento_skip_robots_probes")
        try:
            # MagentoCategoryCrawler.discover() logs Starting / Found / Discovered.
            magento_crawler = MagentoCategoryCrawler(
                get_text,
                max_products=max_products,
                logger=log,
            )
            mag = magento_crawler.discover(seed_url)
            mag_urls = list(mag.get("product_urls") or [])
            product_urls.extend(mag_urls)
            notes.extend(list(mag.get("notes") or []))
            notes.append(f"magento_crawler_products:{len(mag_urls)}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"magento_crawler_failed:{exc}")
            log.warning("[Magento] crawler failed (%s) — HTML fallback only", exc)

        # Optional catalogsearch fallback if nav crawl found nothing.
        if not product_urls:
            catalogsearch = f"{origin.rstrip('/')}/catalogsearch/result/"
            try:
                search_html = get_text(catalogsearch)
                search_found = discover_magento_from_category_html(
                    search_html, catalogsearch, max_links=max_products
                )
                search_found.extend(
                    discover_from_html(
                        search_html, catalogsearch, max_links=max_products
                    )
                )
                search_found = unique_preserve(search_found)[:max_products]
                if search_found:
                    product_urls.extend(search_found)
                    notes.append(f"magento_catalogsearch_products:{len(search_found)}")
                    log.info(
                        "[Magento] catalogsearch product URLs found: %s",
                        len(search_found),
                    )
            except Exception as exc:
                notes.append(f"magento_catalogsearch_failed:{exc}")

        product_urls = unique_preserve(product_urls)[:max_products]
        return {
            "product_urls": product_urls,
            "pagination_urls": pagination_urls,
            "sitemap_urls": [],
            "notes": notes,
            "platform": "Magento",
            "discovery_version": PRODUCT_DISCOVERY_VERSION,
        }

    # Non-Magento: HTML category discovery + pagination (+ optional sitemaps)
    category_urls: list[str] = []
    try:
        if not html:
            html = get_text(seed_url)

        if len(product_urls) < max_products:
            category_urls.extend(
                discover_from_html(
                    html, seed_url, max_links=max_products, card_selector=card_selector
                )
            )
            if "product-item" in (html or "").lower():
                category_urls.extend(
                    discover_magento_from_category_html(
                        html, seed_url, max_links=max_products
                    )
                )
                category_urls = unique_preserve(category_urls)[:max_products]

            log.info(
                "Category page product URLs found: %s on %s",
                len(category_urls),
                seed_url,
            )
            notes.append(f"category_html_products:{len(category_urls)}")
            product_urls.extend(category_urls)

            pagination_urls = discover_pagination_urls(
                html, seed_url, max_pages=max_pages
            )

            for page_url in pagination_urls:
                if len(product_urls) >= max_products:
                    break
                try:
                    page_html = get_text(page_url)
                    page_found = discover_from_html(
                        page_html,
                        page_url,
                        max_links=max_products,
                        card_selector=card_selector,
                    )
                    if "product-item" in (page_html or "").lower():
                        page_found.extend(
                            discover_magento_from_category_html(
                                page_html, page_url, max_links=max_products
                            )
                        )
                    page_found = unique_preserve(page_found)
                    product_urls.extend(page_found)
                    log.info(
                        "Category page product URLs found: %s on %s",
                        len(page_found),
                        page_url,
                    )
                except Exception as exc:
                    notes.append(f"pagination_failed:{page_url}:{exc}")
    except Exception as exc:
        notes.append(f"seed_html_failed:{exc}")

    resolved_platform = infer_discovery_platform(html, explicit=resolved_early or platform)

    if follow_sitemaps:
        allow_shopify = should_probe_shopify_product_sitemaps(resolved_platform, [])
        sitemap_urls, sm_notes = collect_sitemap_seed_urls(
            origin,
            get_text,
            platform=resolved_platform,
            logger=log,
        )
        notes.extend(sm_notes)
        seed_allow = {_sitemap_cache_key(u) for u in sitemap_urls}
        shopify_sitemaps_unavailable = False
        sitemap_product_before = len(product_urls)
        fetched_sitemap_bodies: list[str] = []

        for sm_url in sitemap_urls:
            if len(product_urls) >= max_products:
                break
            if shopify_sitemaps_unavailable and is_shopify_product_sitemap_url(sm_url):
                continue
            xml = _fetch_sitemap_text(
                get_text,
                sm_url,
                logger=log,
                allow_shopify_product_sitemaps=allow_shopify,
                allowlist=seed_allow,
            )
            if not xml:
                if _shopify_products_1_permanent_failure(sm_url):
                    shopify_sitemaps_unavailable = True
                    _mark_remaining_shopify_product_sitemaps_unavailable(origin, logger=log)
                    notes.append("Shopify product sitemap unavailable.")
                continue
            fetched_sitemap_bodies.append(xml)
            if is_sitemap_index(xml) or (
                "<urlset" not in xml.lower()
                and any(loc.lower().endswith(".xml") for loc in extract_locs(xml)[:8])
            ):
                found = expand_sitemap_index(
                    xml,
                    get_text,
                    max_links=max_products - len(product_urls),
                    logger=log,
                    allow_shopify_product_sitemaps=allow_shopify,
                    platform=resolved_platform,
                )
            else:
                found = discover_from_sitemap(
                    xml,
                    max_links=max_products - len(product_urls),
                    platform=resolved_platform,
                )
            product_urls.extend(found)
            log.info(
                "Sitemap product URLs found: %s from %s (platform=%s)",
                len(found),
                sm_url,
                resolved_platform,
            )
            notes.append(f"sitemap_products:{sm_url}:{len(found)}")

            # Magento: path-hint miss → priority>=0.9 SEO fallback (explicit, logged).
            if (
                resolved_platform == "Magento"
                and not found
                and len(product_urls) < max_products
                and not is_sitemap_index(xml)
            ):
                prio_found = discover_from_sitemap_priority_fallback(
                    xml,
                    max_links=max_products - len(product_urls),
                    logger=log,
                )
                if prio_found:
                    product_urls.extend(prio_found)
                    notes.append(
                        f"sitemap_priority_product_detection:{sm_url}:{len(prio_found)}"
                    )

        # Magento last resort: expand category locs via HTML when sitemap still empty.
        if (
            resolved_platform == "Magento"
            and len(product_urls) == sitemap_product_before
            and fetched_sitemap_bodies
        ):
            cat_urls: list[str] = []
            for xml in fetched_sitemap_bodies:
                cat_urls.extend(
                    discover_magento_category_urls_from_sitemap(xml, max_links=50)
                )
            cat_urls = unique_preserve(cat_urls)[:50]
            notes.append(f"magento_sitemap_categories:{len(cat_urls)}")
            log.info(
                "Magento sitemap yielded 0 product URLs — expanding %s category URL(s)",
                len(cat_urls),
            )
            for cat_url in cat_urls:
                if len(product_urls) >= max_products:
                    break
                try:
                    cat_html = get_text(cat_url)
                    cat_found = discover_magento_from_category_html(
                        cat_html, cat_url, max_links=max_products - len(product_urls)
                    )
                    cat_found.extend(
                        discover_from_html(
                            cat_html,
                            cat_url,
                            max_links=max_products - len(product_urls),
                        )
                    )
                    cat_found = unique_preserve(cat_found)
                    product_urls.extend(cat_found)
                    log.info(
                        "Category page product URLs found: %s on %s",
                        len(cat_found),
                        cat_url,
                    )
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"magento_category_expand_failed:{cat_url}:{exc}")

    product_urls = unique_preserve(product_urls)[:max_products]
    return {
        "product_urls": product_urls,
        "pagination_urls": pagination_urls,
        "sitemap_urls": sitemap_urls,
        "notes": notes,
        "platform": resolved_platform,
        "discovery_version": PRODUCT_DISCOVERY_VERSION,
    }
