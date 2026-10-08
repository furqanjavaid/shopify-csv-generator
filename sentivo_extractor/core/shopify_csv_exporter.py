"""Export normalized products to Shopify-compatible CSV (+ reports)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sentivo_extractor.core.utils import is_blank_price, normalize_price

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
    "Variant Barcode",
    "Variant Grams",
    "Variant Inventory Tracker",
    "Variant Inventory Qty",
    "Variant Inventory Policy",
    "Variant Fulfillment Service",
    "Variant Price",
    "Variant Compare At Price",
    "Variant Requires Shipping",
    "Variant Taxable",
    "Image Src",
    "Image Position",
    "Image Alt Text",
    "Gift Card",
    "Variant Image",
    "Status",
]


def _option_names(product: dict[str, Any]) -> list[str]:
    names = [(o.get("name") or "") for o in (product.get("options") or [])[:3]]
    while len(names) < 3:
        names.append("")
    if not names[0] and product.get("variants"):
        names[0] = "Title"
    return names


def product_to_rows(product: dict[str, Any]) -> list[dict[str, str]]:
    """Expand one product into Shopify CSV rows (variants + image rows)."""
    handle = product.get("handle") or ""
    names = _option_names(product)
    variants = product.get("variants") or [ {} ]
    images = product.get("images") or []
    tags = product.get("tags") or []
    if isinstance(tags, list):
        tags_str = ", ".join(str(t) for t in tags)
    else:
        tags_str = str(tags)

    rows: list[dict[str, str]] = []
    status = (product.get("status") or "active").capitalize()
    if status.lower() == "active":
        status = "active"

    for idx, variant in enumerate(variants):
        is_first = idx == 0
        row = {col: "" for col in SHOPIFY_COLUMNS}
        row["Handle"] = handle
        if is_first:
            row["Title"] = product.get("title") or ""
            row["Body (HTML)"] = product.get("description_html") or ""
            row["Vendor"] = product.get("vendor") or product.get("brand") or ""
            row["Product Category"] = product.get("department") or ""
            row["Type"] = product.get("product_type") or ""
            row["Tags"] = tags_str
            row["Published"] = "TRUE"
            row["Option1 Name"] = names[0]
            row["Option2 Name"] = names[1]
            row["Option3 Name"] = names[2]
            row["Status"] = status
            if images:
                row["Image Src"] = images[0].get("src") or ""
                row["Image Position"] = str(images[0].get("position") or 1)
                row["Image Alt Text"] = images[0].get("alt") or ""
        row["Option1 Value"] = str(variant.get("option1") or "")
        row["Option2 Value"] = str(variant.get("option2") or "")
        row["Option3 Value"] = str(variant.get("option3") or "")
        row["Variant SKU"] = str(variant.get("sku") or "")
        row["Variant Barcode"] = str(variant.get("barcode") or "")
        row["Variant Grams"] = str(variant.get("weight_grams") or "")
        row["Variant Inventory Tracker"] = "shopify"
        row["Variant Inventory Qty"] = str(variant.get("inventory_qty") or "")
        row["Variant Inventory Policy"] = "deny"
        row["Variant Fulfillment Service"] = "manual"
        row["Variant Price"] = (
            ""
            if is_blank_price(variant.get("price"))
            else normalize_price(variant.get("price"))
        )
        row["Variant Compare At Price"] = (
            ""
            if is_blank_price(variant.get("compare_at_price"))
            else normalize_price(variant.get("compare_at_price"))
        )
        row["Variant Requires Shipping"] = "TRUE"
        row["Variant Taxable"] = "TRUE"
        row["Gift Card"] = "FALSE"
        row["Variant Image"] = str(variant.get("variant_image") or "")
        if not is_first:
            row["Status"] = ""
        rows.append(row)

    # Extra image rows (position 2+)
    for img in images[1:]:
        row = {col: "" for col in SHOPIFY_COLUMNS}
        row["Handle"] = handle
        row["Image Src"] = img.get("src") or ""
        row["Image Position"] = str(img.get("position") or "")
        row["Image Alt Text"] = img.get("alt") or ""
        rows.append(row)

    return rows


def export_shopify_csv(products: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SHOPIFY_COLUMNS)
        writer.writeheader()
        for product in products:
            for row in product_to_rows(product):
                writer.writerow(row)
    return path


def export_failed_csv(products: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "source_url",
        "title",
        "handle",
        "extraction_method",
        "confidence_score",
        "reason",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for p in products:
            writer.writerow(
                {
                    "source_url": p.get("source_url") or "",
                    "title": p.get("title") or "",
                    "handle": p.get("handle") or "",
                    "extraction_method": p.get("extraction_method") or "",
                    "confidence_score": p.get("confidence_score") or "",
                    "reason": p.get("_fail_reason") or "validation_failed",
                }
            )
    return path


# Re-feedable Magento retry CSV (input-compatible: url column).
MAGENTO_FAILED_COLUMNS = ["URL", "Fail Reason", "Confidence Score"]


def export_magento_failed_csv(rows: list[dict[str, Any]], path: Path) -> Path:
    """
    Write {domain}_failed.csv for Magento retry runs.

    Columns match the requested report; ``URL`` lowercases to ``url`` for
    read_seed_csv re-feed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=MAGENTO_FAILED_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "URL": row.get("URL") or row.get("url") or "",
                    "Fail Reason": row.get("Fail Reason")
                    or row.get("fail_reason")
                    or "",
                    "Confidence Score": row.get("Confidence Score")
                    if row.get("Confidence Score") is not None
                    else row.get("confidence_score", ""),
                }
            )
    return path


def append_shopify_product_csv(product: dict[str, Any], path: Path) -> Path:
    """Append one product's Shopify rows to CSV (create + header if missing)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SHOPIFY_COLUMNS)
        if write_header:
            writer.writeheader()
        for row in product_to_rows(product):
            writer.writerow(row)
    return path


def export_validation_report(issues: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Validation"
        headers = ["severity", "code", "handle", "title", "source_url", "message"]
        ws.append(headers)
        for issue in issues:
            ws.append([issue.get(h, "") for h in headers])
        wb.save(path)
    except Exception:
        # Fallback CSV if openpyxl unavailable
        csv_path = path.with_suffix(".csv")
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["severity", "code", "handle", "title", "source_url", "message"],
            )
            writer.writeheader()
            for issue in issues:
                writer.writerow(issue)
        return csv_path
    return path


def export_images_manifest(manifest_rows: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "handle",
        "position",
        "src",
        "local_path",
        "content_hash",
        "converted_from",
        "status",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in manifest_rows:
            writer.writerow({k: row.get(k, "") for k in fields})
    return path
