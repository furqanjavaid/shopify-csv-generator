"""Dedicated description extractor with priority fallbacks."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

MIN_DESCRIPTION_LEN = 20

CSS_SELECTORS: tuple[str, ...] = (
    ".product-description",
    "#description",
    ".description",
    '[data-content-type="description"]',
    ".product.attribute.description .value",
    ".product-info-main .overview",
    "#tab-description",
    ".woocommerce-product-details__short-description",
    ".product__description",
    "article.product-single__description",
    '[class*="description"]',
)

_DESC_KEYS = (
    "body_html",
    "description_html",
    "description",
    "short_description",
    "bodyHtml",
    "body",
    "desc",
)

_INITIAL_STATE_PATTERNS = (
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;",
    r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;",
    r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\})\s*;",
)


def plain_text_length(html_or_text: str) -> int:
    """Length of description after stripping tags / collapsing whitespace."""
    text = html_or_text or ""
    try:
        plain = BeautifulSoup(text, "lxml").get_text(" ", strip=True)
    except Exception:
        plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return len(plain)


def is_usable_description(html_or_text: str | None) -> bool:
    if not html_or_text:
        return False
    return plain_text_length(str(html_or_text)) >= MIN_DESCRIPTION_LEN


def extract_description(
    *,
    url: str,
    html: str = "",
    http: Any = None,
    playwright_html: str | None = None,
    logger: logging.Logger | None = None,
) -> tuple[str, str]:
    """
    Run the description priority chain.

    Returns (description_html, source_label). Empty string if nothing usable.
    Keeps HTML when the source provides markup (Body HTML column); plain text
    from meta tags is returned as-is for the normalizer to wrap.
    """
    log = logger or logging.getLogger(__name__)
    steps: list[tuple[str, Any]] = [
        ("Shopify JS", lambda: _from_shopify_js(url, http)),
        ("JSON-LD", lambda: _from_jsonld(html)),
        ("Embedded JSON", lambda: _from_embedded_or_initial_state(html)),
        ("OpenGraph", lambda: _from_opengraph(html)),
        (
            "CSS selectors",
            lambda: _from_css_selectors(html, playwright_html),
        ),
        ("meta description", lambda: _from_meta_description(html, playwright_html)),
    ]
    for label, fn in steps:
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001
            log.debug("Description step %s failed: %s", label, exc)
            continue
        if not is_usable_description(value):
            continue
        text = str(value).strip()
        chars = plain_text_length(text)
        log.info("Description extracted via %s: %s chars", label, chars)
        return text, label
    return "", ""


def _from_shopify_js(url: str, http: Any) -> str:
    if http is None or not url:
        return ""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    m = re.search(r"/products/([^/?#]+)", path, re.I)
    if not m:
        return ""
    handle = m.group(1)
    base = f"{parsed.scheme}://{parsed.netloc}"
    data: Any = None
    try:
        data = http.get_json(f"{base}/products/{handle}.js")
    except Exception:
        try:
            payload = http.get_json(f"{base}/products.json?limit=250")
            for p in payload.get("products") or []:
                if p.get("handle") == handle:
                    data = p
                    break
        except Exception:
            return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("body_html") or data.get("description") or "").strip()


def _from_jsonld(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        found = _find_description_in_jsonld(data)
        if found:
            return found
    return ""


def _find_description_in_jsonld(node: Any) -> str:
    if isinstance(node, list):
        for item in node:
            found = _find_description_in_jsonld(item)
            if found:
                return found
        return ""
    if not isinstance(node, dict):
        return ""
    types = node.get("@type") or ""
    type_list = types if isinstance(types, list) else [types]
    type_norm = {str(t).lower() for t in type_list}
    if "product" in type_norm or "productgroup" in type_norm:
        desc = node.get("description")
        if isinstance(desc, dict):
            desc = desc.get("value") or desc.get("text") or ""
        if desc and is_usable_description(str(desc)):
            return str(desc).strip()
    graph = node.get("@graph")
    if graph is not None:
        found = _find_description_in_jsonld(graph)
        if found:
            return found
    for key in ("mainEntity", "item", "product"):
        child = node.get(key)
        if child is not None:
            found = _find_description_in_jsonld(child)
            if found:
                return found
    return ""


def _from_embedded_or_initial_state(html: str) -> str:
    if not html:
        return ""
    # window.__INITIAL_STATE__ / similar
    for pat in _INITIAL_STATE_PATTERNS:
        m = re.search(pat, html, re.S)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        found = _find_description_in_obj(data)
        if found:
            return found

    # Inline product-ish JSON blobs in script tags
    try:
        soup = BeautifulSoup(html, "lxml")
        for script in soup.find_all("script"):
            stype = (script.get("type") or "").lower()
            sid = (script.get("id") or "").lower()
            if "ld+json" in stype or sid in {"__next_data__", "__nuxt_data__"}:
                continue
            text = (script.string or script.get_text() or "").strip()
            if not text or len(text) < 40:
                continue
            if "product" not in text.lower() and "description" not in text.lower():
                continue
            for chunk in re.findall(r"\{[^{}]{40,8000}\}", text):
                try:
                    data = json.loads(chunk)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                found = _find_description_in_obj(data)
                if found:
                    return found
    except Exception:
        pass

    # Next.js __NEXT_DATA__
    try:
        soup = BeautifulSoup(html, "lxml")
        tag = soup.find("script", id="__NEXT_DATA__")
        if tag:
            data = json.loads(tag.string or tag.get_text() or "")
            found = _find_description_in_obj(data)
            if found:
                return found
    except Exception:
        pass
    return ""


def _find_description_in_obj(node: Any, depth: int = 0) -> str:
    if depth > 8 or node is None:
        return ""
    if isinstance(node, list):
        for item in node[:40]:
            found = _find_description_in_obj(item, depth + 1)
            if found:
                return found
        return ""
    if not isinstance(node, dict):
        return ""
    for key in _DESC_KEYS:
        if key not in node:
            continue
        val = node.get(key)
        if isinstance(val, dict):
            val = val.get("html") or val.get("value") or val.get("text") or ""
        if val and is_usable_description(str(val)):
            return str(val).strip()
    # Prefer nested product-like nodes first
    for key in ("product", "productDetail", "pdp", "pageProps", "props"):
        child = node.get(key)
        if isinstance(child, (dict, list)):
            found = _find_description_in_obj(child, depth + 1)
            if found:
                return found
    for child in list(node.values())[:30]:
        if isinstance(child, (dict, list)):
            found = _find_description_in_obj(child, depth + 1)
            if found:
                return found
    return ""


def _from_opengraph(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("meta", attrs={"property": "og:description"})
    if tag and tag.get("content"):
        return str(tag["content"]).strip()
    return ""


def _from_css_selectors(html: str, playwright_html: str | None) -> str:
    # Prefer Playwright-rendered DOM when available, then HTTP HTML.
    for blob, label in (
        (playwright_html, "Playwright"),
        (html, "HTTP"),
    ):
        if not blob:
            continue
        found = _css_on_html(blob)
        if found:
            return found
        del label
    return ""


def _css_on_html(html: str) -> str:
    try:
        soup = BeautifulSoup(html or "", "lxml")
    except Exception:
        return ""
    for sel in CSS_SELECTORS:
        try:
            el = soup.select_one(sel)
        except Exception:
            continue
        if not el:
            continue
        try:
            if hasattr(el, "decode_contents"):
                content = str(el.decode_contents()).strip()
            else:
                content = str(el).strip()
        except Exception:
            content = el.get_text(" ", strip=True)
        if is_usable_description(content):
            return content
    return ""


def _from_meta_description(html: str, playwright_html: str | None) -> str:
    for blob in (playwright_html, html):
        if not blob:
            continue
        try:
            soup = BeautifulSoup(blob, "lxml")
            tag = soup.find("meta", attrs={"name": "description"})
            if tag and tag.get("content"):
                content = str(tag["content"]).strip()
                if is_usable_description(content):
                    return content
        except Exception:
            continue
    return ""
