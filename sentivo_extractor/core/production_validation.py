"""Production Validation Mode — per-product field checks and final report."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

PRODUCTION_FIELD_LABELS = (
    "title",
    "price",
    "images",
    "variants",
    "description",
    "sku",
)

STATUS_SUCCESS = "Successful"
STATUS_RECOVERED = "Recovered After Retry"
STATUS_FAILED = "Failed"

STRICT_SUCCESS_RATE_MIN = 95.0


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


def _product_price(product: dict[str, Any]) -> str:
    for v in product.get("variants") or []:
        p = str(v.get("price") or "").strip()
        if p:
            return p
    return str(product.get("price") or "").strip()


def _product_sku(product: dict[str, Any]) -> str:
    for v in product.get("variants") or []:
        sku = str(v.get("sku") or "").strip()
        if sku:
            return sku
    return str(product.get("sku") or "").strip()


def assess_production_fields(product: dict[str, Any]) -> dict[str, Any]:
    """
    Validate required production fields on a normalized product.
    Returns {missing: [...], fields: {name: ok|missing}, failure_reason: str}.
    """
    missing: list[str] = []
    fields: dict[str, str] = {}

    title = str(product.get("title") or "").strip()
    fields["title"] = "ok" if title else "missing"
    if not title:
        missing.append("title")

    price = _product_price(product)
    fields["price"] = "ok" if price else "missing"
    if not price:
        missing.append("price")

    images = product.get("images") or []
    has_image = any(
        str(img.get("src") or "").strip().startswith("http") for img in images if isinstance(img, dict)
    )
    fields["images"] = "ok" if has_image else "missing"
    if not has_image:
        missing.append("images")

    variants = product.get("variants") or []
    fields["variants"] = "ok" if variants else "missing"
    if not variants:
        missing.append("variants")

    desc = _strip_html(str(product.get("description_html") or "")).strip()
    fields["description"] = "ok" if len(desc) >= 20 else "missing"
    if len(desc) < 20:
        missing.append("description")

    sku = _product_sku(product)
    fields["sku"] = "ok" if sku else "missing"
    if not sku:
        missing.append("sku")

    reason = ""
    if missing:
        reason = f"missing_production_fields: {', '.join(missing)}"

    return {
        "missing": missing,
        "fields": fields,
        "failure_reason": reason,
    }


def validation_row(
    *,
    url: str,
    product: dict[str, Any] | None,
    status: str,
    retry_count: int,
    failure_reason: str = "",
    field_status: dict[str, str] | None = None,
) -> dict[str, Any]:
    field_status = field_status or {}
    p = product or {}
    desc_text = _strip_html(str(p.get("description_html") or "")).strip()
    return {
        "URL": url,
        "Title": str(p.get("title") or ""),
        "Price": _product_price(p) if p else "",
        "Images": len(p.get("images") or []) if p else 0,
        "Variants": len(p.get("variants") or []) if p else 0,
        "SKU": _product_sku(p) if p else "",
        "Description": "ok" if len(desc_text) >= 20 else "missing",
        "Status": status,
        "Confidence": p.get("confidence_score") if p else "",
        "Retry Count": retry_count,
        "Failure Reason": failure_reason,
        "_field_title": field_status.get("title", ""),
        "_field_price": field_status.get("price", ""),
        "_field_images": field_status.get("images", ""),
        "_field_variants": field_status.get("variants", ""),
        "_field_description": field_status.get("description", ""),
        "_field_sku": field_status.get("sku", ""),
    }


def build_validation_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    successful = sum(1 for r in rows if r.get("Status") == STATUS_SUCCESS)
    recovered = sum(1 for r in rows if r.get("Status") == STATUS_RECOVERED)
    failed = sum(1 for r in rows if r.get("Status") == STATUS_FAILED)
    ok = successful + recovered
    rate = round((ok / total) * 100.0, 2) if total else 0.0
    return {
        "Total Products": total,
        "Successful": successful,
        "Recovered After Retry": recovered,
        "Failed": failed,
        "Success Rate %": rate,
    }


def strict_validation_passed(summary: dict[str, Any]) -> bool:
    total = int(summary.get("Total Products") or 0)
    if total == 0:
        return False
    rate = float(summary.get("Success Rate %") or 0)
    return rate >= STRICT_SUCCESS_RATE_MIN


REPORT_COLUMNS = [
    "URL",
    "Title",
    "Price",
    "Images",
    "Variants",
    "SKU",
    "Description",
    "Status",
    "Confidence",
    "Retry Count",
    "Failure Reason",
]


def write_final_validation_report(
    path: Path,
    rows: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = summary or build_validation_summary(rows)

    try:
        import openpyxl

        wb = openpyxl.Workbook()
        ws_sum = wb.active
        ws_sum.title = "Summary"
        ws_sum.append(["metric", "value"])
        for key in (
            "Total Products",
            "Successful",
            "Recovered After Retry",
            "Failed",
            "Success Rate %",
        ):
            ws_sum.append([key, summary.get(key, "")])

        ws = wb.create_sheet("Products")
        ws.append(REPORT_COLUMNS)
        for row in rows:
            ws.append([row.get(c, "") for c in REPORT_COLUMNS])
        wb.save(path)
        wb.close()
        return path
    except Exception:
        import csv
        import json

        fallback = path.with_suffix(".json")
        fallback.write_text(
            json.dumps({"summary": summary, "products": rows}, indent=2, default=str),
            encoding="utf-8",
        )
        csv_path = path.with_suffix(".csv")
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({c: row.get(c, "") for c in REPORT_COLUMNS})
        return csv_path
