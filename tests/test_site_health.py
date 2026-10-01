"""Tests for site health reporting (offline fixtures)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_recommend_action_ready():
    from sentivo_extractor.core.site_health import ACTION_READY, recommend_action

    action = recommend_action(
        platform="Shopify",
        success_rate=98.0,
        validation_passed=True,
        failed_products=0,
        needs_yaml="no",
        products_extracted=50,
        discovered=55,
        field_rates={"images": 100.0, "variants": 100.0, "price": 100.0, "sku": 100.0},
        audit={},
        val_rows=[],
    )
    assert action == ACTION_READY


def test_recommend_action_image_weakness():
    from sentivo_extractor.core.site_health import ACTION_IMAGE, recommend_action

    action = recommend_action(
        platform="Custom",
        success_rate=80.0,
        validation_passed=False,
        failed_products=3,
        needs_yaml="no",
        products_extracted=10,
        discovered=20,
        field_rates={"images": 70.0, "variants": 95.0, "price": 95.0, "sku": 90.0},
        audit={},
        val_rows=[{"Failure Reason": "missing_production_fields: images"}],
    )
    assert action == ACTION_IMAGE


def test_build_site_health_report_from_fixtures():
    from sentivo_extractor.core.site_health import (
        ACTION_YAML,
        build_site_health_report,
        write_site_health_outputs,
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        audit_dir = root / "audit"
        audit_dir.mkdir()
        validation_dir = root / "validation"
        validation_dir.mkdir()
        (root / "full").mkdir()
        (root / "pilot").mkdir()

        (audit_dir / "domain_audit.json").write_text(
            json.dumps(
                [
                    {
                        "domain": "shop.example",
                        "platform_detected": "WooCommerce",
                        "needs_yaml_rules": "yes",
                        "product_urls_found": 0,
                        "expected_count": "",
                    }
                ]
            ),
            encoding="utf-8",
        )
        # loader reads xlsx/csv only — write csv
        import csv

        with (audit_dir / "domain_audit.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "domain",
                    "platform_detected",
                    "needs_yaml_rules",
                    "product_urls_found",
                    "expected_count",
                    "title_success_rate",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "domain": "shop.example",
                    "platform_detected": "WooCommerce",
                    "needs_yaml_rules": "yes",
                    "product_urls_found": 0,
                    "expected_count": "",
                    "title_success_rate": 0.2,
                }
            )

        (root / "full" / "run_summary.json").write_text(
            json.dumps(
                {
                    "coverage": [
                        {
                            "domain": "shop.example",
                            "expected_count": "",
                            "discovered_count": 0,
                            "extracted_count": 0,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        (validation_dir / "final_validation_report.json").write_text(
            json.dumps(
                {
                    "products": [
                        {
                            "URL": "https://shop.example/p1",
                            "Title": "",
                            "Price": "",
                            "Images": 0,
                            "Variants": 0,
                            "SKU": "",
                            "Description": "missing",
                            "Status": "Failed",
                            "Confidence": "",
                            "Retry Count": 1,
                            "Failure Reason": "missing_production_fields: title, images",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        report = build_site_health_report(output_root=root)
        assert report["domains"][0]["Domain"] == "shop.example"
        assert report["domains"][0]["Recommended Action"] == ACTION_YAML
        xlsx, js = write_site_health_outputs(root, report)
        assert js.exists()
        loaded = json.loads(js.read_text(encoding="utf-8"))
        assert loaded["summary"]["domains_total"] == 1


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {exc}")
            import traceback

            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
