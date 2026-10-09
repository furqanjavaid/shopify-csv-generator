"""
Reprocess an existing extractor output folder from raw_json_backup/.

Loads saved product JSON, re-runs variant merge (with category description
fetch), validates, and writes a fresh prefixed shopify_import.csv under
reprocessed/.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from sentivo_extractor.core.http_client import HttpClient
from sentivo_extractor.core.output_layout import (
    domain_folder_name,
    infer_domain_key_from_dir,
    prefixed_filename,
)
from sentivo_extractor.core.shopify_csv_exporter import export_failed_csv, export_shopify_csv
from sentivo_extractor.core.utils import (
    DEFAULT_USER_AGENT,
    configure_stdio_utf8,
    normalize_price,
)
from sentivo_extractor.core.validator import validate_products
from sentivo_extractor.post_processors.variant_merger import merge_products_by_base_title

# Ignore top-level "price" (£0.00 per cm² quirk). Prefer cached sqm rates.
_PLACEHOLDER_PRICE = "0.01"


def _resolve_price_per_sqm(data: dict[str, Any]) -> str:
    """
    CTS price for Shopify Variant Price:
      1) configurator_pricing.pricePerSqm
      2) price_per_sqm (e.g. '£48.16 per m²')
      3) specifications.pricePerSqm
      4) 0.01 placeholder
    Never use top-level / variant 'price' (£0.00 per cm²).
    """
    cp = data.get("configurator_pricing")
    if isinstance(cp, dict) and cp.get("pricePerSqm") is not None:
        amount = normalize_price(cp.get("pricePerSqm"))
        if amount and float(amount) > 0:
            return amount

    pps = data.get("price_per_sqm")
    if pps is not None and str(pps).strip():
        amount = normalize_price(pps)
        if amount and float(amount) > 0:
            return amount

    specs = data.get("specifications")
    if isinstance(specs, dict) and specs.get("pricePerSqm") is not None:
        amount = normalize_price(specs.get("pricePerSqm"))
        if amount and float(amount) > 0:
            return amount

    return _PLACEHOLDER_PRICE


def _is_cts_product(data: dict[str, Any]) -> bool:
    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if "cut-to-size-pricing" in tags:
        return True
    cp = data.get("configurator_pricing")
    if isinstance(cp, dict) and cp.get("pricePerSqm") is not None:
        return True
    if data.get("price_per_sqm"):
        return True
    return False


def _price_needs_sqm_fix(value: Any) -> bool:
    """True when price is missing, zero, or the £0.00 per cm² quirk."""
    text = str(value or "").strip()
    if not text:
        return True
    if re.search(r"per\s*cm", text, re.I):
        return True
    amount = normalize_price(text)
    if not amount:
        return True
    try:
        return float(amount) <= 0
    except ValueError:
        return True


def _hydrate_cts_prices(
    products: list[dict[str, Any]], *, only_blank: bool = False
) -> int:
    """
    Set product/variant price from cached pricePerSqm for CTS products.

    only_blank=True: do not overwrite variants that already have a real £/m²
    (needed after variant merge where each Size keeps its own rate).
    """
    fixed = 0
    for product in products:
        if not isinstance(product, dict) or not _is_cts_product(product):
            continue
        amount = _resolve_price_per_sqm(product)
        if not only_blank or _price_needs_sqm_fix(product.get("price")):
            product["price"] = amount
        variants = list(product.get("variants") or [])
        if not variants:
            variants = [{}]
        changed = False
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            if only_blank and not _price_needs_sqm_fix(variant.get("price")):
                continue
            variant["price"] = amount
            changed = True
        product["variants"] = variants
        if changed or not only_blank:
            fixed += 1
    return fixed


def _load_products(raw_dir: Path) -> list[dict[str, Any]]:
    files = sorted(raw_dir.glob("*.json"))
    products: list[dict[str, Any]] = []
    for path in files:
        try:
            # BOM-safe (Windows utf-8-sig dumps from extractor).
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {path.name}: {exc}")
            continue
        if isinstance(data, dict):
            products.append(data)
        else:
            print(f"  skip {path.name}: expected JSON object")
    return products


def _resolve_domain_key(input_dir: Path, products: list[dict[str, Any]]) -> str:
    key = infer_domain_key_from_dir(input_dir)
    if key and key != "unknown":
        return key
    for product in products:
        src = str(product.get("source_url") or "")
        if src:
            return domain_folder_name(src)
    return key or "unknown"


def reprocess(input_dir: Path, *, delay: float = 0.5) -> Path:
    input_dir = input_dir.resolve()
    raw_dir = input_dir / "raw_json_backup"
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"raw_json_backup/ not found under {input_dir}")

    out_dir = input_dir / "reprocessed"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Input:  {input_dir}")
    print(f"Backup: {raw_dir}")
    print(f"Output: {out_dir}")

    print("Loading raw JSON products…")
    products = _load_products(raw_dir)
    print(f"  loaded {len(products)} product(s) from {len(list(raw_dir.glob('*.json')))} file(s)")
    if not products:
        raise RuntimeError("No valid products found in raw_json_backup/")

    hydrated = _hydrate_cts_prices(products)
    print(f"  CTS pricePerSqm hydrated: {hydrated} product(s)")

    domain_key = _resolve_domain_key(input_dir, products)
    print(f"Domain prefix: {domain_key}_*")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("reprocess")

    print("Running variant merger (with category description fetch)…")
    http = HttpClient(
        user_agent=DEFAULT_USER_AGENT,
        delay_sec=delay,
        retries=2,
        respect_robots=False,
        logger=log,
    )
    before = len(products)
    products = merge_products_by_base_title(products, log=log, http=http)
    print(f"  merge: {before} → {len(products)} product(s)")

    # After merge: only fill blank/£0.00 cm² slots — keep per-Size rates.
    hydrated = _hydrate_cts_prices(products, only_blank=True)
    print(f"  CTS blank prices filled after merge: {hydrated} product(s)")

    print("Validating products…")
    report = validate_products(products)
    passed = report["passed"]
    failed = report["failed"]
    summary = report.get("summary") or {}
    print(
        f"  passed={len(passed)} failed={len(failed)} "
        f"errors={summary.get('errors', '?')} warnings={summary.get('warnings', '?')}"
    )

    csv_name = prefixed_filename(domain_key, "shopify_import.csv")
    csv_path = out_dir / csv_name
    print(f"Exporting {csv_path} (utf-8-sig)…")
    export_shopify_csv(passed, csv_path)

    # Canonical final CSV next to domain folder (requested path).
    final_path = input_dir / prefixed_filename(domain_key, "shopify_import_final.csv")
    print(f"Exporting {final_path} (utf-8-sig)…")
    export_shopify_csv(passed, final_path)

    if failed:
        failed_path = out_dir / prefixed_filename(domain_key, "failed_products.csv")
        export_failed_csv(failed, failed_path)
        print(f"  failed products → {failed_path}")

    print("Done.")
    print(f"CSV: {csv_path}")
    print(f"Final: {final_path}")
    return final_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reprocess raw_json_backup/ through variant merger + validation "
            "and write reprocessed/<domain>_shopify_import.csv"
        )
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a domain output folder containing raw_json_backup/",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds between category-page HTTP requests (default: 0.5)",
    )
    args = parser.parse_args(argv)
    configure_stdio_utf8()

    try:
        reprocess(Path(args.input), delay=float(args.delay))
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
