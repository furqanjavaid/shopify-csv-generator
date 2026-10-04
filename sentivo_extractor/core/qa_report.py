"""QA sampling workbook for manual review."""

from __future__ import annotations

import random
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _domain(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _options_str(product: dict[str, Any]) -> str:
    parts = []
    for opt in product.get("options") or []:
        name = opt.get("name") or ""
        vals = ", ".join(str(v) for v in (opt.get("values") or [])[:8])
        parts.append(f"{name}: {vals}")
    return " | ".join(parts)


def sample_products_for_qa(
    products: list[dict[str, Any]],
    *,
    per_domain: int = 20,
    seed: int = 42,
) -> list[dict[str, Any]]:
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in products:
        by_domain[_domain(str(p.get("source_url") or ""))].append(p)

    rng = random.Random(seed)
    samples: list[dict[str, Any]] = []
    for domain, items in sorted(by_domain.items()):
        pool = list(items)
        rng.shuffle(pool)
        for p in pool[: max(0, int(per_domain))]:
            variants = p.get("variants") or []
            images = p.get("images") or []
            first_price = ""
            compare = ""
            if variants:
                first_price = str(variants[0].get("price") or "")
                compare = str(variants[0].get("compare_at_price") or "")
            samples.append(
                {
                    "domain": domain,
                    "source_url": p.get("source_url") or "",
                    "title": p.get("title") or "",
                    "price": first_price,
                    "compare_at_price": compare,
                    "vendor": p.get("vendor") or p.get("brand") or "",
                    "product_type": p.get("product_type") or "",
                    "tags": ", ".join(p.get("tags") or [])
                    if isinstance(p.get("tags"), list)
                    else str(p.get("tags") or ""),
                    "options": _options_str(p),
                    "variant_count": len(variants),
                    "image_count": len(images),
                    "first_image_url": (images[0].get("src") if images else "") or "",
                    "confidence_score": p.get("confidence_score") or "",
                    "status_green_yellow_red": p.get("confidence_band") or "",
                    "manual_review_notes": "",
                    "approved": "",
                }
            )
    return samples


def write_qa_sample_workbook(rows: list[dict[str, Any]], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "domain",
        "source_url",
        "title",
        "price",
        "compare_at_price",
        "vendor",
        "product_type",
        "tags",
        "options",
        "variant_count",
        "image_count",
        "first_image_url",
        "confidence_score",
        "status_green_yellow_red",
        "manual_review_notes",
        "approved",
    ]
    try:
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sample Review"
        ws.append(fields)
        for row in rows:
            ws.append([row.get(f, "") for f in fields])
        wb.save(path)
    except Exception:
        import csv

        csv_path = path.with_suffix(".csv")
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fields})
        return csv_path
    return path
