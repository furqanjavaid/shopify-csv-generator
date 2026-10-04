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


def test_category_slug_candidates_strip_size_then_color():
    from sentivo_extractor.post_processors.variant_merger import category_slug_candidates

    assert category_slug_candidates(
        "https://www.directplastics.co.uk/acetal-black-rod-6mm-dia-x-500mm"
    ) == ["acetal-black-rod", "acetal-rod"]
    assert category_slug_candidates(
        "https://www.directplastics.co.uk/acetal-natural-sheet-3mm-x-250mm-x-250mm"
    ) == ["acetal-natural-sheet", "acetal-sheet"]


def test_enrich_description_from_category_page_when_longer():
    from sentivo_extractor.post_processors.variant_merger import merge_products_by_base_title

    short = "<p>Short meta blurb about acetal.</p>"
    rich = (
        "<h2>Product Information</h2>"
        "<p>Extruded Acetal C offers high stiffness and food compliance.</p>"
        "<p><strong>Properties</strong></p><ul><li>Easy to machine</li></ul>"
        "<p><strong>Applications</strong></p><ul><li>Gears</li><li>Seals</li></ul>"
    )

    class _Http:
        def get_text(self, url: str) -> str:
            if url.endswith("/acetal-black-rod"):
                raise RuntimeError("404")
            if url.endswith("/acetal-rod"):
                return (
                    "<html><body>"
                    f'<div class="category-description">{rich}</div>'
                    "</body></html>"
                )
            raise RuntimeError(f"unexpected url {url}")

    products = [
        _simple(
            "Acetal Black Rod 6mm dia x 500mm",
            "A1",
            "1.00",
            "https://www.directplastics.co.uk/acetal-black-rod-6mm-dia-x-500mm",
        ),
        _simple(
            "Acetal Black Rod 8mm dia x 500mm",
            "A2",
            "2.00",
            "https://www.directplastics.co.uk/acetal-black-rod-8mm-dia-x-500mm",
        ),
    ]
    for p in products:
        p["description_html"] = short

    merged = merge_products_by_base_title(products, http=_Http())
    acetal = next(p for p in merged if p["title"] == "Acetal Black Rod")
    assert "Product Information" in acetal["description_html"]
    assert "Properties" in acetal["description_html"]
    assert acetal.get("category_description_url", "").endswith("/acetal-rod")


def test_category_fetch_failure_keeps_existing_description():
    from sentivo_extractor.post_processors.variant_merger import merge_products_by_base_title

    class _Http:
        def get_text(self, url: str) -> str:
            raise RuntimeError("network down")

    products = [
        _simple(
            "Acetal Black Rod 6mm dia x 500mm",
            "A1",
            "1.00",
            "https://www.directplastics.co.uk/acetal-black-rod-6mm-dia-x-500mm",
        ),
        _simple(
            "Acetal Black Rod 8mm dia x 500mm",
            "A2",
            "2.00",
            "https://www.directplastics.co.uk/acetal-black-rod-8mm-dia-x-500mm",
        ),
    ]
    products[0]["description_html"] = "<p>Keep me</p>"
    products[1]["description_html"] = "<p>Keep me</p>"

    merged = merge_products_by_base_title(products, http=_Http())
    acetal = next(p for p in merged if p["title"] == "Acetal Black Rod")
    assert acetal["description_html"] == "<p>Keep me</p>"


def test_split_products_with_more_than_99_variants():
    from sentivo_extractor.post_processors.variant_merger import (
        MAX_VARIANTS_PER_PRODUCT,
        merge_products_by_base_title,
    )

    products = []
    for i in range(120):
        dia = 6 + (i % 40)
        length = 500 + (i * 10)
        products.append(
            _simple(
                f"Acetal Natural Sheet {dia}mm x {length}mm",
                f"SKU{i:03d}",
                f"{1 + i * 0.01:.2f}",
                f"https://www.directplastics.co.uk/acetal-natural-sheet-{dia}mm-x-{length}mm",
            )
        )

    merged = merge_products_by_base_title(products)
    parts = [p for p in merged if p["title"].startswith("Acetal Natural Sheet (Part ")]
    assert len(parts) == 2
    assert parts[0]["title"] == "Acetal Natural Sheet (Part 1)"
    assert parts[1]["title"] == "Acetal Natural Sheet (Part 2)"
    assert len(parts[0]["variants"]) == MAX_VARIANTS_PER_PRODUCT
    assert len(parts[1]["variants"]) == 120 - MAX_VARIANTS_PER_PRODUCT
    assert len(parts[0]["options"][0]["values"]) == MAX_VARIANTS_PER_PRODUCT
    assert parts[0]["variant_merge"]["part"] == 1
    assert parts[0]["variant_merge"]["part_count"] == 2
    assert parts[1]["handle"] != parts[0]["handle"]


