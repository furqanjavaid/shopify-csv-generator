"""
Sentivo — Automated Core Test Suite
Run with: python tests/test_all.py
"""
from __future__ import annotations

import csv as csvlib
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "[PASS]"
FAIL = "[FAIL]"
results = []


def test(name, fn):
    try:
        fn()
        results.append((PASS, name))
        print(f"{PASS} {name}")
    except Exception as e:
        results.append((FAIL, name))
        print(f"{FAIL} {name}")
        print(f"   -> {e}")
        traceback.print_exc()


def _mapping_dict(headers: list[str]) -> dict:
    """ColumnMapper.auto_map → {client_col: shopify_field}."""
    from app.core.column_mapper import ColumnMapper

    items = ColumnMapper().auto_map(headers)
    return {m["client_col"]: m["shopify_field"] for m in items}


def _list_mapping(client_to_shopify: dict) -> list[dict]:
    """Dict mapping → ShopifyGenerator mapping list."""
    return [
        {"client_col": c, "shopify_field": s}
        for c, s in client_to_shopify.items()
        if s
    ]


# ─── FILE PARSER ───────────────────────────────────────────
def test_csv_parse():
    from app.core.file_parser import FileParser

    rows = [
        ["Title", "Price", "SKU", "Description"],
        ["Test Product", "29.99", "SKU001", "A great product"],
        ["Test Product 2", "49.99", "SKU002", "Another product"],
    ]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
    ) as f:
        csvlib.writer(f).writerows(rows)
        path = f.name
    try:
        result = FileParser().parse(path)
    finally:
        os.unlink(path)
    assert result["headers"], "No headers returned"
    assert len(result["rows"]) == 2, f"Expected 2 rows, got {len(result['rows'])}"
    assert "Title" in result["headers"], "Title not in headers"


def test_excel_parse():
    from app.core.file_parser import FileParser

    try:
        import openpyxl
    except ImportError as exc:
        raise Exception("openpyxl not installed") from exc

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Raw Merged Data"
    ws.append(["Title", "Price", "SKU", "Body (HTML)"])
    ws.append(["Excel Product", "19.99", "EX001", "<p>Description</p>"])
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        wb.save(path)
        result = FileParser().parse(path)
        assert len(result["rows"]) >= 1, "No rows parsed from Excel"
        assert "Title" in result["headers"]
    finally:
        os.unlink(path)


# ─── COLUMN MAPPER ─────────────────────────────────────────
def test_automapping_basic():
    headers = ["Title", "Price", "SKU", "Description", "Image URL"]
    mapping = _mapping_dict(headers)
    assert mapping.get("Title") == "Title", f"Title not mapped: {mapping}"
    assert mapping.get("Price") in ("Price", "Variant Price"), f"Price not mapped: {mapping}"
    assert mapping.get("SKU") in ("SKU", "Variant SKU"), f"SKU not mapped: {mapping}"
    assert mapping.get("Description") == "Description", f"Description not mapped: {mapping}"


def test_automapping_aliases():
    headers = [
        "Product Name",
        "Raw Min Price",
        "Description / Specification",
        "First Image URL",
        "Brand",
    ]
    mapping = _mapping_dict(headers)
    assert mapping.get("Product Name") == "Title", f"Product Name→Title failed: {mapping}"
    assert mapping.get("Brand") == "Vendor", f"Brand→Vendor failed: {mapping}"
    assert mapping.get("Description / Specification") in (
        "Description",
        "Body (HTML)",
    ), f"Description alias failed: {mapping}"
    assert mapping.get("Raw Min Price") == "Price", f"Raw Min Price→Price failed: {mapping}"
    assert mapping.get("First Image URL") == "Product image URL", (
        f"First Image URL failed: {mapping}"
    )


