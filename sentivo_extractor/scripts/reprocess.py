"""
Reprocess an existing extractor output folder from raw_json_backup/.

Loads saved product JSON, re-runs variant merge (with category description
fetch), validates, and writes a fresh shopify_import.csv under reprocessed/.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from sentivo_extractor.core.http_client import HttpClient
from sentivo_extractor.core.shopify_csv_exporter import export_failed_csv, export_shopify_csv
from sentivo_extractor.core.utils import DEFAULT_USER_AGENT
from sentivo_extractor.core.validator import validate_products
from sentivo_extractor.post_processors.variant_merger import merge_products_by_base_title


def _load_products(raw_dir: Path) -> list[dict[str, Any]]:
    files = sorted(raw_dir.glob("*.json"))
    products: list[dict[str, Any]] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {path.name}: {exc}")
            continue
        if isinstance(data, dict):
            products.append(data)
        else:
            print(f"  skip {path.name}: expected JSON object")
    return products


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

    print("Validating products…")
    report = validate_products(products)
    passed = report["passed"]
    failed = report["failed"]
    summary = report.get("summary") or {}
    print(
        f"  passed={len(passed)} failed={len(failed)} "
        f"errors={summary.get('errors', '?')} warnings={summary.get('warnings', '?')}"
    )

    csv_path = out_dir / "shopify_import.csv"
    print(f"Exporting {csv_path} (utf-8-sig)…")
    export_shopify_csv(passed, csv_path)
    if failed:
        failed_path = out_dir / "failed_products.csv"
        export_failed_csv(failed, failed_path)
        print(f"  failed products → {failed_path}")

    print("Done.")
    print(f"CSV: {csv_path}")
    return csv_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reprocess raw_json_backup/ through variant merger + validation "
            "and write reprocessed/shopify_import.csv"
        )
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to an extractor output folder containing raw_json_backup/",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds between category-page HTTP requests (default: 0.5)",
    )
    args = parser.parse_args(argv)

    try:
        reprocess(Path(args.input), delay=float(args.delay))
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