def test_strip_sample_size_color_title():
    from sentivo_extractor.post_processors.variant_merger import (
        strip_sample_size_color_title,
    )

    assert (
        strip_sample_size_color_title("20mm Sample Acetal Black Rod")
        == "Acetal Black Rod"
    )
    assert (
        strip_sample_size_color_title("20mm Sample Acetal Black Rod", strip_color=True)
        == "Acetal Rod"
    )


def test_title_fallback_category_when_url_slug_guess_fails():
    from sentivo_extractor.post_processors.variant_merger import merge_products_by_base_title

    rich = (
        "<h2>Product Information</h2>"
        + ("<p>Rich category copy for acetal machining plastic. </p>" * 20)
    )

    class _Http:
        def get_text(self, url: str) -> str:
            # Only the cleaned-title slug works (no size segment in source URLs).
            if url.endswith("/acetal-black-rod") or url.endswith("/acetal-rod"):
                return (
                    f'<html><body><div class="category-description">{rich}</div>'
                    "</body></html>"
                )
            raise RuntimeError(f"unexpected {url}")

    products = [
        _simple(
            "Acetal Black Rod 6mm dia x 500mm",
            "A1",
            "1.00",
            "https://www.directplastics.co.uk/samples/acetal-a",
        ),
        _simple(
            "Acetal Black Rod 8mm dia x 500mm",
            "A2",
            "2.00",
            "https://www.directplastics.co.uk/samples/acetal-b",
        ),
    ]
    for p in products:
        p["description_html"] = "<p>Short meta only.</p>"

    merged = merge_products_by_base_title(products, http=_Http())
    acetal = next(p for p in merged if p["title"] == "Acetal Black Rod")
    assert "Product Information" in acetal["description_html"]
    assert acetal.get("category_description_url", "").endswith(
        ("/acetal-black-rod", "/acetal-rod")
    )


def test_inherit_description_from_sibling_for_sample_product():
    from sentivo_extractor.post_processors.variant_merger import (
        MIN_RICH_DESCRIPTION_LEN,
        merge_products_by_base_title,
    )

    rich = (
        "<h2>Product Information</h2>"
        + ("<p>Full acetal rod properties and applications text. </p>" * 25)
    )
    assert len(rich) >= MIN_RICH_DESCRIPTION_LEN

    # Merged parent with rich description
    parent_members = [
        _simple(
            "Acetal Black Rod 6mm dia x 500mm",
            "A1",
            "1.00",
            "https://www.directplastics.co.uk/acetal-black-rod-6mm-dia-x-500mm",
        ),
        _simple(
            "Acetal Black Rod 8mm dia x 500mm",
            "A2",
            "2.00",
            "https://www.directplastics.co.uk/acetal-black-rod-8mm-dia-x-500mm",
        ),
    ]
    for p in parent_members:
        p["description_html"] = rich

    # Sample product stays as its own (short) listing
    sample = _simple(
        "20mm Sample Acetal Black Rod",
        "SAMP20",
        "3.00",
        "https://www.directplastics.co.uk/20mm-sample-acetal-black-rod",
    )
    sample["description_html"] = "<p>Short sample blurb only.</p>"

    # No HTTP → category fetch skipped; inheritance should still copy from sibling.
    merged = merge_products_by_base_title(parent_members + [sample], http=None)
    sample_out = next(p for p in merged if "Sample" in p["title"])
    parent_out = next(p for p in merged if p["title"] == "Acetal Black Rod")
    assert "Product Information" in parent_out["description_html"]
    assert sample_out["description_html"] == parent_out["description_html"]
    assert sample_out.get("description_inherited_from") == "Acetal Black Rod"
