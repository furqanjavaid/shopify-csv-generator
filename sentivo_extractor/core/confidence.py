"""Confidence scoring for extracted products."""

from __future__ import annotations

import re
from typing import Any

METHOD_WEIGHTS = {
    "shopify": 0.25,
    "woocommerce_store_api": 0.22,
    "woocommerce": 0.18,
    "jsonld": 0.18,
    "nextjs_hydration": 0.16,
    "magento_jsonconfig": 0.14,
    "playwright": 0.12,
    "html": 0.08,
}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def score_product(product: dict[str, Any]) -> float:
    """
    Score 0.0–1.0 based on field completeness + extraction method reliability.
    Thresholds: >=0.90 green, 0.70–0.89 yellow, <0.70 red.
    """
    score = 0.0
    title = (product.get("title") or "").strip()
    if title:
        score += 0.25

    variants = [v for v in (product.get("variants") or []) if isinstance(v, dict)]
    has_price = any(str(v.get("price") or "").strip() for v in variants)
    if not has_price:
        has_price = bool(str(product.get("price") or "").strip())
    if has_price:
        score += 0.20

    images = product.get("images") or []
    if images and any(
        (isinstance(img, dict) and img.get("src"))
        or (isinstance(img, str) and img.strip())
        for img in images
    ):
        score += 0.15

    desc = _strip_html(str(product.get("description_html") or ""))
    if len(desc) >= 20:
        score += 0.10

    options = [o for o in (product.get("options") or []) if isinstance(o, dict)]
    meaningful_options = [
        o
        for o in options
        if (o.get("name") or "") not in ("", "Title")
        or any(v != "Default Title" for v in (o.get("values") or []))
    ]
    if meaningful_options:
        # Expect unique variant combos covering options
        combo_keys = {
            (
                str(v.get("option1") or ""),
                str(v.get("option2") or ""),
                str(v.get("option3") or ""),
            )
            for v in variants
        }
        if len(variants) >= 2 and len(combo_keys) == len(variants):
            score += 0.15
        elif variants:
            score += 0.07
    else:
        # Single default variant is fine
        if variants:
            score += 0.10

    method = str(product.get("extraction_method") or "").lower()
    method_bonus = 0.05
    for key, weight in METHOD_WEIGHTS.items():
        if key in method:
            method_bonus = weight
            break
    score += method_bonus

    return round(min(1.0, score), 3)


def confidence_band(score: float) -> str:
    if score >= 0.90:
        return "green"
    if score >= 0.70:
        return "yellow"
    return "red"


def apply_confidence(product: dict[str, Any]) -> dict[str, Any]:
    sources = product.get("field_sources") or {}
    all_shopify_js = (
        isinstance(sources, dict)
        and bool(sources)
        and all(str(v) == "Shopify JS" for v in sources.values() if v)
    )
    method = str(product.get("extraction_method") or "")
    shopify_js_method = "Shopify JS" in method or method.lower().strip() in {
        "shopify",
        "shopify js",
    }

    # Full Shopify JS extractions are authoritative — green / 1.00.
    if all_shopify_js or (
        shopify_js_method
        and product.get("title")
        and product.get("images")
        and product.get("variants")
    ):
        product.pop("confidence_partial", None)
        product.pop("variant_mismatch", None)
        product["confidence_score"] = 1.0
        product["confidence_band"] = "green"
        return product

    score = score_product(product)
    if product.get("confidence_partial") or product.get("variant_mismatch"):
        # Cap at yellow / partial — never green when variants are incomplete.
        score = min(score, 0.89)
        product["confidence_partial"] = True
    product["confidence_score"] = score
    product["confidence_band"] = confidence_band(score)
    return product
