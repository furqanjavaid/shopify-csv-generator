"""WooCommerce title / handle cleanup (strip site suffix, prefer H1 thickness)."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from sentivo_extractor.core.utils import stable_handle

# Thickness prefixes used for H1 vs JSON-LD preference.
_THICKNESS_PREFIXES = (
    "1mm",
    "2mm",
    "4mm",
    "5mm",
    "6mm",
    "8mm",
    "10mm",
    "12mm",
)
_THICKNESS_START_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*mm\b", re.I)


def looks_like_woocommerce_url(url: str) -> bool:
    path = (urlparse(url or "").path or "").lower()
    if "/product-category/" in path or "/product_category/" in path:
        return True
    if "/catalog/product/" in path:
        return False
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 2 and parts[0] == "product"


def clean_woocommerce_title(title: str) -> str:
    """Strip ' | Site Name' suffix at extraction time."""
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    if " | " in text:
        text = text.split(" | ")[0].strip()
    return text


def title_has_thickness_prefix(title: str) -> bool:
    """True when title starts with a known mm thickness (1mm, 2mm, …)."""
    low = re.sub(r"\s+", " ", str(title or "").lower()).strip()
    if not low:
        return False
    for pref in _THICKNESS_PREFIXES:
        num = pref[:-2]
        if low.startswith(pref) or low.startswith(f"{num} mm"):
            return True
    return bool(_THICKNESS_START_RE.match(low))


def extract_woocommerce_h1_title(html: str) -> str:
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return ""
    for sel in ("h1.product_title", "h1.entry-title", "h1"):
        try:
            el = soup.select_one(sel)
        except Exception:
            continue
        if not el:
            continue
        text = el.get_text(" ", strip=True)
        if text:
            return text
    return ""


def resolve_woocommerce_title(
    current_title: str,
    html: str = "",
    *,
    logger: logging.Logger | None = None,
) -> str:
    """
    Prefer cleaned H1 when it has a thickness prefix that JSON-LD/current lacks.
    Always strip ' | Site Name' from the winner.
    """
    log = logger or logging.getLogger("sentivo_extractor")
    cleaned_current = clean_woocommerce_title(current_title)
    h1_raw = extract_woocommerce_h1_title(html)
    cleaned_h1 = clean_woocommerce_title(h1_raw) if h1_raw else ""

    if (
        cleaned_h1
        and title_has_thickness_prefix(cleaned_h1)
        and not title_has_thickness_prefix(cleaned_current)
    ):
        log.info("[WooCommerce] Using H1 title over JSON-LD: %s", cleaned_h1)
        return cleaned_h1

    return cleaned_current or cleaned_h1


def apply_woocommerce_title_and_handle(
    product: dict[str, Any],
    html: str = "",
    *,
    logger: logging.Logger | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    Clean title + regenerate handle from cleaned title only.

    force=True: apply even without Woo URL/platform hints (known Woo dumps).
    """
    if not isinstance(product, dict):
        return product

    src = str(product.get("source_url") or "")
    platform = str(product.get("platform") or "")
    method = str(product.get("extraction_method") or "").lower()

    is_woo = (
        force
        or platform == "WooCommerce"
        or "woocommerce" in method
        or looks_like_woocommerce_url(src)
    )
    if not is_woo:
        return product

    title = resolve_woocommerce_title(
        str(product.get("title") or ""),
        html=html,
        logger=logger,
    )
    product["title"] = title
    # Always regenerate from cleaned title (drop site-name slug tails).
    product["handle"] = stable_handle(title, src)
    return product
