"""Duplicate SKU policy + Shopify CSV pre-import validation."""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sentivo_extractor.core.shopify_csv_exporter import SHOPIFY_COLUMNS

ALLOWED_STATUS = {"active", "draft", "archived"}


def apply_duplicate_sku_policy(
    products: list[dict[str, Any]],
    policy: str = "warn",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """
    Apply duplicate Variant SKU policy across products.
    Returns (kept_products, failed_products, warnings).
    Policies: warn | suffix | blank | fail
    """
    policy = (policy or "warn").lower()
    sku_owners: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for pi, product in enumerate(products):
        for vi, variant in enumerate(product.get("variants") or []):
            sku = str(variant.get("sku") or "").strip()
            if sku:
                sku_owners[sku].append((pi, vi))

    dup_skus = {sku for sku, locs in sku_owners.items() if len(locs) > 1}
    warnings: list[str] = []
    failed: list[dict[str, Any]] = []
    kept = list(products)

    if not dup_skus:
        return kept, failed, warnings

    if policy == "warn":
        for sku in sorted(dup_skus):
            warnings.append(f"Duplicate SKU '{sku}' appears {len(sku_owners[sku])} times")
        return kept, failed, warnings

    if policy == "fail":
        fail_indexes = {pi for sku in dup_skus for pi, _vi in sku_owners[sku]}
        new_kept = []
        for i, p in enumerate(products):
            if i in fail_indexes:
                p = dict(p)
                p["_fail_reason"] = "duplicate_sku"
                failed.append(p)
            else:
                new_kept.append(p)
        for sku in sorted(dup_skus):
            warnings.append(f"FAIL policy: duplicate SKU '{sku}'")
        return new_kept, failed, warnings

    # Mutating policies: suffix / blank
    seen_count: dict[str, int] = defaultdict(int)
    for product in kept:
        for variant in product.get("variants") or []:
            sku = str(variant.get("sku") or "").strip()
            if not sku:
                continue
            seen_count[sku] += 1
            if sku not in dup_skus:
                continue
            if seen_count[sku] == 1:
                continue  # keep first
            if policy == "blank":
                warnings.append(f"Blanked duplicate SKU '{sku}'")
                variant["sku"] = ""
            elif policy == "suffix":
                new_sku = f"{sku}-{seen_count[sku]}"
                warnings.append(f"Suffixed duplicate SKU '{sku}' → '{new_sku}'")
                variant["sku"] = new_sku
    return kept, failed, warnings


def _is_absolute_http(url: str) -> bool:
    try:
        p = urlparse(url or "")
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def _is_numeric(val: str) -> bool:
    if val is None or str(val).strip() == "":
        return False
    try:
        float(str(val).replace(",", ""))
        return True
    except ValueError:
        return False


def validate_shopify_csv(
    csv_path: Path,
    *,
    allow_duplicate_sku: bool = True,
) -> dict[str, Any]:
    """
    Validate exported Shopify CSV. Returns issues + summary.
    """
    issues: list[dict[str, Any]] = []
    path = Path(csv_path)
    if not path.exists():
        return {
            "issues": [
                {
                    "severity": "error",
                    "code": "missing_csv",
                    "handle": "",
                    "message": f"CSV not found: {path}",
                }
            ],
            "summary": {"errors": 1, "warnings": 0, "rows": 0},
        }

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)

    for col in SHOPIFY_COLUMNS:
        if col not in headers:
            issues.append(
                {
                    "severity": "error",
                    "code": "missing_column",
                    "handle": "",
                    "message": f"Required column missing: {col}",
                }
            )

    # Group by handle
    by_handle: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_handle[str(row.get("Handle") or "").strip()].append(row)

    sku_seen: dict[str, str] = {}
    for handle, group in by_handle.items():
        if not handle:
            issues.append(
                {
                    "severity": "error",
                    "code": "empty_handle",
                    "handle": "",
                    "message": "Row(s) with empty Handle",
                }
            )
            continue

        first = group[0]
        title = (first.get("Title") or "").strip()
        # Title required on first product row (may be blank on image-only rows)
        has_variant_row = any((r.get("Option1 Value") or r.get("Variant Price")) for r in group)
        if has_variant_row and not title:
            # title might be only on first row — check any
            if not any((r.get("Title") or "").strip() for r in group):
                issues.append(
                    {
                        "severity": "error",
                        "code": "empty_title",
                        "handle": handle,
                        "message": "Title is empty",
                    }
                )

        status_vals = {
            (r.get("Status") or "").strip().lower()
            for r in group
            if (r.get("Status") or "").strip()
        }
        for st in status_vals:
            if st not in ALLOWED_STATUS:
                issues.append(
                    {
                        "severity": "error",
                        "code": "invalid_status",
                        "handle": handle,
                        "message": f"Invalid status '{st}' (expected active/draft/archived)",
                    }
                )

        # Option dimensions <= 3 already by columns; check names/values
        option_names = [
            (first.get("Option1 Name") or "").strip(),
            (first.get("Option2 Name") or "").strip(),
            (first.get("Option3 Name") or "").strip(),
        ]
        combos: set[tuple[str, str, str]] = set()
        image_srcs = {
            (r.get("Image Src") or "").strip()
            for r in group
            if (r.get("Image Src") or "").strip()
        }
        variant_rows = [
            r
            for r in group
            if (r.get("Option1 Value") or "").strip()
            or (r.get("Variant Price") or "").strip()
            or (r.get("Variant SKU") or "").strip()
        ]

        if variant_rows and not option_names[0]:
            # Allow Default Title style when Option1 Value set
            if any((r.get("Option1 Value") or "").strip() for r in variant_rows):
                issues.append(
                    {
                        "severity": "warning",
                        "code": "missing_option1_name",
                        "handle": handle,
                        "message": "Option1 Name missing while Option1 Value present",
                    }
                )

        for r in variant_rows:
            o1 = (r.get("Option1 Value") or "").strip()
            o2 = (r.get("Option2 Value") or "").strip()
            o3 = (r.get("Option3 Value") or "").strip()
            key = (o1, o2, o3)
            if key in combos:
                issues.append(
                    {
                        "severity": "error",
                        "code": "duplicate_variant_combo",
                        "handle": handle,
                        "message": f"Duplicate option combination {key}",
                    }
                )
            combos.add(key)

            if not o1 and (o2 or o3 or (r.get("Variant Price") or "").strip()):
                issues.append(
                    {
                        "severity": "error",
                        "code": "empty_variant_row",
                        "handle": handle,
                        "message": "Variant row missing Option1 Value",
                    }
                )

            price = (r.get("Variant Price") or "").strip()
            compare = (r.get("Variant Compare At Price") or "").strip()
            if price and not _is_numeric(price):
                issues.append(
                    {
                        "severity": "error",
                        "code": "non_numeric_price",
                        "handle": handle,
                        "message": f"Non-numeric price: {price}",
                    }
                )
            if compare:
                if not _is_numeric(compare):
                    issues.append(
                        {
                            "severity": "error",
                            "code": "non_numeric_compare",
                            "handle": handle,
                            "message": f"Non-numeric compare-at: {compare}",
                        }
                    )
                elif price and _is_numeric(price):
                    try:
                        if float(compare.replace(",", "")) < float(price.replace(",", "")):
                            issues.append(
                                {
                                    "severity": "error",
                                    "code": "compare_lt_price",
                                    "handle": handle,
                                    "message": f"Compare-at {compare} < price {price}",
                                }
                            )
                    except ValueError:
                        pass

            sku = (r.get("Variant SKU") or "").strip()
            if sku:
                if sku in sku_seen and sku_seen[sku] != handle:
                    severity = "warning" if allow_duplicate_sku else "error"
                    issues.append(
                        {
                            "severity": severity,
                            "code": "duplicate_sku",
                            "handle": handle,
                            "message": f"SKU '{sku}' also used by handle '{sku_seen[sku]}'",
                        }
                    )
                else:
                    sku_seen[sku] = handle

            img = (r.get("Image Src") or "").strip()
            if img and not _is_absolute_http(img):
                issues.append(
                    {
                        "severity": "error",
                        "code": "relative_image_url",
                        "handle": handle,
                        "message": f"Image Src not absolute: {img}",
                    }
                )
            vimg = (r.get("Variant Image") or "").strip()
            if vimg:
                if not _is_absolute_http(vimg):
                    issues.append(
                        {
                            "severity": "error",
                            "code": "relative_variant_image",
                            "handle": handle,
                            "message": f"Variant Image not absolute: {vimg}",
                        }
                    )
                elif image_srcs and vimg not in image_srcs:
                    issues.append(
                        {
                            "severity": "warning",
                            "code": "variant_image_not_in_list",
                            "handle": handle,
                            "message": "Variant Image not present in product Image Src list",
                        }
                    )

        # Extra image-only rows
        for r in group:
            img = (r.get("Image Src") or "").strip()
            if img and not _is_absolute_http(img):
                issues.append(
                    {
                        "severity": "error",
                        "code": "relative_image_url",
                        "handle": handle,
                        "message": f"Image Src not absolute: {img}",
                    }
                )

    errors = sum(1 for i in issues if i["severity"] == "error")
    warnings = sum(1 for i in issues if i["severity"] == "warning")
    return {
        "issues": issues,
        "summary": {"errors": errors, "warnings": warnings, "rows": len(rows)},
    }


def write_preimport_validation_report(result: dict[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["severity", "code", "handle", "message"]
    try:
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Pre-Import Validation"
        ws.append(fields)
        for issue in result.get("issues") or []:
            ws.append([issue.get(f, "") for f in fields])
        summary = result.get("summary") or {}
        ws2 = wb.create_sheet("Summary")
        ws2.append(["metric", "value"])
        for k, v in summary.items():
            ws2.append([k, v])
        wb.save(path)
    except Exception:
        import csv as csvlib

        csv_path = path.with_suffix(".csv")
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csvlib.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for issue in result.get("issues") or []:
                writer.writerow({k: issue.get(k, "") for k in fields})
        return csv_path
    return path
