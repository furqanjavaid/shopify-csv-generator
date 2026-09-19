"""Auto-map client columns to Shopify product CSV fields (new format)."""

from __future__ import annotations

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
        h = (header or "").strip().lower()
        if not h:
            return None

        rules: list[tuple[list[str], str]] = [
            (["title", "name", "product"], "Title"),
            (["handle", "slug", "url handle"], "URL handle"),
            (["compare", "original", "was", "old price", "compare-at"], "Compare-at price"),
            (["price", "mrp", "rate"], "Price"),
            (["cost per", "cost"], "Cost per item"),
            (["sku", "code", "item no", "article"], "SKU"),
            (["qty", "stock", "inventory", "quantity"], "Inventory quantity"),
            (["desc", "detail", "about", "body", "info"], "Description"),
            (["vendor", "brand", "company", "manufacturer"], "Vendor"),
            (["tag", "keyword", "label"], "Tags"),
            (["type", "category", "collection"], "Type"),
            (["image", "photo", "img", "picture", "url"], "Product image URL"),
            (["weight", "gram", "kg"], "Weight value (grams)"),
            (["barcode", "ean", "upc", "isbn"], "Barcode"),
            (["seo title", "meta title"], "SEO title"),
            (["seo desc", "meta desc", "meta description"], "SEO description"),
            (["size"], "Option1 value"),
            (["color", "colour"], "Option2 value"),
            (["material", "flavor", "flavour", "scent", "variant"], "Option3 value"),
        ]

        for keywords, field in rules:
            if any(kw in h for kw in keywords):
                if field not in used_fields:
                    return field
                continue

        return None

    @staticmethod
    def dropdown_options() -> list[str]:
        return [SKIP_LABEL] + SHOPIFY_FIELDS
