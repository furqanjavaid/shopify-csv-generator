"""Generate Shopify-ready product import CSV files."""

from __future__ import annotations

import csv
import itertools
import re
from pathlib import Path
from typing import Any

from app.core.column_mapper import SKIP_LABEL
from app.utils.helpers import clean_value, slugify

SHOPIFY_COLUMNS = [
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

PRODUCT_FIELDS = {
    "Title",
    "Body (HTML)",
    "Vendor",
    "Product Category",
    "Type",
    "Tags",
    "Published",
    "Image Src",
    "Image Position",
    "Image Alt Text",
    "Gift Card",
    "SEO Title",
    "SEO Description",
    "Status",
}

DEFAULTS = {
    "Published": "true",
    "Status": "active",
    "Variant Inventory Tracker": "shopify",
    "Variant Inventory Policy": "deny",
    "Variant Fulfillment Service": "manual",
    "Variant Requires Shipping": "true",
    "Variant Taxable": "true",
    "Variant Weight Unit": "kg",
}


class ShopifyGenerator:
    """Build a Shopify products CSV from mapped client data."""

    def generate(
        self,
        parsed_data: dict[str, Any],
        mapping: list[dict],
        output_path: str,
    ) -> dict[str, Any]:
        field_map: dict[str, str] = {}
        for item in mapping:
            shopify_field = item.get("shopify_field")
            client_col = item.get("client_col")
            if not shopify_field or shopify_field == SKIP_LABEL:
                continue
            if shopify_field not in field_map:
                field_map[shopify_field] = client_col

        # Infer option names when values are mapped
        if "Option1 Value" in field_map and "Option1 Name" not in field_map:
            field_map["Option1 Name"] = "__OPTION1_NAME__"
        if "Option2 Value" in field_map and "Option2 Name" not in field_map:
            field_map["Option2 Name"] = "__OPTION2_NAME__"
        if "Option3 Value" in field_map and "Option3 Name" not in field_map:
            field_map["Option3 Name"] = "__OPTION3_NAME__"

        option_value_fields = [
            f for f in ("Option1 Value", "Option2 Value", "Option3 Value") if f in field_map
        ]

        seen_handles: dict[str, int] = {}
        output_rows: list[dict[str, str]] = []
        product_count = 0
        variant_count = 0

        for source_row in parsed_data.get("rows", []):
            title = self._get_val(source_row, field_map, "Title")
            if not title:
                continue

            handle = self._unique_handle(title, seen_handles, source_row, field_map)
            variants = self._expand_variants(source_row, field_map, option_value_fields)

            product_count += 1
            for index, variant in enumerate(variants):
                variant_count += 1
                row = {col: "" for col in SHOPIFY_COLUMNS}
                row["Handle"] = handle

                if index == 0:
                    for field in PRODUCT_FIELDS:
                        row[field] = self._get_val(source_row, field_map, field)
                    row["Title"] = title
                    # Option names on first row
                    row["Option1 Name"] = self._option_name(
                        source_row, field_map, 1, "Size"
                    )
                    row["Option2 Name"] = self._option_name(
                        source_row, field_map, 2, "Color"
                    )
                    row["Option3 Name"] = self._option_name(
                        source_row, field_map, 3, "Option"
                    )
                else:
                    # Subsequent variant rows: only handle + variant fields
                    if "Option1 Value" in variant:
                        row["Option1 Name"] = self._option_name(
                            source_row, field_map, 1, "Size"
                        )
                    if "Option2 Value" in variant:
                        row["Option2 Name"] = self._option_name(
                            source_row, field_map, 2, "Color"
                        )
                    if "Option3 Value" in variant:
                        row["Option3 Name"] = self._option_name(
                            source_row, field_map, 3, "Option"
                        )

                # Variant-specific mapped fields
                for field in SHOPIFY_COLUMNS:
                    if field in PRODUCT_FIELDS or field in {
                        "Handle",
                        "Option1 Name",
                        "Option2 Name",
                        "Option3 Name",
                    }:
                        continue
                    if field in variant:
                        row[field] = variant[field]
                    else:
                        row[field] = self._get_val(source_row, field_map, field)

                for key, default in DEFAULTS.items():
                    if not row.get(key):
                        row[key] = default

                if row.get("Image Src") and not row.get("Image Position"):
                    row["Image Position"] = "1"

                # Ensure option names blank if no option values
                for n in (1, 2, 3):
                    if not row.get(f"Option{n} Value"):
                        row[f"Option{n} Name"] = ""

                output_rows.append(row)

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=SHOPIFY_COLUMNS,
                quoting=csv.QUOTE_MINIMAL,
            )
            writer.writeheader()
            writer.writerows(output_rows)

        return {
            "products": product_count,
            "variants": variant_count,
            "rows": len(output_rows),
            "output_path": str(path.resolve()),
        }

    def _get_val(
        self, row: dict, field_map: dict[str, str], shopify_field: str
    ) -> str:
        client_col = field_map.get(shopify_field)
        if not client_col:
            return ""
        if client_col == "__OPTION1_NAME__":
            return "Size"
        if client_col == "__OPTION2_NAME__":
            return "Color"
        if client_col == "__OPTION3_NAME__":
            return "Option"
        return clean_value(row.get(client_col, ""))

    def _unique_handle(
        self,
        title: str,
        seen_handles: dict[str, int],
        row: dict,
        field_map: dict[str, str],
    ) -> str:
        mapped = self._get_val(row, field_map, "Handle")
        base = slugify(mapped) if mapped else slugify(title)
        if not base:
            base = "product"
        if base not in seen_handles:
            seen_handles[base] = 1
            return base
        seen_handles[base] += 1
        return f"{base}-{seen_handles[base]}"

    def _option_name(
        self, row: dict, field_map: dict[str, str], index: int, default: str
    ) -> str:
        value_field = f"Option{index} Value"
        name_field = f"Option{index} Name"
        if value_field not in field_map and name_field not in field_map:
            return ""
        name = self._get_val(row, field_map, name_field)
        return name or default

    def _expand_variants(
        self,
        source_row: dict,
        field_map: dict[str, str],
        option_value_fields: list[str],
    ) -> list[dict[str, str]]:
        if not option_value_fields:
            return [{}]

        option_lists: list[list[tuple[str, str]]] = []
        for field in option_value_fields:
            raw = self._get_val(source_row, field_map, field)
            values = self._split_option_values(raw)
            if not values:
                values = [""]
            option_lists.append([(field, v) for v in values])

        variants: list[dict[str, str]] = []
        for combo in itertools.product(*option_lists):
            variant = {field: value for field, value in combo}
            variants.append(variant)
        return variants or [{}]

    @staticmethod
    def _split_option_values(raw: str) -> list[str]:
        if not raw:
            return []
        parts = re.split(r"[,;]", raw)
        return [p.strip() for p in parts if p.strip()]
