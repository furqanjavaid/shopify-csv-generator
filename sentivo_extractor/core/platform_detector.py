"""Detect ecommerce platform from URL / HTML / probes."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import requests

from sentivo_extractor.core.utils import (
    BROWSER_HEADERS,
    BROWSER_USER_AGENT,
    DEFAULT_USER_AGENT,
)

Platform = str  # Shopify | WooCommerce | Magento | Next.js | Custom | Unknown

logger = logging.getLogger(__name__)

_SCRIPT_LINK_MAGE_RE = re.compile(
    r"<(?:script|link)\b[^>]*(?:src|href)\s*=\s*[\"'][^\"']*(?:mage|magento)[^\"']*[\"']",
    re.I,
)
_HREF_CATALOG_PRODUCT_RE = re.compile(
    r"href\s*=\s*[\"'][^\"']*/catalog/product/[^\"']*[\"']",
    re.I,
)


def detect_shopify(base_url: str, session: requests.Session | None = None) -> bool:
    sess = session or requests.Session()
    probe = f"{base_url.rstrip('/')}/products.json?limit=1"
    try:
        resp = sess.get(
            probe,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
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


def detect_woocommerce(html: str, base_url: str = "", session: requests.Session | None = None) -> bool:
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
        sess = session or requests.Session()
        try:
            resp = sess.get(
                f"{base_url.rstrip('/')}/wp-json/wc/store/products?per_page=1",
                headers={"User-Agent": DEFAULT_USER_AGENT},
                timeout=8,
                allow_redirects=True,
            )
            if resp.status_code == 200 and resp.text.strip().startswith("["):
                return True
        except Exception:
            pass
    return False


def detect_magento(html: str = "", url: str = "") -> bool:
    """
    Magento 1/2 (incl. Hyva) signals:
    - meta generator containing Magento
    - Any script/link src/href containing mage/magento
    - Mage.Cookies / mage-data-role / classic Magento JS markers
    - URL / href patterns like /catalog/product/
    """
    url_low = (url or "").lower()
    if "/catalog/product/" in url_low or "/catalogsearch/" in url_low:
        return True

    body = html or ""
    if not body:
        return False
    low = body.lower()

    # meta name="generator" content="Magento ..."
    if 'name="generator"' in low and "magento" in low:
        return True
    if 'content="magento' in low or "content='magento" in low:
        return True

    # Any script/link referencing mage / magento (Hyva often loads mage modules this way)
    if _SCRIPT_LINK_MAGE_RE.search(body):
        return True
    if "mage" in low or "magento" in low:
        # Broader text hit only when paired with asset/path cues
        if any(
            tok in low
            for tok in (
                "/static/version",
                "mage/",
                "magento_",
                "magento/",
                "requirejs",
            )
        ):
            return True

    # /catalog/product/ appearing in any href on the page
    if _HREF_CATALOG_PRODUCT_RE.search(body) or "/catalog/product/" in low:
        return True

    return any(
        token in low
        for token in (
            "mage.cookies",
            "mage-data-role",
            "mage/requirejs",
            "magento_init",
            "data-mage-init",
            "mage-init",
            "catalog-product-view",
            "magento_theme",
            "hyvä",
            "hyva",
            'x-data="mage',
            "checkout/cart/add",
        )
    )


def detect_next_or_nuxt(html: str) -> str | None:
    low = (html or "").lower()
    if "__next_data__" in low or "/_next/static" in low:
        return "Next.js"
    if "__nuxt__" in low or "window.__nuxt" in low or "/_nuxt/" in low:
        return "Nuxt"
    if re.search(r'data-reactroot|id="root".*react', low):
        return "React"
    return None


_CF_ERROR_MARKERS = (
    "cf-error-details",
    "bad gateway",
    "error code 502",
    "error code 503",
    "error code 520",
    "attention required! | cloudflare",
    "checking your browser before accessing",
    "cdn-cgi/styles/main.css",
)


def _is_error_or_challenge_html(html: str) -> bool:
    """True for Cloudflare/WAF error or challenge pages (not real storefront HTML)."""
    if not html or len(html) < 200:
        return not bool(html)
    low = html.lower()
    if any(m in low for m in _CF_ERROR_MARKERS):
        return True
    # Tiny bare 5xx titles
    if re.search(r"<title>[^<]*\b(502|503|504|403|401)\b[^<]*</title>", low):
        return True
    return False


def _fetch_homepage_html(
    url: str,
    session: requests.Session | None = None,
    *,
    browser_headers: bool = False,
) -> str:
    sess = session or requests.Session()
    headers = dict(BROWSER_HEADERS) if browser_headers else {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }
    if browser_headers:
        headers.setdefault("User-Agent", BROWSER_USER_AGENT)
    try:
        resp = sess.get(url, headers=headers, timeout=20, allow_redirects=True)
        if resp.status_code >= 400:
            return ""
        text = (resp.text or "")[:300_000]
        if _is_error_or_challenge_html(text):
            return ""
        return text
    except Exception:
        return ""


def _fetch_homepage_playwright(url: str) -> str:
    """Optional Playwright fallback when requests returns empty/blocked HTML."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    user_agent=BROWSER_USER_AGENT,
                    extra_http_headers={
                        k: v
                        for k, v in BROWSER_HEADERS.items()
                        if k.lower() != "user-agent"
                    },
                    viewport={"width": 1440, "height": 900},
                )
                page = ctx.new_page()
                resp = page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1200)
                if resp is not None and resp.status >= 400:
                    return ""
                text = (page.content() or "")[:300_000]
                if _is_error_or_challenge_html(text):
                    return ""
                return text
            finally:
                browser.close()
    except Exception as exc:
        logger.info("Playwright platform probe failed for %s: %s", url, exc)
        return ""


