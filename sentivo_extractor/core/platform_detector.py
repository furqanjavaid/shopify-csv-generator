"""Detect ecommerce platform from URL / HTML / probes."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import requests

from sentivo_extractor.core.utils import DEFAULT_USER_AGENT

Platform = str  # Shopify | WooCommerce | Magento | Next.js | Custom | Unknown


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
    - Mage.Cookies / mage-data-role / classic Magento JS markers
    - URL patterns like /catalog/product/
    """
    url_low = (url or "").lower()
    if "/catalog/product/" in url_low or "/catalogsearch/" in url_low:
        return True

    low = (html or "").lower()
    if not low:
        return False

    # meta name="generator" content="Magento ..."
    if 'name="generator"' in low and "magento" in low:
        return True
    if "content=\"magento" in low or "content='magento" in low:
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
            "hyva",
            "x-data=\"mage",
            "checkout/cart/add",
            "form_key",
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


def detect_platform(
    url: str,
    html: str = "",
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """
    Return {"platform": str, "signals": list[str]}.
    """
    url = (url or "").strip()
    signals: list[str] = []
    if not url.startswith(("http://", "https://")):
        return {"platform": "Unknown", "signals": ["invalid_url"]}

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    sess = session or requests.Session()

    if detect_shopify(base, sess):
        signals.append("products.json")
        return {"platform": "Shopify", "signals": signals}

    body = html
    if not body:
        try:
            resp = sess.get(
                url,
                headers={
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=15,
                allow_redirects=True,
            )
            body = resp.text[:250_000] if resp.ok else ""
        except Exception:
            body = ""

    if detect_woocommerce(body, base, sess):
        signals.append("woocommerce")
        return {"platform": "WooCommerce", "signals": signals}

    if detect_magento(body, url=url):
        signals.append("magento")
        return {"platform": "Magento", "signals": signals}

    spa = detect_next_or_nuxt(body)
    if spa:
        signals.append(spa.lower())
        return {"platform": spa, "signals": signals}

    if '"@type"' in body and "Product" in body:
        signals.append("json-ld")
        return {"platform": "Custom", "signals": signals}

    return {"platform": "Custom", "signals": signals or ["html"]}
