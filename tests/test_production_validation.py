"""Tests for Production Validation Mode (field checks + report)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _sample_product(**overrides):
    base = {
        "title": "Widget",
        "description_html": "<p>A sufficiently long product description for validation.</p>",
        "images": [{"src": "https://cdn.example.com/w.jpg", "alt": "", "position": 1}],
        "variants": [{"sku": "W-1", "price": "12.00", "option1": "Default Title"}],
        "confidence_score": 0.91,
        "source_url": "https://shop.example.com/products/widget",
    }
    base.update(overrides)
    return base


def test_assess_production_fields_complete():
    from sentivo_extractor.core.production_validation import assess_production_fields

    check = assess_production_fields(_sample_product())
    assert check["missing"] == []
    assert check["failure_reason"] == ""


def test_assess_production_fields_missing_multiple():
    from sentivo_extractor.core.production_validation import assess_production_fields

    check = assess_production_fields(
        _sample_product(title="", sku="", description_html="", images=[], variants=[])
    )
    assert "title" in check["missing"]
    assert "sku" in check["missing"]
    assert "description" in check["missing"]
    assert "images" in check["missing"]
    assert "variants" in check["missing"]


def test_validation_summary_and_strict():
    from sentivo_extractor.core.production_validation import (
        STATUS_FAILED,
        STATUS_RECOVERED,
        STATUS_SUCCESS,
        build_validation_summary,
        strict_validation_passed,
        validation_row,
    )

    rows = [
        validation_row(
            url="https://a/1",
            product=_sample_product(),
            status=STATUS_SUCCESS,
            retry_count=0,
        ),
        validation_row(
            url="https://a/2",
            product=_sample_product(),
            status=STATUS_RECOVERED,
            retry_count=1,
        ),
        validation_row(
            url="https://a/3",
            product=None,
            status=STATUS_FAILED,
            retry_count=1,
            failure_reason="missing_production_fields: sku",
        ),
    ]
    summary = build_validation_summary(rows)
    assert summary["Total Products"] == 3
    assert summary["Successful"] == 1
    assert summary["Recovered After Retry"] == 1
    assert summary["Failed"] == 1
    assert summary["Success Rate %"] == round(2 / 3 * 100, 2)
    assert strict_validation_passed(summary) is False

    summary_ok = {
        "Total Products": 20,
        "Successful": 18,
        "Recovered After Retry": 2,
        "Failed": 0,
        "Success Rate %": 100.0,
    }
    assert strict_validation_passed(summary_ok) is True


def test_write_final_validation_report_xlsx():
    from sentivo_extractor.core.production_validation import (
        STATUS_SUCCESS,
        build_validation_summary,
        validation_row,
        write_final_validation_report,
    )

    rows = [
        validation_row(
            url="https://shop.example.com/p",
            product=_sample_product(),
            status=STATUS_SUCCESS,
            retry_count=0,
        )
    ]
    summary = build_validation_summary(rows)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "final_validation_report.xlsx"
        out = write_final_validation_report(path, rows, summary)
        assert out.exists()
        assert out.stat().st_size > 100
        text = out.read_bytes()[:2]
        assert text == b"PK" or out.suffix == ".json"


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