def detect_platform(
    url: str,
    html: str = "",
    session: requests.Session | None = None,
    *,
    live_fallback: bool = True,
) -> dict[str, Any]:
    """
    Return {"platform": str, "signals": list[str], "html": optional body used}.

    When initial detection is Custom/Unknown and live_fallback is True, re-fetch the
    homepage with browser headers (and Playwright if needed) and re-check Magento
    BEFORE callers run discovery.
    """
    url = (url or "").strip()
    signals: list[str] = []
    if not url.startswith(("http://", "https://")):
        return {"platform": "Unknown", "signals": ["invalid_url"], "html": ""}

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    home = f"{base}/"
    sess = session or requests.Session()

    if detect_shopify(base, sess):
        signals.append("products.json")
        return {"platform": "Shopify", "signals": signals, "html": html or ""}

    body = html or ""
    if body and _is_error_or_challenge_html(body):
        body = ""
    if not body:
        body = _fetch_homepage_html(url, sess, browser_headers=False)
        if body:
            signals.append("fetched_default")

    if body and detect_woocommerce(body, base, sess):
        signals.append("woocommerce")
        return {"platform": "WooCommerce", "signals": signals, "html": body}

    if body and detect_magento(body, url=url):
        signals.append("magento")
        logger.info("Detected platform: Magento")
        return {"platform": "Magento", "signals": signals, "html": body}

    spa = detect_next_or_nuxt(body) if body else None
    if spa:
        signals.append(spa.lower())
        return {"platform": spa, "signals": signals, "html": body}

    # Live fallback: Custom so far — re-fetch with browser headers and re-check Magento.
    if live_fallback:
        live_body = _fetch_homepage_html(home, sess, browser_headers=True)
        if not live_body:
            live_body = _fetch_homepage_html(url, sess, browser_headers=True)
        if live_body:
            signals.append("live_browser_fetch")
            if detect_magento(live_body, url=url):
                signals.append("magento_live")
                logger.info("Detected platform: Magento (live browser-header fetch)")
                return {"platform": "Magento", "signals": signals, "html": live_body}
            if detect_woocommerce(live_body, base, sess):
                signals.append("woocommerce_live")
                return {
                    "platform": "WooCommerce",
                    "signals": signals,
                    "html": live_body,
                }
            body = live_body or body

        # Still Custom — try Playwright render for JS-heavy Magento/Hyva storefronts.
        if not detect_magento(body, url=url):
            pw_body = _fetch_homepage_playwright(home)
            if pw_body:
                signals.append("live_playwright_fetch")
                body = pw_body
                if detect_magento(pw_body, url=url):
                    signals.append("magento_playwright")
                    logger.info("Detected platform: Magento (Playwright live fetch)")
                    return {
                        "platform": "Magento",
                        "signals": signals,
                        "html": pw_body,
                    }

        if detect_magento(body, url=url):
            signals.append("magento")
            return {"platform": "Magento", "signals": signals, "html": body}

    if '"@type"' in body and "Product" in body:
        signals.append("json-ld")
        return {"platform": "Custom", "signals": signals, "html": body}

    return {"platform": "Custom", "signals": signals or ["html"], "html": body}
