"""Discover store categories / collections for seed filtering."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from sentivo_extractor.core.platform_detector import detect_platform
from sentivo_extractor.core.utils import DEFAULT_USER_AGENT

logger = logging.getLogger("sentivo_extractor.category_discovery")

SKIP_LABELS = frozenset(
    {
        "home",
        "shop",
        "all products",
        "cart",
        "checkout",
        "account",
        "login",
        "sign in",
        "sign up",
        "register",
        "search",
        "wishlist",
        "compare",
        "blog",
        "news",
        "contact",
        "contact us",
        "about",
        "about us",
        "faq",
        "faqs",
        "help",
        "support",
        "delivery",
        "shipping",
        "returns",
        "privacy",
        "privacy policy",
        "terms",
        "terms of service",
        "cookie policy",
        "my account",
        "order tracking",
        "gift cards",
    }
)

SKIP_PATH_FRAGMENTS = (
    "/cart",
    "/checkout",
    "/account",
    "/customer",
    "/login",
    "/register",
    "/wishlist",
    "/compare",
    "/search",
    "/blog",
    "/news",
    "/contact",
    "/about",
    "/faq",
    "/help",
    "/support",
    "/privacy",
    "/terms",
    "/cookie",
    "/delivery",
    "/shipping",
    "/returns",
    "/cms/",
    "/page/",
    "/pages/",
    "/wp-admin",
    "/customer/account",
)

CATEGORY_HINTS = (
    "/collections/",
    "/collection/",
    "/categories/",
    "/category/",
    "/product-category/",
    "/product_cat/",
    "/catalog/",
    "/catalogue/",
    "/shop/",
    "/c/",
)


def discover_categories(url: str) -> dict[str, Any]:
    """
    Discover categories/collections from a store homepage or seed URL.

    Returns:
      {
        "platform": str,
        "categories": [{"label": str, "url": str}, ...],
        "source": str,  # shopify_json | magento_nav | woocommerce_api | nav
      }
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"platform": "Unknown", "categories": [], "source": "invalid"}

    parsed = urlparse(url)
    if not parsed.netloc:
        return {"platform": "Unknown", "categories": [], "source": "invalid"}

    base = f"{parsed.scheme}://{parsed.netloc}"
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json",
        }
    )

    html = ""
    try:
        resp = session.get(url, timeout=15, allow_redirects=True)
        if resp.ok:
            html = resp.text or ""
    except Exception as exc:
        logger.debug("homepage fetch failed for %s: %s", url, exc)

    platform_info = detect_platform(url, html=html, session=session)
    platform = str(platform_info.get("platform") or "Unknown")

    categories: list[dict[str, str]] = []
    source = "nav"

    if platform == "Shopify":
        categories = _discover_shopify(base, session)
        source = "shopify_json" if categories else "nav"
    elif platform == "WooCommerce":
        categories = _discover_woocommerce(base, session)
        source = "woocommerce_api" if categories else "nav"
    elif platform == "Magento":
        categories = _discover_magento_nav(html, base)
        source = "magento_nav" if categories else "nav"

    if not categories:
        categories = _discover_nav_links(html, base)
        source = "nav"

    return {
        "platform": platform,
        "categories": categories,
        "source": source,
    }


def _discover_shopify(base: str, session: requests.Session) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    page = 1
    while page <= 20:
        endpoint = f"{base.rstrip('/')}/collections.json?limit=250&page={page}"
        try:
            resp = session.get(
                endpoint,
                headers={"Accept": "application/json"},
                timeout=15,
                allow_redirects=True,
            )
            if resp.status_code >= 400:
                break
            data = resp.json()
        except Exception as exc:
            logger.debug("Shopify collections.json failed: %s", exc)
            break
        collections = data.get("collections") if isinstance(data, dict) else None
        if not isinstance(collections, list) or not collections:
            break
        for col in collections:
            if not isinstance(col, dict):
                continue
            handle = (col.get("handle") or "").strip()
            title = (col.get("title") or handle or "").strip()
            if not handle:
                continue
            full = f"{base.rstrip('/')}/collections/{handle}"
            key = full.rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({"label": title or handle, "url": full})
        if len(collections) < 250:
            break
        page += 1
    return out


