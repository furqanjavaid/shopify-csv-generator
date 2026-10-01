"""Read seed CSV with optional metadata columns."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

OPTIONAL_FIELDS = (
    "department",
    "source_name",
    "vendor",
    "product_type",
    "tags",
    "currency",
    "expected_count",
    "priority",
)

SEED_FIELDS = ("url", "type", *OPTIONAL_FIELDS)


def read_seed_csv(path: Path) -> list[dict[str, str]]:
    """
    Return list of seed rows with lowercased keys.
    Required: url. Optional: type + OPTIONAL_FIELDS.
    """
    path = Path(path)
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        # Headered CSV?
        has_header = "url" in sample.lower().split("\n", 1)[0]
        if has_header:
            reader = csv.DictReader(f)
            for row in reader:
                cleaned = {
                    (k or "").strip().lower(): (v or "").strip()
                    for k, v in row.items()
                    if k
                }
                if cleaned.get("url"):
                    rows.append(cleaned)
        else:
            for line in f:
                line = line.strip()
                if not line or line.lower().startswith("url"):
                    continue
                rows.append({"url": line, "type": "auto"})
    return rows


def seed_metadata(seed: dict[str, str]) -> dict[str, Any]:
    """Extract metadata to merge into normalized products."""
    meta: dict[str, Any] = {}
    for key in OPTIONAL_FIELDS:
        val = (seed.get(key) or "").strip()
        if not val:
            continue
        if key == "tags":
            meta["tags"] = [t.strip() for t in val.split(",") if t.strip()]
        elif key == "expected_count":
            try:
                meta["expected_count"] = int(float(val))
            except ValueError:
                meta["expected_count"] = val
        else:
            meta[key] = val
    return meta


def apply_seed_metadata(product: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Fill empty product fields from seed metadata; merge tags."""
    if not meta:
        return product
    for key in ("department", "vendor", "product_type", "currency", "source_name"):
        if meta.get(key) and not product.get(key):
            product[key] = meta[key]
    if meta.get("vendor") and not product.get("brand"):
        product["brand"] = meta["vendor"]
    if meta.get("tags"):
        existing = product.get("tags") or []
        if isinstance(existing, str):
            existing = [t.strip() for t in existing.split(",") if t.strip()]
        merged = list(existing)
        for t in meta["tags"]:
            if t not in merged:
                merged.append(t)
        product["tags"] = merged
    if meta.get("priority"):
        product["priority"] = meta["priority"]
    if meta.get("expected_count") is not None:
        product["expected_count"] = meta["expected_count"]
    if meta.get("source_name"):
        product["source_name"] = meta["source_name"]
    return product
