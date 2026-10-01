"""Tests for pilot QA: coverage, pre-import, SKU policy, QA workbook, images, pilot limit."""

from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_expected_count_coverage_calculation():
    from sentivo_extractor.core.coverage import build_coverage_report, coverage_row

    row = coverage_row(
        domain="shop.example",
        expected_count=500,
        discovered_count=520,
        extracted_count=420,
        min_coverage_percent=90,
    )
    assert row["missing_count"] == 80
    assert row["over_discovered_count"] == 20
    assert row["coverage_percent"] == 84.0
    assert row["risk_level"] == "high"
    assert row["coverage_status"] in ("warning", "fail")

    seeds = [
        {"url": "https://a.example/c", "expected_count": "100"},
        {"url": "https://b.example/c", "expected_count": "50"},
    ]
    discovered = [f"https://a.example/products/{i}" for i in range(90)] + [
        f"https://b.example/products/{i}" for i in range(40)
    ]
    products = [{"source_url": u} for u in discovered[:80]]  # 80 from a only in slice
    # Fix products to match domains properly
    products = [{"source_url": f"https://a.example/products/{i}"} for i in range(90)] + [
        {"source_url": f"https://b.example/products/{i}"} for i in range(45)
    ]
    rows = build_coverage_report(
        seeds=seeds,
        discovered_urls=discovered,
        products=products,
        min_coverage_percent=90,
    )
    by_dom = {r["domain"]: r for r in rows}
    assert by_dom["a.example"]["coverage_percent"] == 90.0
    assert by_dom["b.example"]["extracted_count"] == 45
    assert by_dom["b.example"]["coverage_percent"] == 90.0