def _discover_woocommerce(base: str, session: requests.Session) -> list[dict[str, str]]:
    endpoints = (
        f"{base.rstrip('/')}/wp-json/wc/v3/product_categories?per_page=100",
        f"{base.rstrip('/')}/wp-json/wc/store/v1/products/categories?per_page=100",
        f"{base.rstrip('/')}/wp-json/wp/v2/product_cat?per_page=100",
    )
    for endpoint in endpoints:
        try:
            resp = session.get(
                endpoint,
                headers={"Accept": "application/json"},
                timeout=15,
                allow_redirects=True,
            )
            if resp.status_code >= 400:
                continue
            data = resp.json()
        except Exception as exc:
            logger.debug("WooCommerce categories API failed (%s): %s", endpoint, exc)
            continue
        rows = data if isinstance(data, list) else []
        if not rows:
            continue
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = (row.get("name") or row.get("label") or "").strip()
            link = (
                row.get("permalink")
                or row.get("link")
                or row.get("url")
                or ""
            ).strip()
            slug = (row.get("slug") or "").strip()
            if not link and slug:
                link = f"{base.rstrip('/')}/product-category/{slug}/"
            if not link:
                continue
            full = urljoin(base + "/", link).split("#")[0]
            key = full.rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            label = name or slug or urlparse(full).path.rstrip("/").split("/")[-1]
            out.append({"label": label, "url": full})
        if out:
            return out
    return []


def _discover_magento_nav(html: str, base: str) -> list[dict[str, str]]:
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    selectors = (
        "nav.navigation a[href]",
        ".navigation a[href]",
        "#store\\.menu a[href]",
        ".nav-sections a[href]",
        ".sections.nav-sections a[href]",
        ".category-item a[href]",
        ".menu-category a[href]",
        "nav a[href]",
    )
    anchors = []
    for sel in selectors:
        found = soup.select(sel)
        if found:
            anchors = found
            break
    return _links_from_anchors(anchors, base, prefer_hints=True)


def _discover_nav_links(html: str, base: str) -> list[dict[str, str]]:
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    anchors = soup.select(
        "nav a[href], header a[href], [role='navigation'] a[href], "
        ".menu a[href], .navigation a[href], .main-nav a[href], "
        ".main-menu a[href], .navbar a[href], #menu a[href]"
    )
    if not anchors:
        anchors = soup.select("main a[href], a[href]")
    return _links_from_anchors(anchors, base, prefer_hints=True)


def _links_from_anchors(
    anchors: list[Any],
    base: str,
    *,
    prefer_hints: bool,
    limit: int = 80,
) -> list[dict[str, str]]:
    parsed_base = urlparse(base)
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in anchors:
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base + "/", href).split("#")[0]
        parsed = urlparse(full)
        if parsed.netloc.lower() != parsed_base.netloc.lower():
            continue
        path = (parsed.path or "/").rstrip("/") or "/"
        if path == "/":
            continue
        label = re.sub(r"\s+", " ", (a.get_text(" ", strip=True) or "").strip())
        if not label or len(label) > 80:
            label = path.rstrip("/").split("/")[-1].replace("-", " ").title()
        if _should_skip(label, path):
            continue
        depth = len([p for p in path.split("/") if p])
        if depth > 4:
            continue
        if re.search(r"/page/\d+/?$", path, re.I):
            continue
        if re.search(r"\.(pdf|docx?|xlsx?|zip|rar|csv|png|jpe?g|gif|webp)$", path, re.I):
            continue
        key = full.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        results.append({"label": label, "url": full})

    if prefer_hints:
        hinted = [r for r in results if _looks_like_category(urlparse(r["url"]).path)]
        if hinted:
            results = hinted

    results.sort(
        key=lambda x: (
            0 if _looks_like_category(urlparse(x["url"]).path) else 1,
            len(urlparse(x["url"]).path.split("/")),
            x["label"].lower(),
        )
    )
    return results[:limit]


def _should_skip(label: str, path: str) -> bool:
    label_l = re.sub(r"\s+", " ", (label or "").strip()).lower()
    if label_l in SKIP_LABELS:
        return True
    low_path = (path or "/").lower()
    return any(frag in low_path for frag in SKIP_PATH_FRAGMENTS)


def _looks_like_category(path: str) -> bool:
    low = (path or "/").lower()
    return any(hint in low for hint in CATEGORY_HINTS)
