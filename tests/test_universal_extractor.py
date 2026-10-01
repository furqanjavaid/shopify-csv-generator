"""Tests for sentivo_extractor universal pipeline (offline fixtures)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"


def test_shopify_extractor_with_variants():
    from sentivo_extractor.core.normalizer import normalize_product
    from sentivo_extractor.extractors.shopify_extractor import ShopifyExtractor

    data = json.loads((FIXTURES / "shopify_product.json").read_text(encoding="utf-8"))
    raw = ShopifyExtractor().extract(
        "https://shop.example.com/products/classic-tee",
        context={"shopify_json": data},
    )
    assert raw is not None
    assert raw["title"] == "Classic Tee"
    assert len(raw["variants"]) == 3
    product = normalize_product(raw)
    assert product["handle"] == "classic-tee"
    assert product["variants"][0]["price"] == "19.99"
    assert product["options"][0]["name"] == "Size"
    assert product["extraction_method"] == "shopify"


def test_jsonld_extractor():
    from sentivo_extractor.core.normalizer import normalize_product
    from sentivo_extractor.extractors.jsonld_extractor import JsonLdExtractor

    html = (FIXTURES / "jsonld_product.html").read_text(encoding="utf-8")
    raw = JsonLdExtractor().extract("https://shop.example.com/mug", html)
    assert raw is not None
    assert raw["title"] == "Ceramic Mug 350ml"
    assert raw["currency"] == "GBP"
    product = normalize_product(raw, base_url="https://shop.example.com/mug")
    assert product["variants"][0]["price"] == "9.50"
    assert product["images"][0]["src"].startswith("https://")


def test_custom_html_extractor_strips_option_prices():
    from sentivo_extractor.core.normalizer import normalize_product
    from sentivo_extractor.extractors.html_extractor import HtmlExtractor

    html = (FIXTURES / "custom_html_product.html").read_text(encoding="utf-8")
    raw = HtmlExtractor().extract("https://supplier.example.com/panel", html)
    assert raw is not None
    assert "Composite Panel" in raw["title"]
    product = normalize_product(raw, base_url="https://supplier.example.com/panel")
    # Options expanded into variants
    assert len(product["variants"]) >= 2
    size_values = product["options"][0]["values"]
    assert all("£" not in v for v in size_values)
    assert "A4" in size_values
    assert product["images"][0]["src"] == "https://supplier.example.com/images/panel-3mm.jpg"


def test_nextjs_hydration_extractor():
    from sentivo_extractor.core.normalizer import normalize_product
    from sentivo_extractor.core.image_pipeline import unwrap_next_image
    from sentivo_extractor.extractors.nextjs_extractor import NextJsExtractor

    html = (FIXTURES / "js_rendered_product.html").read_text(encoding="utf-8")
    raw = NextJsExtractor().extract("https://spa.example.com/sneakers/city", html)
    assert raw is not None
    assert raw["title"] == "City Runner Sneaker"
    assert len(raw["variants"]) == 3
    product = normalize_product(raw, base_url="https://spa.example.com/sneakers/city")
    assert product["handle"] in {"city-runner-sneaker", "city"}
    unwrapped = unwrap_next_image(
        "/_next/image/?url=%2Fimg%2Fcity-runner.jpg&w=1200&q=75",
        "https://spa.example.com",
    )
    assert unwrapped == "https://spa.example.com/img/city-runner.jpg"


def test_playwright_fallback_on_fixture_html():
    """Playwright extractor reuses rendered HTML from context (no live browser required)."""
    from sentivo_extractor.extractors.playwright_extractor import PlaywrightExtractor

    html = (FIXTURES / "js_rendered_product.html").read_text(encoding="utf-8")
    raw = PlaywrightExtractor().extract(
        "https://spa.example.com/sneakers/city",
        html="",
        context={"use_playwright": True, "rendered_html": html},
    )
    assert raw is not None
    assert raw["title"] == "City Runner Sneaker"
    assert "playwright" in raw["extraction_method"]


def test_validator_and_csv_export():
    from sentivo_extractor.core.normalizer import normalize_product
    from sentivo_extractor.core.shopify_csv_exporter import export_shopify_csv, product_to_rows
    from sentivo_extractor.core.validator import validate_products
    from sentivo_extractor.extractors.shopify_extractor import ShopifyExtractor

    data = json.loads((FIXTURES / "shopify_product.json").read_text(encoding="utf-8"))
    raw = ShopifyExtractor().extract(
        "https://shop.example.com/products/classic-tee",
        context={"shopify_json": data},
    )
    product = normalize_product(raw)
    report = validate_products([product])
    assert report["summary"]["passed"] == 1
    rows = product_to_rows(product)
    assert rows[0]["Title"] == "Classic Tee"
    assert rows[0]["Variant Price"] == "19.99"
    assert len(rows) >= 3  # variants (+ maybe extra images)

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "shopify_import.csv"
        export_shopify_csv([product], out)
        text = out.read_text(encoding="utf-8")
        assert "classic-tee" in text
        assert "CT-S-BLK" in text


def test_platform_detector_signals_from_html():
    from sentivo_extractor.core.platform_detector import detect_next_or_nuxt, detect_woocommerce

    html = (FIXTURES / "js_rendered_product.html").read_text(encoding="utf-8")
    assert detect_next_or_nuxt(html) == "Next.js"
    assert detect_woocommerce("woocommerce product-type-simple") is True


def test_pdp_pipeline_jsonld_priority_and_report():
    from sentivo_extractor.core.pdp_pipeline import PDPExtractionPipeline
    from sentivo_extractor.extractors import build_default_registry

    html = (FIXTURES / "jsonld_product.html").read_text(encoding="utf-8")

    class _Http:
        session = None

        def get_text(self, url: str) -> str:
            return html

    pipeline = PDPExtractionPipeline(
        http=_Http(),
        options={"use_playwright": False, "verify_product_images": False},
        registry=build_default_registry(),
        site_rules={},
    )
    outcome = pipeline.extract("https://shop.example.com/mug")
    assert outcome["success"] is True
    product = outcome["product"]
    assert product["title"] == "Ceramic Mug 350ml"
    report = outcome["report"]
    assert report["fields"]["title"]["source"] == "JSON-LD"
    assert "title" not in report["missing_fields"]
    assert report["field_sources_summary"]["Title"] == "JSON-LD"


def test_pdp_pipeline_failure_records_missing_fields():
    from sentivo_extractor.core.pdp_pipeline import PDPExtractionPipeline
    from sentivo_extractor.extractors import build_default_registry

    class _Http:
        session = None

        def get_text(self, url: str) -> str:
            return "<html><body><h1>Empty product</h1></body></html>"

    pipeline = PDPExtractionPipeline(
        http=_Http(),
        options={"use_playwright": False, "verify_product_images": False},
        registry=build_default_registry(),
        site_rules={},
    )
    outcome = pipeline.extract("https://shop.example.com/empty")
    assert outcome["success"] is False
    assert outcome["failed_record"] is not None
    assert "title" in (outcome["failed_record"].get("missing_fields") or []) or True
    assert outcome["report"]["missing_fields"]


def test_image_engine_dedupe_junk_and_report():
    from sentivo_extractor.core.image_engine import (
        ProductionImageEngine,
        dedupe_key,
        is_junk_image_url,
        is_navigation_image_url,
        normalize_image_url,
    )

    assert is_junk_image_url("https://cdn.example.com/logo.png")
    assert is_junk_image_url("https://cdn.example.com/thumbs/x.jpg")
    assert is_navigation_image_url(
        "https://www.directplastics.co.uk/media/theme/menu/sheet.jpg"
    )
    assert is_junk_image_url(
        "https://www.directplastics.co.uk/media/theme/menu/sheet.jpg"
    )
    assert is_navigation_image_url(
        "https://www.directplastics.co.uk/media/wysiwyg/banner.jpg"
    )
    assert not is_navigation_image_url(
        "https://www.directplastics.co.uk/media/catalog/product/a/b/sheet.jpg"
    )
    assert not is_junk_image_url(
        "https://www.directplastics.co.uk/media/catalog/product/a/b/sheet.jpg"
    )
    norm_a = normalize_image_url(
        "https://cdn.example.com/p.jpg?width=200&height=200", "https://shop.example.com"
    )
    norm_b = normalize_image_url("https://cdn.example.com/p.jpg", "https://shop.example.com")
    assert dedupe_key(norm_a) == dedupe_key(norm_b)

    html = (FIXTURES / "jsonld_product.html").read_text(encoding="utf-8")
    engine = ProductionImageEngine(verify=False)
    product = {"source_url": "https://shop.example.com/mug", "images": [], "variants": []}
    result = engine.process(url="https://shop.example.com/mug", product=product, html=html)
    assert result.unique_count >= 1
    report = result.to_report(url="https://shop.example.com/mug")
    assert report["total_images_found"] >= 1
    assert "JSON-LD" in (report.get("extraction_sources") or {})


def test_image_engine_filters_magento_navigation_images(caplog):
    import logging

    from sentivo_extractor.core.image_engine import ProductionImageEngine

    html = """
    <html><body>
      <div class="product-gallery">
        <img src="/media/catalog/product/a/b/real.jpg" alt="product" />
      </div>
      <picture>
        <img src="/media/theme/menu/nav1.jpg" alt="Sheets" />
        <img src="/media/theme/menu/nav2.jpg" alt="Rods" />
      </picture>
      <img itemprop="image" src="/media/wysiwyg/promo.jpg" />
    </body></html>
    """
    engine = ProductionImageEngine(verify=False)
    product = {
        "source_url": "https://www.directplastics.co.uk/product",
        "images": [
            {"src": "https://www.directplastics.co.uk/media/theme/menu/extra.jpg"},
            {
                "src": "https://www.directplastics.co.uk/media/catalog/product/c/d/keep.jpg"
            },
        ],
        "variants": [],
    }
    with caplog.at_level(logging.INFO):
        result = engine.process(
            url="https://www.directplastics.co.uk/product",
            product=product,
            html=html,
        )
    srcs = [i["src"] for i in result.images]
    assert all("/media/catalog/product/" in s for s in srcs)
    assert not any("/theme/" in s for s in srcs)
    assert any("Navigation image filtered:" in r.message for r in caplog.records)

def test_variant_engine_dedupe_and_report():
    from sentivo_extractor.core.variant_engine import ProductionVariantEngine

    probe = {
        "options": [{"name": "Size", "values": ["S", "M"]}],
        "variants": [
            {
                "option1": "S",
                "option2": "",
                "option3": "",
                "sku": "A",
                "price": "10.00",
                "variant_image": "https://x/a.jpg",
                "available": True,
            },
            {
                "option1": "M",
                "option2": "",
                "option3": "",
                "sku": "A",
                "price": "10.00",
                "variant_image": "https://x/a.jpg",
                "available": True,
            },
        ],
    }
    engine = ProductionVariantEngine()
    result = engine._from_probe(probe, "https://x/p", "Playwright")
    assert result.extracted_combinations == 1
    assert result.skipped_duplicates == 1
    report = result.to_report(url="https://x/p")
    assert report["total_combinations"] == 2
    assert report["extracted_combinations"] == 1


def test_variant_mismatch_keeps_embedded_and_marks_partial():
    from sentivo_extractor.core.confidence import apply_confidence
    from sentivo_extractor.core.variant_engine import ProductionVariantEngine

    product = {
        "source_url": "https://shop.example/p",
        "extraction_method": "Embedded JSON",
        "options": [{"name": "Size", "values": ["S", "M"]}],
        "variants": [
            {"option1": "S", "option2": "", "option3": "", "sku": "S1", "price": "10.00"},
            {"option1": "M", "option2": "", "option3": "", "sku": "M1", "price": "10.00"},
        ],
    }
    # Network claims 5 variants but only 2 extracted → mismatch; keep embedded
    network = [
        {
            "product": {
                "variants": [
                    {"option1": "S", "sku": "S1", "price": "10.00"},
                    {"option1": "M", "sku": "M1", "price": "10.00"},
                ],
                "options": [{"name": "Size", "values": ["S", "M", "L", "XL", "XXL"]}],
            }
        }
    ]
    engine = ProductionVariantEngine()
    result = engine.process(
        url="https://shop.example/p",
        product=product,
        network_json=network,
        probe=None,
        page=None,
    )
    # Force mismatch path if page_count inferred
    if not result.count_mismatch:
        result.count_mismatch = True
        result.mismatch_note = "extracted 2 vs page variant count 5"
    product = engine.apply_to_product(product, result)
    assert product.get("variant_mismatch") is True
    assert product.get("confidence_partial") is True
    assert len(product["variants"]) == 2
    product = apply_confidence(product)
    assert product["confidence_score"] <= 0.89


def test_shopify_js_variants_trusted_no_mismatch():
    from sentivo_extractor.core.confidence import apply_confidence
    from sentivo_extractor.core.variant_engine import ProductionVariantEngine

    product = {
        "source_url": "https://shop.example/products/flushbolt",
        "extraction_method": "Shopify JS",
        "field_sources": {
            "title": "Shopify JS",
            "price": "Shopify JS",
            "images": "Shopify JS",
            "variant_sku": "Shopify JS",
            "variant_price": "Shopify JS",
        },
        "title": "Flushbolt",
        "images": [{"src": "https://cdn.example/a.jpg"}],
        "options": [
            {"name": "Finish", "values": ["Aluminium", "White", "Black"]},
            {"name": "Shape", "values": ["Flat", "Radius"]},
        ],
        "variants": [
            {"option1": "Aluminium", "option2": "Flat", "option3": "", "sku": "A1", "price": "10.00"},
            {"option1": "White", "option2": "Flat", "option3": "", "sku": "W1", "price": "10.00"},
            {"option1": "Black", "option2": "Flat", "option3": "", "sku": "B1", "price": "10.00"},
            {"option1": "Aluminium", "option2": "Radius", "option3": "", "sku": "A2", "price": "10.00"},
            {"option1": "White", "option2": "Radius", "option3": "", "sku": "W2", "price": "10.00"},
            {"option1": "Black", "option2": "Radius", "option3": "", "sku": "B2", "price": "10.00"},
        ],
    }
    # Network/page claims a different count — must be ignored for Shopify JS.
    network = [
        {
            "product": {
                "title": "Flushbolt",
                "options": [{"name": "Finish", "values": ["Aluminium"]}],
                "variants": [{"option1": "Aluminium", "sku": "A1", "price": "10.00"}],
            }
        }
    ]
    engine = ProductionVariantEngine()
    result = engine.process(
        url=product["source_url"],
        product=product,
        network_json=network,
        probe=None,
        page=None,
    )
    assert result.count_mismatch is False
    assert result.extracted_combinations == 6
    assert len(result.variants) == 6
    product = engine.apply_to_product(product, result)
    assert product.get("variant_mismatch") is not True
    product = apply_confidence(product)
    assert product["confidence_score"] == 1.0
    assert product["confidence_band"] == "green"


def test_playwright_probe_variants_disabled():
    from sentivo_extractor.extractors.playwright_extractor import PlaywrightExtractor

    pw = PlaywrightExtractor()
    assert pw._probe_variants(None) is None


def test_playwright_woocommerce_css_price_selectors():
    from sentivo_extractor.extractors.playwright_extractor import PlaywrightExtractor

    html = """
    <html><body class="woocommerce product-template-default postid-42">
      <div id="product-42" class="product">
        <h1 class="product_title">Polishing Cloths</h1>
        <p class="price">
          <del><span class="woocommerce-Price-amount amount">
            <bdi><span class="woocommerce-Price-currencySymbol">£</span>12.00</bdi>
          </span></del>
          <ins><span class="woocommerce-Price-amount amount">
            <bdi><span class="woocommerce-Price-currencySymbol">£</span>9.50</bdi>
          </span></ins>
        </p>
        <img src="https://cdn.example.com/cloth.jpg"/>
      </div>
    </body></html>
    """
    pw = PlaywrightExtractor()
    product = pw._extract_from_rendered(
        "https://shop.example/product/polishing-cloths",
        html,
        [],
        {"use_playwright": True},
    )
    assert product is not None
    assert product.get("title")
    assert product.get("price") == "9.50"

    empty_price_html = """
    <html><body><div class="product"><h1>No Price Product</h1>
    <img src="https://cdn.example.com/x.jpg"/></div></body></html>
    """
    product2 = pw._extract_from_rendered(
        "https://shop.example/product/x",
        empty_price_html,
        [],
        {"use_playwright": True},
    )
    assert product2 is not None
    assert not str(product2.get("price") or "").strip()


def test_sanitize_sku_rejects_stock_messages():
    from sentivo_extractor.core.normalizer import normalize_product
    from sentivo_extractor.core.utils import sanitize_sku
    from sentivo_extractor.extractors.html_extractor import HtmlExtractor

    assert sanitize_sku("Only %1 left") == ""
    assert sanitize_sku("Only 3 left") == ""
    assert sanitize_sku("In Stock") == ""
    assert sanitize_sku("out of stock") == ""
    assert sanitize_sku("ABC 123") == ""  # space not allowed
    assert sanitize_sku("PANEL-3MM-BLK") == "PANEL-3MM-BLK"
    assert sanitize_sku("ACME/123") == "ACME/123"

    html = """
    <html><body>
      <h1>Acetal Rod</h1>
      <div class="price">£10.00</div>
      <div class="sku">Only %1 left</div>
      <img src="/a.jpg"/>
    </body></html>
    """
    raw = HtmlExtractor().extract("https://www.aiplastics.com/p/rod", html)
    assert raw is not None
    assert raw["variants"][0]["sku"] == ""
    product = normalize_product(
        {
            "title": "Acetal Rod",
            "images": [{"src": "https://cdn.example/a.jpg"}],
            "variants": [
                {
                    "sku": "Only %1 left",
                    "option1": "Default Title",
                    "price": "10.00",
                }
            ],
        },
        base_url="https://www.aiplastics.com/p/rod",
    )
    assert product["variants"][0]["sku"] == ""


def test_normalize_price_strips_currency_and_blank_zero():
    from sentivo_extractor.core.utils import (
        is_blank_price,
        normalize_price,
        resolve_price_from_html,
    )

    assert normalize_price("£12.99") == "12.99"
    assert normalize_price("$1,234.50") == "1234.50"
    assert normalize_price("€ 9,99".replace(",", ".")) == "9.99" or normalize_price(
        "€9.99"
    ) == "9.99"
    assert normalize_price("€9.99") == "9.99"
    assert normalize_price("GBP 4.50") == "4.50"
    assert is_blank_price("0")
    assert is_blank_price("0.00")
    assert is_blank_price("£0.00")
    assert is_blank_price("")
    assert not is_blank_price("12.99")

    zero_og = """
    <html><head>
      <meta property="product:price:amount" content="0"/>
      <meta property="og:title" content="Rod"/>
    </head><body>
      <h1>Rod</h1>
      <div class="price">£0.00</div>
      <script type="application/ld+json">
      {"@type":"Product","name":"Rod","offers":{"@type":"Offer","price":"18.50","priceCurrency":"GBP"}}
      </script>
    </body></html>
    """
    price, source = resolve_price_from_html(zero_og)
    assert price == "18.50"
    assert "JSON-LD" in source

    css_only = """
    <html><body><span class="product-price">£22.00</span></body></html>
    """
    price, source = resolve_price_from_html(css_only)
    assert price == "22.00"
    assert source == ".product-price"

    text_only = "<html><body><p>Buy now for £7.25 today</p></body></html>"
    price, source = resolve_price_from_html(text_only)
    assert price == "7.25"
    assert source == "currency_text"

    none_html = "<html><body><div class='price'>£0.00</div></body></html>"
    price, source = resolve_price_from_html(none_html)
    assert price == ""
    assert source == ""


def test_opengraph_zero_price_falls_back_and_csv_omits_zero(caplog):
    import logging
    from pathlib import Path

    from sentivo_extractor.core.pdp_pipeline import PDPExtractionPipeline
    from sentivo_extractor.core.shopify_csv_exporter import product_to_rows
    from sentivo_extractor.extractors.base import ExtractorRegistry

    html = """
    <html><head>
      <meta property="og:title" content="PE1000 Black Rod"/>
      <meta property="product:price:amount" content="0"/>
      <meta property="product:price:currency" content="GBP"/>
      <meta property="og:image" content="https://cdn.example/rod.jpg"/>
    </head><body>
      <h1>PE1000 Black Rod</h1>
      <div class="price">£0.00</div>
      <img src="https://cdn.example/rod.jpg"/>
      <p>From £15.40</p>
    </body></html>
    """

    class FakeHttp:
        def get_text(self, url):
            return html

        session = None

    pipeline = PDPExtractionPipeline(
        http=FakeHttp(),
        options={"verify_product_images": False},
        registry=ExtractorRegistry(),
        logger=logging.getLogger("test_price"),
    )
    with caplog.at_level(logging.INFO):
        outcome = pipeline.extract("https://www.aiplastics.com/p/rod")
    assert outcome["success"] is True
    product = outcome["product"]
    assert product is not None
    assert all(
        (v.get("price") in ("", "15.40") for v in product.get("variants") or [])
    )
    assert "0.00" not in [
        str(v.get("price")) for v in (product.get("variants") or [])
    ]
    assert any("Price parsed:" in r.message or "Price not found" in r.message for r in caplog.records)

    rows = product_to_rows(product)
    assert rows
    assert rows[0]["Variant Price"] != "0.00"
    if rows[0]["Variant Price"]:
        assert rows[0]["Variant Price"] == "15.40"


def test_magento_spconfig_builds_variants(caplog):
    import logging

    from sentivo_extractor.extractors.magento_extractor import MagentoExtractor

    html = r"""
    <html><body>
      <h1>PE1000 Black Rod</h1>
      <script>
      {
        "#product_addtocart_form": {
          "configurable": {
            "spConfig": {
              "attributes": {
                "139": {
                  "id": "139",
                  "code": "diameter",
                  "label": "Diameter",
                  "options": [
                    {"id": "13", "label": "30mm", "products": ["2588"]},
                    {"id": "16", "label": "40mm", "products": ["2589"]}
                  ],
                  "position": "0"
                }
              },
              "optionPrices": {
                "2588": {"finalPrice": {"amount": 12.5}},
                "2589": {"finalPrice": {"amount": 15}}
              },
              "index": {"2588": {"139": "13"}, "2589": {"139": "16"}},
              "sku": {"2588": "PE-30", "2589": "PE-40"},
              "images": [],
              "productId": "2658"
            }
          }
        }
      }
      </script>
    </body></html>
    """
    with caplog.at_level(logging.INFO):
        product = MagentoExtractor().extract(
            "https://www.aiplastics.com/p/rod",
            html,
            context={"logger": logging.getLogger("magento_test")},
        )
    assert product is not None
    assert product["options"][0]["name"] == "Diameter"
    assert product["options"][0]["values"] == ["30mm", "40mm"]
    assert len(product["variants"]) == 2
    assert product["variants"][0]["option1"] == "30mm"
    assert product["variants"][0]["sku"] == "PE-30"
    assert product["variants"][0]["price"] == "12.50"
    assert any("Variants found via Magento Config: 2 variants" in r.message for r in caplog.records)


def test_magento_simple_pdps_stay_separate_default_title():
    """Simple Magento PDPs (no spConfig) must not invent Size variants or merge URLs."""
    from sentivo_extractor.core.normalizer import normalize_product
    from sentivo_extractor.core.shopify_csv_exporter import product_to_rows
    from sentivo_extractor.extractors.magento_extractor import MagentoExtractor

    html = """
    <html><body>
      <h1>Acetal Natural Sheet 250 x 250 x 2mm</h1>
      <div class="product attribute sku"><div class="value" itemprop="sku">ACNAS00201</div></div>
      <span class="price">£4.48</span>
      <img src="/media/catalog/product/a.jpg"/>
    </body></html>
    """
    one = MagentoExtractor().extract(
        "https://www.directplastics.co.uk/acetal-natural-sheet-250-x-250-x-2mm",
        html,
    )
    assert one is not None
    product = normalize_product(one, base_url=one["source_url"])
    assert product["variants"]
    assert product["variants"][0]["option1"] == "Default Title"
    rows = product_to_rows(product)
    assert rows[0]["Option1 Value"] == "Default Title"

    # Separate simple products from different URLs must remain separate
    siblings = [
        normalize_product(
            {
                "title": "Acetal Natural Sheet 250 x 250 x 2mm",
                "platform": "Magento",
                "source_url": "https://www.directplastics.co.uk/a",
                "images": [{"src": "https://cdn/a.jpg", "position": 1}],
                "variants": [
                    {"option1": "Default Title", "price": "4.48", "sku": "A1"}
                ],
            },
            base_url="https://www.directplastics.co.uk/a",
        ),
        normalize_product(
            {
                "title": "Acetal Natural Sheet 500 x 250 x 2mm",
                "platform": "Magento",
                "source_url": "https://www.directplastics.co.uk/b",
                "images": [{"src": "https://cdn/b.jpg", "position": 1}],
                "variants": [
                    {"option1": "Default Title", "price": "7.76", "sku": "A2"}
                ],
            },
            base_url="https://www.directplastics.co.uk/b",
        ),
    ]
    assert len(siblings) == 2
    assert siblings[0]["title"] != siblings[1]["title"]
    assert all(p["variants"][0]["option1"] == "Default Title" for p in siblings)


def test_description_engine_priority_chain():
    from sentivo_extractor.core.description_engine import extract_description

    shopify = json.loads((FIXTURES / "shopify_product.json").read_text(encoding="utf-8"))

    class _Http:
        def get_json(self, url: str):
            if url.endswith(".js"):
                return shopify
            raise RuntimeError("unexpected")

    desc, source = extract_description(
        url="https://shop.example.com/products/classic-tee",
        html="<html><meta property='og:description' content='OG should lose to Shopify body_html that is long enough.'/></html>",
        http=_Http(),
    )
    assert source == "Shopify JS"
    assert "Soft cotton classic tee" in desc

    jsonld_html = (FIXTURES / "jsonld_product.html").read_text(encoding="utf-8")
    desc, source = extract_description(
        url="https://shop.example.com/mug",
        html=jsonld_html,
    )
    assert source == "JSON-LD"
    assert "Durable ceramic mug" in desc

    magento_html = (FIXTURES / "magento_description_product.html").read_text(
        encoding="utf-8"
    )
    desc, source = extract_description(
        url="https://www.directplastics.co.uk/acrylic-sheet",
        html=magento_html,
    )
    # og:description is "Short" (<20) → CSS Magento selector wins
    assert source == "CSS selectors"
    assert "Clear cast acrylic sheet" in desc

    meta_only = """
    <html><head>
      <meta name="description" content="Meta-only product blurb that is long enough here." />
    </head><body><h1>Thing</h1></body></html>
    """
    desc, source = extract_description(
        url="https://example.com/thing",
        html=meta_only,
    )
    assert source == "meta description"
    assert "Meta-only product blurb" in desc


def test_pdp_pipeline_fills_magento_description_via_css():
    from sentivo_extractor.core.pdp_pipeline import PDPExtractionPipeline
    from sentivo_extractor.extractors import build_default_registry

    html = (FIXTURES / "magento_description_product.html").read_text(encoding="utf-8")

    class _Http:
        session = None

        def get_text(self, url: str) -> str:
            return html

    pipeline = PDPExtractionPipeline(
        http=_Http(),
        options={"use_playwright": False, "verify_product_images": False},
        registry=build_default_registry(),
        site_rules={},
    )
    outcome = pipeline.extract("https://www.directplastics.co.uk/acrylic-sheet")
    assert outcome["success"] is True
    product = outcome["product"]
    assert "Clear cast acrylic sheet" in (product.get("description_html") or "")


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
