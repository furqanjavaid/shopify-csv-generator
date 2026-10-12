"""
Offline WooCommerce title/handle cleanup (no re-scrape).

Usage:
  python scripts/cleanup_woocommerce_titles.py --input output/thepolycarbonatestore
  python scripts/cleanup_woocommerce_titles.py --csv path/to/shopify_import.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sentivo_extractor.core.normalizer import normalize_product
from sentivo_extractor.core.shopify_csv_exporter import export_shopify_csv
from sentivo_extractor.core.woocommerce_title import apply_woocommerce_title_and_handle


def _cleanup_products(products: list[dict], *, force: bool = True) -> int:
    fixed = 0
    for product in products:
        before_t = str(product.get("title") or "")
        before_h = str(product.get("handle") or "")
        html = str(
            product.get("page_html")
            or product.get("html")
            or product.get("debug_html")
            or ""
        )
        apply_woocommerce_title_and_handle(product, html, force=force)
        if product.get("title") != before_t or product.get("handle") != before_h:
            fixed += 1
    return fixed


def cleanup_raw_dir(input_dir: Path) -> Path:
    raw_dir = input_dir / "raw_json_backup"
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"raw_json_backup/ not found under {input_dir}")

    products: list[dict] = []
    for path in sorted(raw_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            products.append(data)

    fixed = _cleanup_products(products, force=True)
    out_dir = input_dir / "reprocessed"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Rewrite cleaned JSON next to originals under reprocessed/raw/
    raw_out = out_dir / "raw_json_backup"
    raw_out.mkdir(parents=True, exist_ok=True)
    cleaned: list[dict] = []
    for product in products:
        normalized = normalize_product(product)
        cleaned.append(normalized)
        handle = normalized.get("handle") or "product"
        (raw_out / f"{handle}.json").write_text(
            json.dumps(normalized, indent=2, ensure_ascii=False),
            encoding="utf-8-sig",
        )

    csv_path = out_dir / f"{input_dir.name}_shopify_import.csv"
    export_shopify_csv(cleaned, csv_path)
    print(f"Cleaned {fixed}/{len(products)} product(s)")
    print(f"CSV: {csv_path}")
    return csv_path


def cleanup_csv(csv_path: Path) -> Path:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    products: list[dict] = []
    for row in rows:
        products.append(
            {
                "title": row.get("Title") or row.get("title") or "",
                "handle": row.get("Handle") or row.get("handle") or "",
                "source_url": row.get("Source URL")
                or row.get("source_url")
                or "",
                "platform": "WooCommerce",
            }
        )

    fixed = _cleanup_products(products, force=True)
    out = csv_path.with_name(csv_path.stem + "_title_cleaned.csv")
    # Patch title/handle back into original Shopify rows when columns exist.
    for row, product in zip(rows, products):
        if "Title" in row:
            row["Title"] = product["title"]
        if "title" in row:
            row["title"] = product["title"]
        if "Handle" in row:
            row["Handle"] = product["handle"]
        if "handle" in row:
            row["handle"] = product["handle"]

    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames or list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Cleaned {fixed}/{len(products)} row(s)")
    print(f"CSV: {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline WooCommerce title/handle cleanup")
    parser.add_argument("--input", help="Domain output folder with raw_json_backup/")
    parser.add_argument("--csv", help="Shopify import CSV to clean")
    args = parser.parse_args(argv)

    try:
        if args.csv:
            cleanup_csv(Path(args.csv))
        elif args.input:
            cleanup_raw_dir(Path(args.input))
        else:
            parser.error("Provide --input or --csv")
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
