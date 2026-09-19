"""Auto-map client columns to Shopify product CSV fields."""

from __future__ import annotations

from typing import Optional

# Exact Shopify product CSV field order (36 fields)
SHOPIFY_FIELDS = [
    "Handle",
    "Title",
    "Body (HTML)",
    "Vendor",
    "Product Category",
    "Type",
    "Tags",
    "Published",
    "Option1 Name",
    "Option1 Value",
    "Option2 Name",
    "Option2 Value",
    "Option3 Name",
    "Option3 Value",
    "Variant SKU",
    "Variant Grams",
    "Variant Inventory Tracker",
    "Variant Inventory Qty",
    "Variant Inventory Policy",
    "Variant Fulfillment Service",
    "Variant Price",
    "Variant Compare At Price",
    "Variant Requires Shipping",
    "Variant Taxable",
    "Variant Barcode",
    "Image Src",
    "Image Position",
    "Image Alt Text",
    "Gift Card",
    "SEO Title",
    "SEO Description",
    "Variant Image",
    "Variant Weight Unit",
    "Variant Tax Code",
    "Cost per item",
    "Status",
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

        # If size/color mapped, ensure option names are set via synthetic extras
        # (handled in generator when Option values exist without names)
        return results

    def _match_header(self, header: str, used_fields: set[str]) -> Optional[str]:
        h = (header or "").strip().lower()
        if not h:
            return None

        rules: list[tuple[list[str], str]] = [
            (["title", "name", "product"], "Title"),
            (["compare", "original", "was", "old price"], "Variant Compare At Price"),
            (["price", "mrp", "cost", "rate"], "Variant Price"),
            (["sku", "code", "item no", "article"], "Variant SKU"),
            (["qty", "stock", "inventory", "quantity"], "Variant Inventory Qty"),
            (["desc", "detail", "about", "body", "info"], "Body (HTML)"),
            (["vendor", "brand", "company", "manufacturer"], "Vendor"),
            (["tag", "keyword", "label"], "Tags"),
            (["type", "category", "collection"], "Type"),
            (["image", "photo", "img", "picture", "url"], "Image Src"),
            (["weight", "gram", "kg"], "Variant Grams"),
            (["barcode", "ean", "upc", "isbn"], "Variant Barcode"),
            (["size"], "Option1 Value"),
            (["color", "colour"], "Option2 Value"),
            (["material", "flavor", "flavour", "scent", "variant"], "Option3 Value"),
        ]

        for keywords, field in rules:
            if any(kw in h for kw in keywords):
                if field not in used_fields:
                    return field
                # Prefer first unique match; skip if already taken
                continue

        return None

    @staticmethod
    def dropdown_options() -> list[str]:
        return [SKIP_LABEL] + SHOPIFY_FIELDS
