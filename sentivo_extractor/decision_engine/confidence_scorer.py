"""Field-based confidence scoring for Decision Engine extractions."""

from __future__ import annotations

import re
from typing import Any

# Absolute point weights (max = 100). Threshold: 70%.
POINTS_TITLE = 40
POINTS_PRICE = 20
POINTS_SKU = 15
POINTS_IMAGES = 15
POINTS_DESCRIPTION = 10
MAX_SCORE = (
    POINTS_TITLE + POINTS_PRICE + POINTS_SKU + POINTS_IMAGES + POINTS_DESCRIPTION
)
CONFIDENCE_THRESHOLD = 70  # percent


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def _has_title(data: dict[str, Any]) -> bool:
    return bool(str(data.get("title") or data.get("name") or "").strip())


def _has_price(data: dict[str, Any]) -> bool:
    if str(data.get("price") or "").strip():
        return True
    variants = data.get("variants") or []
    for v in variants:
        if isinstance(v, dict) and str(v.get("price") or "").strip():
            return True
    return False


def _has_sku(data: dict[str, Any]) -> bool:
    if str(data.get("sku") or data.get("variant_sku") or "").strip():
        return True
    variants = data.get("variants") or []
    for v in variants:
        if isinstance(v, dict) and str(v.get("sku") or "").strip():
            return True
    return False


def _has_images(data: dict[str, Any]) -> bool:
    images = data.get("images") or []
    for img in images:
        if isinstance(img, dict) and str(img.get("src") or "").strip():
            return True
        if isinstance(img, str) and img.strip():
            return True
    if str(data.get("image") or "").strip():
        return True
    return False


def _has_description(data: dict[str, Any]) -> bool:
    desc = _strip_html(
        str(
            data.get("description_html")
            or data.get("description")
            or data.get("body_html")
            or ""
        )
    )
    return len(desc) >= 20


class ConfidenceScorer:
    """
    Score extracted product fields on a 0–100 scale.

    Title +40, Price +20, SKU +15, Images +15, Description +10.
    Total below 70% → caller should retry with another strategy.

    When price_optional=True (cut-to-size Magento), missing price does not
    reduce the denominator or fail the product — price is a bonus only.
    """

    threshold = CONFIDENCE_THRESHOLD
    max_score = MAX_SCORE

    def score(
        self,
        data: dict[str, Any] | None,
        *,
        price_optional: bool = False,
    ) -> dict[str, Any]:
        product = data if isinstance(data, dict) else {}
        points = 0
        breakdown: dict[str, int] = {
            "title": 0,
            "price": 0,
            "sku": 0,
            "images": 0,
            "description": 0,
        }
        if _has_title(product):
            breakdown["title"] = POINTS_TITLE
            points += POINTS_TITLE
        has_price = _has_price(product)
        if has_price:
            breakdown["price"] = POINTS_PRICE
            points += POINTS_PRICE
        if _has_sku(product):
            breakdown["sku"] = POINTS_SKU
            points += POINTS_SKU
        if _has_images(product):
            breakdown["images"] = POINTS_IMAGES
            points += POINTS_IMAGES
        if _has_description(product):
            breakdown["description"] = POINTS_DESCRIPTION
            points += POINTS_DESCRIPTION

        if price_optional and not has_price:
            effective_max = MAX_SCORE - POINTS_PRICE
        else:
            effective_max = MAX_SCORE
        percent = int(round((points / effective_max) * 100)) if effective_max else 0
        missing = [k for k, v in breakdown.items() if v == 0]
        if price_optional:
            missing = [m for m in missing if m != "price"]
        return {
            "points": points,
            "percent": percent,
            "max_points": effective_max,
            "threshold": CONFIDENCE_THRESHOLD,
            "passed": percent >= CONFIDENCE_THRESHOLD,
            "breakdown": breakdown,
            "missing": missing,
            "price_optional": price_optional,
        }

    def passes(
        self, data: dict[str, Any] | None, *, price_optional: bool = False
    ) -> bool:
        return bool(self.score(data, price_optional=price_optional).get("passed"))