# ─── SHOPIFY CSV GENERATOR ─────────────────────────────────
def test_csv_generation():
    from app.core.shopify_generator import ShopifyGenerator

    parsed = {
        "headers": ["Title", "Price", "SKU"],
        "rows": [
            {"Title": "Product A", "Price": "29.99", "SKU": "A001"},
            {"Title": "Product B", "Price": "49.99", "SKU": "B001"},
        ],
        "row_count": 2,
    }
    mapping = _list_mapping(
        {"Title": "Title", "Price": "Price", "SKU": "SKU"}
    )
    fd, out = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        ShopifyGenerator().generate(parsed, mapping, out)
        assert os.path.exists(out), "CSV file not created"
        with open(out, newline="", encoding="utf-8-sig") as f:
            rows = list(csvlib.DictReader(f))
        assert len(rows) >= 2, f"Expected 2+ rows, got {len(rows)}"
        assert rows[0].get("Title") == "Product A", f"Wrong title: {rows[0]}"
    finally:
        if os.path.exists(out):
            os.unlink(out)


def test_csv_html_description():
    from app.core.shopify_generator import ShopifyGenerator

    html = "<p>Hello, world. <strong>Bold</strong> text, with commas, everywhere.</p>"
    parsed = {
        "headers": ["Title", "Description"],
        "rows": [{"Title": "HTML Product", "Description": html}],
        "row_count": 1,
    }
    mapping = _list_mapping({"Title": "Title", "Description": "Description"})
    fd, out = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        ShopifyGenerator().generate(parsed, mapping, out)
        with open(out, newline="", encoding="utf-8-sig") as f:
            rows = list(csvlib.DictReader(f))
        assert len(rows) == 1, f"HTML split into multiple rows: {len(rows)}"
        desc = rows[0].get("Description", "")
        assert "Hello" in desc, "Description content missing"
    finally:
        if os.path.exists(out):
            os.unlink(out)


# ─── SCRAPER ───────────────────────────────────────────────
def test_scraper_valid_url():
    from app.core.collection_crawler import crawl

    result = crawl("https://unaya.de/collections/darm")
    assert result["row_count"] > 0, "No products scraped"
    assert result["headers"], "No headers returned"
    assert result["rows"], "No rows returned"
    first = result["rows"][0]
    assert first.get("Title"), f"Title missing in first row: {first}"


def test_scraper_invalid_url():
    from app.core.collection_crawler import CollectionCrawlError, crawl

    try:
        crawl("https://thisisnotarealstore99999.com/collections/all")
        raise AssertionError("Should have raised CollectionCrawlError")
    except CollectionCrawlError:
        pass  # expected


# ─── CONFIG ────────────────────────────────────────────────
def test_config_save_load():
    from app.utils.config import get_theme, set_theme

    previous = get_theme()
    try:
        set_theme("light")
        assert get_theme() == "light", "Light theme not saved"
        set_theme("dark")
        assert get_theme() == "dark", "Dark theme not saved"
    finally:
        set_theme(previous)


def test_image_converter():
    from app.core.image_converter import convert_images
    from PIL import Image
    import tempfile
    import os

    tmp_dir = tempfile.mkdtemp()
    img_path = os.path.join(tmp_dir, "test.png")
    Image.new("RGB", (100, 100), color=(255, 0, 0)).save(img_path)

    out_dir = os.path.join(tmp_dir, "converted")
    result = convert_images([img_path], out_dir, target_format="WEBP", quality=85)

    assert result["converted"] == 1, f"Expected 1 converted, got {result}"
    assert len(result["output_files"]) == 1
    assert result["output_files"][0].endswith(".webp")
    assert os.path.exists(result["output_files"][0])


# ─── RUN ALL ───────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  SENTIVO — CORE TEST SUITE")
    print("=" * 50 + "\n")

    test("CSV file parsing", test_csv_parse)
    test("Excel file parsing", test_excel_parse)
    test("Auto-mapping basic columns", test_automapping_basic)
    test("Auto-mapping aliases", test_automapping_aliases)
    test("CSV generation", test_csv_generation)
    test("CSV HTML description (no comma split)", test_csv_html_description)
    test("Scraper — valid Shopify URL", test_scraper_valid_url)
    test("Scraper — invalid URL error handling", test_scraper_invalid_url)
    test("Config save/load", test_config_save_load)
    test("Image converter WEBP", test_image_converter)

    print("\n" + "=" * 50)
    passed = sum(1 for r in results if r[0] == PASS)
    failed = sum(1 for r in results if r[0] == FAIL)
    print(f"  Results: {passed} passed, {failed} failed out of {len(results)}")
    print("=" * 50 + "\n")

    if failed:
        sys.exit(1)
