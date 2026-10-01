"""Validate normalized products before Shopify export."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


def _issue(
    product: dict[str, Any],
    code: str,
    message: str,
    severity: str = SEVERITY_ERROR,
) -> dict[str, Any]:
    return {
        "handle": product.get("handle") or "",
        "title": product.get("title") or "",
        "source_url": product.get("source_url") or "",
        "code": code,
        "message": message,
        "severity": severity,
    }


def validate_products(products: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Return {
      issues: [...],
      failed: [products with blocking errors],
      passed: [products ok to export],
      summary: {...}
    }
    """
    issues: list[dict[str, Any]] = []
    handles: dict[str, int] = {}
    skus: dict[str, int] = {}
    currencies: set[str] = set()

    for p in products:
        handle = (p.get("handle") or "").strip()
        if handle:
            handles[handle] = handles.get(handle, 0) + 1
        for v in p.get("variants") or []:
            sku = (v.get("sku") or "").strip()
            if sku:
                skus[sku] = skus.get(sku, 0) + 1
        cur = (p.get("currency") or "").strip()
        if cur:
            currencies.add(cur)

    for p in products:
        title = (p.get("title") or "").strip()
        handle = (p.get("handle") or "").strip()
        desc = (p.get("description_html") or "").strip()
        images = p.get("images") or []
        variants = p.get("variants") or []
        options = p.get("options") or []

        if not title:
            issues.append(_issue(p, "missing_title", "Missing title"))
        if not images:
            issues.append(_issue(p, "missing_images", "Missing images", SEVERITY_WARNING))
        else:
            for img in images:
                src = img.get("src") or ""
                if not src or not urlparse(src).scheme.startswith("http"):
                    issues.append(
                        _issue(p, "broken_image_url", f"Broken/invalid image URL: {src}")
                    )

        if not desc or len(_strip_html(desc).strip()) < 20:
            issues.append(
                _issue(
                    p,
                    "suspicious_empty_description",
                    "Description missing or suspiciously short",
                    SEVERITY_WARNING,
                )
            )

        if handle and handles.get(handle, 0) > 1:
            issues.append(_issue(p, "duplicate_handle", f"Duplicate handle: {handle}"))

        if not variants:
            issues.append(_issue(p, "missing_variants", "No variants"))
        else:
            combo_seen: set[tuple[str, str, str]] = set()
            for v in variants:
                price = str(v.get("price") or "").strip()
                compare = str(v.get("compare_at_price") or "").strip()
                if not price:
                    issues.append(_issue(p, "missing_price", "Variant missing price"))
                if compare and price:
                    try:
                        if float(compare) < float(price):
                            issues.append(
                                _issue(
                                    p,
                                    "invalid_compare_at_price",
                                    f"Compare-at ({compare}) < price ({price})",
                                )
                            )
                    except ValueError:
                        issues.append(
                            _issue(
                                p,
                                "invalid_compare_at_price",
                                f"Non-numeric compare-at: {compare}",
                            )
                        )
                sku = (v.get("sku") or "").strip()
                if sku and skus.get(sku, 0) > 1:
                    issues.append(_issue(p, "duplicate_sku", f"Duplicate SKU: {sku}"))

                key = (
                    str(v.get("option1") or ""),
                    str(v.get("option2") or ""),
                    str(v.get("option3") or ""),
                )
                if key in combo_seen:
                    issues.append(
                        _issue(
                            p,
                            "duplicate_variant_combination",
                            f"Duplicate variant combo: {key}",
                        )
                    )
                combo_seen.add(key)

                for i, opt in enumerate(options[:3], start=1):
                    name = (opt.get("name") or "").strip()
                    val = str(v.get(f"option{i}") or "").strip()
                    if name and not val:
                        issues.append(
                            _issue(
                                p,
                                "missing_variant_option",
                                f"Missing Option{i} value for {name}",
                            )
                        )
                    if val and val != "Default Title" and not name:
                        issues.append(
                            _issue(
                                p,
                                "missing_variant_option",
                                f"Missing Option{i} name for value {val}",
                                SEVERITY_WARNING,
                            )
                        )

        if len(currencies) > 1 and (p.get("currency") or ""):
            issues.append(
                _issue(
                    p,
                    "currency_mismatch",
                    f"Mixed currencies in batch: {sorted(currencies)}",
                    SEVERITY_WARNING,
                )
            )

    failed: list[dict[str, Any]] = []
    passed: list[dict[str, Any]] = []
    for p in products:
        handle = (p.get("handle") or "").strip()
        title = (p.get("title") or "").strip()
        product_errors = [
            i
            for i in issues
            if i["severity"] == SEVERITY_ERROR
            and (
                (handle and i.get("handle") == handle)
                or (
                    not handle
                    and i.get("source_url")
                    and i.get("source_url") == p.get("source_url")
                )
            )
        ]
        if not title or product_errors:
            failed.append(p)
        else:
            passed.append(p)

    summary = {
        "total": len(products),
        "passed": len(passed),
        "failed": len(failed),
        "errors": sum(1 for i in issues if i["severity"] == SEVERITY_ERROR),
        "warnings": sum(1 for i in issues if i["severity"] == SEVERITY_WARNING),
    }
    return {
        "issues": issues,
        "failed": failed,
        "passed": passed,
        "summary": summary,
    }
