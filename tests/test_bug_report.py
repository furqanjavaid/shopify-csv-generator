"""Tests for bug report capture and workbook (no auto-fix)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_classify_reason_groups():
    from sentivo_extractor.core.bug_report import classify_reason_group

    assert classify_reason_group("missing_valid_images") == "Image Failed"
    assert classify_reason_group("missing_required: title, price") == "Required Fields Missing"
    assert "Timeout" in classify_reason_group("Read timed out")
    assert classify_reason_group("variant probe empty") == "Variant Failed"


def test_bug_report_saves_artifacts_and_xlsx():
    from sentivo_extractor.core.bug_report import BugReportCollector

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        collector = BugReportCollector(root, run_mode="pilot")
        collector.record(
            url="https://shop.example/products/a",
            reason="missing_valid_images",
            stage="PDP Extraction",
            html="<html><body>A</body></html>",
            screenshot_png=b"\x89PNG\r\n\x1a\nfake",
            network_json=[{"url": "https://shop.example/api", "data": {}}],
        )
        collector.record(
            url="https://shop.example/products/b",
            reason="missing_required: price",
            html="<html>b</html>",
        )
        collector.record(
            url="https://other.example/p/c",
            reason="timeout reading response",
        )
        path = collector.write(
            root / "bug_report.xlsx",
            retry_queue=[{"url": "https://shop.example/products/a", "reason": "missing_valid_images"}],
        )
        assert path.exists()
        assert (root / "bug_failures" / "shop.example").exists()
        html_files = list((root / "bug_failures" / "shop.example").glob("*.html"))
        assert html_files
        counts = collector.reason_counts()
        assert counts[0][1] >= counts[-1][1]
        summary = collector.summary()
        assert summary["total_failures"] == 3


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
