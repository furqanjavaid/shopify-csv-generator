"""Tests for post-extraction variant merger (same-base-title → Size variants)."""

from __future__ import annotations


def _simple(title: str, sku: str, price: str, url: str, images: list[str] | None = None):
    imgs = [
        {"src": src, "alt": "", "position": i}
        for i, src in enumerate(images or [], start=1)
    ]
    return {
        "title": title,
        "handle": title.lower().replace(" ", "-"),
        "source_url": url,
        "vendor": "Direct Plastics",
        "description_html": f"<p>{title}</p>",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [
            {
                "sku": sku,
                "barcode": "",
                "option1": "Default Title",
                "option2": "",
                "option3": "",
                "price": price,
                "compare_at_price": "",
                "inventory_qty": "",
                "available": True,
                "weight_grams": "",
                "variant_image": "",
            }
        ],
        "images": imgs,
        "confidence_score": 0.85,
        "extraction_method": "Magento Config",
        "platform": "Magento",
    }


def test_extract_base_title_and_size_from_directplastics_titles():
    from sentivo_extractor.post_processors.variant_merger import (
        extract_base_title,
        extract_size_value,
    )

    assert (
        extract_base_title("Acetal Black Rod 6mm dia x 500mm") == "Acetal Black Rod"
    )
    assert extract_size_value("Acetal Black Rod 6mm dia x 500mm") == "6mm × 500mm"
    assert extract_size_value("Acetal Black Rod 8mm × 1000mm") == "8mm × 1000mm"
    assert extract_base_title("Acetal Black Rod 8mm × 1000mm") == "Acetal Black Rod"


def test_merge_same_base_title_into_size_variants():
    from sentivo_extractor.post_processors.variant_merger import merge_products_by_base_title

    products = [
        _simple(
            "Acetal Black Rod 6mm dia x 500mm",
            "ACBKR00605",
            "0.46",
            "https://www.directplastics.co.uk/a-500",
            [
                "https://www.directplastics.co.uk/media/catalog/product/cache/aaa/d/i/rod.jpg",
                "https://www.directplastics.co.uk/media/catalog/product/cache/bbb/d/i/rod.jpg",
            ],
        ),
        _simple(
            "Acetal Black Rod 6mm dia x 1000mm",
            "ACBKR00610",
            "0.84",
            "https://www.directplastics.co.uk/a-1000",
            [
                "https://www.directplastics.co.uk/media/catalog/product/cache/ccc/d/i/rod.jpg",
            ],
        ),
        _simple(
            "Acetal Black Rod 8mm dia x 500mm",
            "ACBKR00805",
            "0.71",
            "https://www.directplastics.co.uk/a-8-500",
            [
                "https://www.directplastics.co.uk/media/catalog/product/cache/ddd/d/i/rod-alt.jpg",
            ],
        ),
        _simple(
            "Nylon Sheet 10mm",
            "NYLON10",
            "9.99",
            "https://www.directplastics.co.uk/nylon-alone",
            ["https://www.directplastics.co.uk/media/catalog/product/n.jpg"],
        ),
    ]

    merged = merge_products_by_base_title(products)
    assert len(merged) == 2  # Acetal parent + lone Nylon

    acetal = next(p for p in merged if p["title"] == "Acetal Black Rod")
    nylon = next(p for p in merged if "Nylon" in p["title"])

    assert acetal["options"] == [
        {
            "name": "Size",
            "values": ["6mm × 500mm", "6mm × 1000mm", "8mm × 500mm"],
        }
    ]
    assert len(acetal["variants"]) == 3
    assert acetal["variants"][0]["sku"] == "ACBKR00605"
    assert acetal["variants"][0]["price"] == "0.46"
    assert acetal["variants"][0]["option1"] == "6mm × 500mm"
    assert acetal["variants"][1]["sku"] == "ACBKR00610"
    assert acetal["variants"][2]["sku"] == "ACBKR00805"
    # Magento cache paths for same file collapse; distinct file kept
    assert len(acetal["images"]) == 2
    assert "VariantMerge" in acetal["extraction_method"]
    assert len(acetal["merged_from_urls"]) == 3

    # Singleton with a size in title but no siblings stays untouched
    assert nylon["title"] == "Nylon Sheet 10mm"
    assert nylon["variants"][0]["option1"] == "Default Title"


def test_already_multi_variant_products_not_merged():
    from sentivo_extractor.post_processors.variant_merger import merge_products_by_base_title

    multi = {
        "title": "Acetal Black Rod",
        "source_url": "https://www.directplastics.co.uk/parent",
        "options": [{"name": "Diameter", "values": ["6mm", "8mm"]}],
        "variants": [
            {"sku": "A", "option1": "6mm", "option2": "", "option3": "", "price": "1"},
            {"sku": "B", "option1": "8mm", "option2": "", "option3": "", "price": "2"},
        ],
        "images": [],
    }
    simple = _simple(
        "Acetal Black Rod 6mm dia x 500mm",
        "ACBKR00605",
        "0.46",
        "https://www.directplastics.co.uk/a-500",
    )
    out = merge_products_by_base_title([multi, simple])
    assert len(out) == 2
    assert out[0]["variants"][0]["option1"] == "6mm"
    assert out[1]["variants"][0]["option1"] == "Default Title"


def test_cross_domain_products_not_merged():
    from sentivo_extractor.post_processors.variant_merger import merge_products_by_base_title

    a = _simple(
        "Acetal Black Rod 6mm x 500mm",
        "A1",
        "1.00",
        "https://shop-a.example/p1",
    )
    b = _simple(
        "Acetal Black Rod 6mm x 1000mm",
        "B1",
        "2.00",
        "https://shop-b.example/p1",
    )
    out = merge_products_by_base_title([a, b])
    assert len(out) == 2
