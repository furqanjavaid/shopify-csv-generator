"""Generate Shopify-ready product import CSV files (new column format)."""

from __future__ import annotations

import csv
import itertools
import re
from pathlib import Path
from typing import Any

from app.core.column_mapper import SKIP_LABEL
from app.utils.helpers import clean_value, slugify

NEW_SHOPIFY_COLUMNS = [
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

# Alias for callers / tests that still import SHOPIFY_COLUMNS
SHOPIFY_COLUMNS = NEW_SHOPIFY_COLUMNS

# Product-level fields: filled on first variant row only
PRODUCT_FIELDS = {
    "Title",
    "Description",
    "Vendor",
    "Product category",
    "Type",
    "Tags",
    "Published on online store",
    "Status",
    "Product image URL",
    "Image position",
    "Image alt text",
    "Gift card",
    "SEO title",
    "SEO description",
}

OPTION_NAME_FIELDS = {
    "Option1 name",
    "Option2 name",
    "Option3 name",
}

DEFAULTS = {
    "Published on online store": "TRUE",
    "Status": "Active",
    "Inventory tracker": "shopify",
    "Continue selling when out of stock": "DENY",
    "Fulfillment service": "manual",
    "Requires shipping": "TRUE",
    "Charge tax": "TRUE",
    "Weight unit for display": "g",
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
        if "Option1 value" in field_map and "Option1 name" not in field_map:
            field_map["Option1 name"] = "__OPTION1_NAME__"
        if "Option2 value" in field_map and "Option2 name" not in field_map:
            field_map["Option2 name"] = "__OPTION2_NAME__"
        if "Option3 value" in field_map and "Option3 name" not in field_map:
            field_map["Option3 name"] = "__OPTION3_NAME__"

        option_value_fields = [
            f
            for f in ("Option1 value", "Option2 value", "Option3 value")
            if f in field_map
        ]

        seen_handles: dict[str, int] = {}
        output_rows: list[dict[str, str]] = []
        product_count = 0
        variant_count = 0
        active_handle: str | None = None
        active_option_names: dict[int, str] = {}

        for source_row in parsed_data.get("rows", []):
            title = self._get_val(source_row, field_map, "Title")

            # Continuation variant rows (from collection crawler): empty Title,
            # same URL handle — attach under the active product.
            if not title:
                if not active_handle:
                    continue
                variant_count += 1
                row = self._build_continuation_row(
                    source_row, field_map, active_handle, active_option_names
                )
                output_rows.append(row)
                continue

            handle = self._unique_handle(title, seen_handles, source_row, field_map)
            active_handle = handle
            variants = self._expand_variants(source_row, field_map, option_value_fields)

            product_count += 1
            for index, variant in enumerate(variants):
                variant_count += 1
                row = {col: "" for col in NEW_SHOPIFY_COLUMNS}
                row["URL handle"] = handle

                if index == 0:
                    for field in PRODUCT_FIELDS:
                        if field == "Title":
                            row["Title"] = title
                        elif field == "Image position":
                            continue  # set below from image URL
                        else:
                            row[field] = self._get_val(source_row, field_map, field)

                    row["Option1 name"] = self._option_name(
                        source_row, field_map, 1, "Size"
                    )
                    row["Option2 name"] = self._option_name(
                        source_row, field_map, 2, "Color"
                    )
                    row["Option3 name"] = self._option_name(
                        source_row, field_map, 3, "Option"
                    )
                    active_option_names = {
                        1: row["Option1 name"],
                        2: row["Option2 name"],
                        3: row["Option3 name"],
                    }

                    image_url = row.get("Product image URL") or self._get_val(
                        source_row, field_map, "Product image URL"
                    )
                    row["Product image URL"] = self._absolute_image_url(image_url)
                    row["Image position"] = "1" if row["Product image URL"] else ""
                else:
                    # Expanded variants from comma-split options on same source row
                    row["Title"] = ""
                    if "Option1 value" in variant or "Option1 value" in field_map:
                        row["Option1 name"] = active_option_names.get(1) or self._option_name(
                            source_row, field_map, 1, "Size"
                        )
                    if "Option2 value" in variant or "Option2 value" in field_map:
                        row["Option2 name"] = active_option_names.get(2) or self._option_name(
                            source_row, field_map, 2, "Color"
                        )
                    if "Option3 value" in variant or "Option3 value" in field_map:
                        row["Option3 name"] = active_option_names.get(3) or self._option_name(
                            source_row, field_map, 3, "Option"
                        )

                # Variant-specific fields (all rows)
                for field in NEW_SHOPIFY_COLUMNS:
                    if field in PRODUCT_FIELDS or field in {
                        "URL handle",
                        *OPTION_NAME_FIELDS,
                    }:
                        continue
                    if field in variant:
                        row[field] = variant[field]
                    elif not row.get(field):
                        row[field] = self._get_val(source_row, field_map, field)

                if row.get("Product image URL"):
                    row["Product image URL"] = self._absolute_image_url(
                        row["Product image URL"]
                    )
                if row.get("Variant image URL"):
                    row["Variant image URL"] = self._absolute_image_url(
                        row["Variant image URL"]
                    )

                self._apply_defaults_and_normalize(row, is_first=(index == 0))

                for n in (1, 2, 3):
                    if not row.get(f"Option{n} value"):
                        row[f"Option{n} name"] = ""
                        row[f"Option{n} Linked To"] = ""

                output_rows.append(row)

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=NEW_SHOPIFY_COLUMNS,
                quoting=csv.QUOTE_ALL,
            )
            writer.writeheader()
            writer.writerows(output_rows)

        return {
            "products": product_count,
            "variants": variant_count,
            "rows": len(output_rows),
            "output_path": str(path.resolve()),
        }

    def _build_continuation_row(
        self,
        source_row: dict,
        field_map: dict[str, str],
        handle: str,
        active_option_names: dict[int, str],
    ) -> dict[str, str]:
        """Build a variant-only row under an existing product handle."""
        row = {col: "" for col in NEW_SHOPIFY_COLUMNS}
        row["URL handle"] = handle

        # Variant fields allowed on subsequent rows
        variant_fields = [
            "SKU",
            "Barcode",
            "Price",
            "Compare-at price",
            "Inventory quantity",
            "Option1 value",
            "Option2 value",
            "Option3 value",
            "Weight value (grams)",
            "Requires shipping",
            "Fulfillment service",
            "Inventory tracker",
            "Continue selling when out of stock",
            "Charge tax",
            "Status",
            "Variant image URL",
            "Cost per item",
            "Tax code",
            "Weight unit for display",
        ]
        for field in variant_fields:
            row[field] = self._get_val(source_row, field_map, field)

        # Carry option names from the first row when values are present
        for n in (1, 2, 3):
            if row.get(f"Option{n} value"):
                row[f"Option{n} name"] = active_option_names.get(n, "")

        if row.get("Variant image URL"):
            row["Variant image URL"] = self._absolute_image_url(row["Variant image URL"])

        self._apply_defaults_and_normalize(row, is_first=False)

        for n in (1, 2, 3):
            if not row.get(f"Option{n} value"):
                row[f"Option{n} name"] = ""
                row[f"Option{n} Linked To"] = ""

        return row

    @staticmethod
    def _absolute_image_url(url: str) -> str:
        url = (url or "").strip()
        if url.startswith("//"):
            return "https:" + url
        return url

    def _apply_defaults_and_normalize(self, row: dict[str, str], is_first: bool) -> None:
        """Fill defaults and normalize Shopify boolean/status casing."""
        for key, default in DEFAULTS.items():
            # Product-level defaults only on first row (except Status — kept on variants)
            if key in PRODUCT_FIELDS and key != "Status" and not is_first:
                continue
            if not row.get(key):
                row[key] = default

        if is_first:
            row["Published on online store"] = self._as_true_false(
                row.get("Published on online store"), default="TRUE"
            )
            row["Status"] = self._as_status(row.get("Status"))
            if row.get("Product image URL"):
                row["Product image URL"] = self._absolute_image_url(
                    row["Product image URL"]
                )
                row["Image position"] = "1"
            else:
                row["Image position"] = ""
        else:
            row["Image position"] = ""
            row["Published on online store"] = ""
            row["Status"] = self._as_status(row.get("Status") or "Active")

        row["Requires shipping"] = self._as_true_false(
            row.get("Requires shipping"), default="TRUE"
        )
        row["Charge tax"] = self._as_true_false(row.get("Charge tax"), default="TRUE")
        row["Continue selling when out of stock"] = self._as_inventory_policy(
            row.get("Continue selling when out of stock")
        )

        if not row.get("Inventory tracker"):
            row["Inventory tracker"] = "shopify"
        if not row.get("Fulfillment service"):
            row["Fulfillment service"] = "manual"
        if not row.get("Weight unit for display"):
            row["Weight unit for display"] = "g"

    @staticmethod
    def _as_true_false(value: str | None, default: str = "TRUE") -> str:
        text = (value or "").strip().lower()
        if not text:
            return default
        if text in {"false", "0", "no", "n", "off", "unpublished"}:
            return "FALSE"
        if text in {"true", "1", "yes", "y", "on", "published"}:
            return "TRUE"
        return default

    @staticmethod
    def _as_status(value: str | None) -> str:
        text = (value or "").strip().lower()
        if text in {"draft", "archived"}:
            return text.capitalize()
        return "Active"

    @staticmethod
    def _as_inventory_policy(value: str | None) -> str:
        text = (value or "").strip().lower()
        if "continue" in text:
            return "CONTINUE"
        return "DENY"

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
        mapped = self._get_val(row, field_map, "URL handle")
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
        value_field = f"Option{index} value"
        name_field = f"Option{index} name"
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
        # Never comma-split HTML (e.g. Description wrongly mapped to an option)
        if "<" in raw and ">" in raw:
            return [raw.strip()]
        parts = re.split(r"[,;]", raw)
        return [p.strip() for p in parts if p.strip()]
