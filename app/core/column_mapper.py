"""Auto-map client columns to Shopify product CSV fields (new format)."""

from __future__ import annotations

import re
from typing import Optional

# Exact Shopify product CSV field order (new format)
SHOPIFY_FIELDS = [
    "Title",
    "URL handle",
    "Description",
    "Vendor",
    "Product category",
    "Type",
    "Tags",
    "Published on online store",
    "Status",
    "SKU",
    "Barcode",
    "Option1 name",
    "Option1 value",
    "Option1 Linked To",
    "Option2 name",
    "Option2 value",
    "Option2 Linked To",
    "Option3 name",
    "Option3 value",
    "Option3 Linked To",
    "Price",
    "Compare-at price",
    "Cost per item",
    "Charge tax",
    "Tax code",
    "Inventory tracker",
    "Inventory quantity",
    "Continue selling when out of stock",
    "Weight value (grams)",
    "Weight unit for display",
    "Requires shipping",
    "Fulfillment service",
    "Product image URL",
    "Image position",
    "Image alt text",
    "Variant image URL",
    "Gift card",
    "SEO title",
    "SEO description",
]

SKIP_LABEL = "— Skip this column —"

# Aliases → our Shopify field names (Body HTML / Variant Price etc. map to app columns)
FIELD_ALIASES: dict[str, list[str]] = {
    "Description": [
        "description",
        "body",
        "body html",
        "body_html",
        "body (html)",
        "description / specification",
        "description/specification",
        "specification",
        "product description",
        "desc",
        "details",
        "product details",
        "about",
    ],
    "Title": [
        "product name",
        "name",
        "product title",
        "item name",
        "item",
        "title",
    ],
    "Vendor": [
        "brand",
        "manufacturer",
        "supplier",
        "vendor name",
        "vendor",
    ],
    # Shopify export "Variant Price" → our "Price"
    "Price": [
        "price",
        "variant price",
        "raw min price",
        "sale price",
        "retail price",
        "cost",
    ],
    # Shopify export "Variant SKU" → our "SKU"
    "SKU": [
        "sku",
        "variant sku",
        "item sku",
        "product sku",
        "sku status",
        "article number",
    ],
    # Shopify export "Image Src" → our "Product image URL"
    "Product image URL": [
        "image url",
        "image src",
        "first image url",
        "product image url",
        "image",
        "photo url",
        "image link",
    ],
    "Tags": [
        "tag",
        "tags",
        "keywords",
        "labels",
    ],
    "Type": [
        "product type",
        "category",
        "type",
    ],
}


def _normalize_header(header: str) -> str:
    """Case-insensitive, collapse / strip extra spaces, unify separators."""
    text = (header or "").strip().lower()
    text = text.replace("_", " ")
    text = re.sub(r"\s*/\s*", "/", text)  # "description / specification" → "description/specification"
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class ColumnMapper:
    """Fuzzy keyword mapping from client headers to Shopify fields."""

    def auto_map(self, client_headers: list[str]) -> list[dict]:
        used_fields: set[str] = set()
        results: list[dict] = []

        for header in client_headers:
            field = self._match_header(header, used_fields)
            if field:
                used_fields.add(field)
            results.append({"client_col": header, "shopify_field": field})

        return results

    def _match_header(self, header: str, used_fields: set[str]) -> Optional[str]:
        h = _normalize_header(header)
        if not h:
            return None

        # Exact Shopify field name (case-insensitive, space-normalized)
        for field in SHOPIFY_FIELDS:
            if _normalize_header(field) == h:
                if field not in used_fields:
                    return field
                return None

        # Exact alias match (normalized)
        for field, aliases in FIELD_ALIASES.items():
            normalized_aliases = {_normalize_header(a) for a in aliases}
            # Also accept slash/space variants of description aliases
            if h in normalized_aliases or h.replace(" / ", "/") in normalized_aliases:
                if field not in used_fields:
                    return field
                return None

        # Broader keyword fallback for remaining columns
        rules: list[tuple[list[str], str]] = [
            (["handle", "slug", "url handle"], "URL handle"),
            (["compare", "original", "was", "old price", "compare-at"], "Compare-at price"),
            (["cost per"], "Cost per item"),
            (["qty", "stock", "inventory", "quantity"], "Inventory quantity"),
            (["weight", "gram", "kg"], "Weight value (grams)"),
            (["barcode", "ean", "upc", "isbn"], "Barcode"),
            (["seo title", "meta title"], "SEO title"),
            (["seo desc", "meta desc", "meta description"], "SEO description"),
            (["size"], "Option1 value"),
            (["color", "colour"], "Option2 value"),
            (["material", "flavor", "flavour", "scent"], "Option3 value"),
        ]

        for keywords, field in rules:
            if any(_normalize_header(kw) in h for kw in keywords):
                if field not in used_fields:
                    return field
                continue

        return None

    @staticmethod
    def dropdown_options() -> list[str]:
        return [SKIP_LABEL] + SHOPIFY_FIELDS