def test_shopify_pre_import_validator(tmp_path: Path | None = None):
    from sentivo_extractor.core.shopify_csv_exporter import SHOPIFY_COLUMNS, export_shopify_csv
    from sentivo_extractor.core.shopify_preimport import validate_shopify_csv

    products = [
        {
            "handle": "tee",
            "title": "Tee",
            "description_html": "<p>Nice tee product description here.</p>",
            "vendor": "Acme",
            "department": "",
            "product_type": "Shirt",
            "tags": ["a"],
            "status": "active",
            "options": [{"name": "Size", "values": ["S", "M"]}],
            "variants": [
                {"option1": "S", "option2": "", "option3": "", "price": "10.00", "sku": "S1"},
                {"option1": "M", "option2": "", "option3": "", "price": "10.00", "sku": "S2"},
            ],
            "images": [{"src": "https://cdn.example/a.jpg", "alt": "", "position": 1}],
        }
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "shopify_import.csv"
        export_shopify_csv(products, path)
        result = validate_shopify_csv(path)
        assert result["summary"]["errors"] == 0

        # Break compare-at
        text = path.read_text(encoding="utf-8")
        # inject bad compare on first data line via rewrite
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
        rows[0]["Variant Compare At Price"] = "5.00"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SHOPIFY_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        bad = validate_shopify_csv(path)
        assert any(i["code"] == "compare_lt_price" for i in bad["issues"])


def test_duplicate_sku_policy():
    from sentivo_extractor.core.shopify_preimport import apply_duplicate_sku_policy

    products = [
        {
            "handle": "a",
            "variants": [{"sku": "DUP", "option1": "S"}],
        },
        {
            "handle": "b",
            "variants": [{"sku": "DUP", "option1": "M"}],
        },
    ]
    kept, failed, warnings = apply_duplicate_sku_policy(
        [dict(p, variants=[dict(v) for v in p["variants"]]) for p in products],
        "warn",
    )
    assert len(kept) == 2 and not failed and warnings

    kept, failed, warnings = apply_duplicate_sku_policy(
        [
            {"handle": "a", "variants": [{"sku": "DUP", "option1": "S"}]},
            {"handle": "b", "variants": [{"sku": "DUP", "option1": "M"}]},
        ],
        "fail",
    )
    assert len(failed) == 2 and len(kept) == 0

    products2 = [
        {"handle": "a", "variants": [{"sku": "DUP", "option1": "S"}]},
        {"handle": "b", "variants": [{"sku": "DUP", "option1": "M"}]},
    ]
    kept, failed, warnings = apply_duplicate_sku_policy(products2, "suffix")
    skus = [v["sku"] for p in kept for v in p["variants"]]
    assert "DUP" in skus and "DUP-2" in skus

    products3 = [
        {"handle": "a", "variants": [{"sku": "DUP", "option1": "S"}]},
        {"handle": "b", "variants": [{"sku": "DUP", "option1": "M"}]},
    ]
    kept, failed, warnings = apply_duplicate_sku_policy(products3, "blank")
    skus = [v["sku"] for p in kept for v in p["variants"]]
    assert skus.count("DUP") == 1 and "" in skus


def test_qa_sample_workbook_generation():
    from sentivo_extractor.core.qa_report import (
        sample_products_for_qa,
        write_qa_sample_workbook,
    )

    products = []
    for dom, n in (("a.example", 30), ("b.example", 5)):
        for i in range(n):
            products.append(
                {
                    "source_url": f"https://{dom}/products/p{i}",
                    "title": f"P{i}",
                    "vendor": "V",
                    "product_type": "T",
                    "tags": ["x"],
                    "options": [{"name": "Size", "values": ["S"]}],
                    "variants": [{"option1": "S", "price": "9.99"}],
                    "images": [{"src": f"https://cdn.example/{i}.jpg"}],
                    "confidence_score": 0.95,
                    "confidence_band": "green",
                }
            )
    samples = sample_products_for_qa(products, per_domain=20, seed=42)
    assert sum(1 for s in samples if s["domain"] == "a.example") == 20
    assert sum(1 for s in samples if s["domain"] == "b.example") == 5
    # deterministic
    samples2 = sample_products_for_qa(products, per_domain=20, seed=42)
    assert [s["source_url"] for s in samples] == [s["source_url"] for s in samples2]

    with tempfile.TemporaryDirectory() as tmp:
        out = write_qa_sample_workbook(samples, Path(tmp) / "sample_review.xlsx")
        assert out.exists()


def test_image_url_checker_mocked():
    from sentivo_extractor.core.image_checker import check_image_url, check_product_images

    class Resp:
        def __init__(self, status, url="https://cdn.example/a.jpg", history=None):
            self.status_code = status
            self.url = url
            self.history = history or []
            self.headers = {}

        def close(self):
            pass

        def iter_content(self, chunk_size=1024):
            yield b"x"

    sess = MagicMock()
    sess.headers = {}
    sess.head.side_effect = lambda *a, **k: Resp(200)
    result = check_image_url("https://cdn.example/a.jpg", session=sess)
    assert result["status"] == "ok"

    sess.head.side_effect = lambda *a, **k: Resp(404)
    result = check_image_url("https://cdn.example/missing.jpg", session=sess)
    assert result["status"] == "broken"

    sess.head.side_effect = lambda *a, **k: Resp(403)
    result = check_image_url("https://cdn.example/blocked.jpg", session=sess)
    assert result["status"] == "blocked"

    products = [
        {
            "handle": "x",
            "images": [{"src": "https://cdn.example/a.jpg"}],
            "variants": [],
        }
    ]
    sess.head.side_effect = lambda *a, **k: Resp(200)
    issues = check_product_images(products, session=sess)
    assert issues == []


def test_pilot_mode_domain_limit():
    from sentivo_extractor.core.crawler import UniversalCrawler

    crawler = UniversalCrawler(
        {
            "output": tempfile.mkdtemp(),
            "pilot": True,
            "pilot_size_per_domain": 3,
            "max_products_per_domain": 500,
            "respect_robots": False,
            "delay": 0,
        }
    )
    assert crawler.max_products_per_domain == 3
    urls = [f"https://a.example/products/{i}" for i in range(10)] + [
        f"https://b.example/products/{i}" for i in range(10)
    ]
    limited = crawler._limit_urls_per_domain(urls, 3)
    assert len(limited) == 6
    assert sum(1 for u in limited if "a.example" in u) == 3
    assert sum(1 for u in limited if "b.example" in u) == 3


def test_pilot_overwrite_guard():
    from sentivo_extractor.core.crawler import UniversalCrawler
    from sentivo_extractor.core.utils import close_logger

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        (out / "shopify_import.csv").write_text("Handle\nx\n", encoding="utf-8")
        crawler = UniversalCrawler(
            {
                "output": str(out),
                "pilot": True,
                "overwrite": False,
                "respect_robots": False,
                "delay": 0,
            }
        )
        seeds = Path(tmp) / "seeds.csv"
        seeds.write_text(
            "url,type\nhttps://example.com/p,product\n", encoding="utf-8"
        )
        raised = False
        try:
            crawler.run(seeds)
        except RuntimeError as exc:
            raised = True
            assert "overwrite" in str(exc).lower()
        finally:
            close_logger(crawler.logger)
        assert raised


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
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
