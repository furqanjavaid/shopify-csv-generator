"""Generate sample extractor outputs from offline fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from sentivo_extractor.core.image_pipeline import process_product_images
from sentivo_extractor.core.normalizer import normalize_product
from sentivo_extractor.core.shopify_csv_exporter import (
    export_failed_csv,
    export_images_manifest,
    export_shopify_csv,
    export_validation_report,
)
from sentivo_extractor.core.utils import write_json
from sentivo_extractor.core.validator import validate_products
from sentivo_extractor.extractors.html_extractor import HtmlExtractor
from sentivo_extractor.extractors.jsonld_extractor import JsonLdExtractor
from sentivo_extractor.extractors.shopify_extractor import ShopifyExtractor

fx = Path("tests/fixtures")
out = Path("output/sample_demo")
out.mkdir(parents=True, exist_ok=True)
(out / "raw_json_backup").mkdir(exist_ok=True)

products = []
shop = json.loads((fx / "shopify_product.json").read_text(encoding="utf-8"))
products.append(
    normalize_product(
        ShopifyExtractor().extract(
            "https://shop.example.com/products/classic-tee",
            context={"shopify_json": shop},
        )
    )
)
products.append(
    normalize_product(
        JsonLdExtractor().extract(
            "https://shop.example.com/mug",
            (fx / "jsonld_product.html").read_text(encoding="utf-8"),
        ),
        "https://shop.example.com/mug",
    )
)
products.append(
    normalize_product(
        HtmlExtractor().extract(
            "https://supplier.example.com/panel",
            (fx / "custom_html_product.html").read_text(encoding="utf-8"),
        ),
        "https://supplier.example.com/panel",
    )
)

manifest = []
for p in products:
    write_json(out / "raw_json_backup" / f"{p['handle']}.json", p)
    manifest.extend(process_product_images(p, download=False))

report = validate_products(products)
export_shopify_csv(report["passed"], out / "shopify_import.csv")
export_failed_csv(report["failed"], out / "failed_products.csv")
export_validation_report(report["issues"], out / "validation_report.xlsx")
export_images_manifest(manifest, out / "images_manifest.csv")
print("sample written to", out)
print("summary", report["summary"])
print("csv preview:")
for line in (out / "shopify_import.csv").read_text(encoding="utf-8").splitlines()[:5]:
    print(line[:160])
