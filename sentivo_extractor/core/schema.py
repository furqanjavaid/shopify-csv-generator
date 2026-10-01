"""Internal normalized product schema."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def empty_option(name: str = "") -> dict[str, Any]:
    return {"name": name, "values": []}


def empty_variant() -> dict[str, Any]:
    return {
        "sku": "",
        "barcode": "",
        "option1": "",
        "option2": "",
        "option3": "",
        "price": "",
        "compare_at_price": "",
        "inventory_qty": "",
        "available": True,
        "weight_grams": "",
        "variant_image": "",
    }


def empty_image(position: int = 1) -> dict[str, Any]:
    return {"src": "", "alt": "", "position": position}


def empty_product() -> dict[str, Any]:
    return {
        "source_url": "",
        "department": "",
        "brand": "",
        "title": "",
        "handle": "",
        "description_html": "",
        "product_type": "",
        "tags": [],
        "options": [],
        "variants": [],
        "images": [],
        "currency": "",
        "confidence_score": 0.0,
        "confidence_band": "",
        "extraction_method": "",
        "status": "active",
        "vendor": "",
        "source_name": "",
        "priority": "",
    }


def ensure_product(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge partial product dict into a full schema copy."""
    product = empty_product()
    if not raw:
        return product
    for key, value in raw.items():
        if key in product:
            product[key] = deepcopy(value)
        else:
            product[key] = value
    if not product.get("vendor") and product.get("brand"):
        product["vendor"] = product["brand"]
    if not product.get("brand") and product.get("vendor"):
        product["brand"] = product["vendor"]
    return product
