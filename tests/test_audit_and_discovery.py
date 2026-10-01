"""Tests for audit workflow, discovery, confidence, extended CSV, Playwright mapping."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"


def test_extended_csv_columns():
    from sentivo_extractor.core.input_csv import (
        apply_seed_metadata,
        read_seed_csv,
        seed_metadata,
    )

    csv_text = (
        "url,type,department,source_name,vendor,product_type,tags,currency,expected_count,priority\n"
        "https://a.example/p/1,product,Panels,Hashim,Acme,Sheet,\"tag1, tag2\",GBP,10,high\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "seeds.csv"
        path.write_text(csv_text, encoding="utf-8")
        rows = read_seed_csv(path)
    assert len(rows) == 1
    meta = seed_metadata(rows[0])
    assert meta["department"] == "Panels"
    assert meta["vendor"] == "Acme"
    assert meta["tags"] == ["tag1", "tag2"]
    assert meta["expected_count"] == 10
    product = {"title": "X", "tags": ["existing"], "vendor": ""}
    product = apply_seed_metadata(product, meta)
    assert product["department"] == "Panels"
    assert product["vendor"] == "Acme"
    assert "tag1" in product["tags"] and "existing" in product["tags"]


def test_sitemap_index_parsing():
    from sentivo_extractor.core.product_discovery import (
        expand_sitemap_index,
        is_sitemap_index,
        discover_from_sitemap,
        clear_sitemap_failure_cache,
    )

    clear_sitemap_failure_cache()
    index_xml = """<?xml version="1.0"?>
    <sitemapindex>
      <sitemap><loc>https://shop.example/sitemap_products_1.xml</loc></sitemap>
      <sitemap><loc>https://shop.example/sitemap_pages_1.xml</loc></sitemap>
    </sitemapindex>
    """
    products_xml = """<?xml version="1.0"?>
    <urlset>
      <url><loc>https://shop.example/products/alpha</loc></url>
      <url><loc>https://shop.example/products/beta?utm_source=x</loc></url>
      <url><loc>https://shop.example/pages/about</loc></url>
    </urlset>
    """
    assert is_sitemap_index(index_xml)

    def get_text(url: str) -> str:
        if "products" in url:
            return products_xml
        return "<urlset></urlset>"

    found = expand_sitemap_index(index_xml, get_text)
    assert "https://shop.example/products/alpha" in found
    assert "https://shop.example/products/beta" in found  # utm stripped
    assert all("/pages/" not in u for u in found)


def test_skip_shopify_sitemap_probe_for_custom_platform():
    from sentivo_extractor.core.product_discovery import (
        clear_sitemap_failure_cache,
        collect_sitemap_seed_urls,
        should_probe_shopify_product_sitemaps,
    )

    clear_sitemap_failure_cache()
    assert should_probe_shopify_product_sitemaps("Custom", []) is False
    assert should_probe_shopify_product_sitemaps("Shopify", []) is True
    # Listing in robots no longer invents extra files; probe flag is Shopify-only
    assert (
        should_probe_shopify_product_sitemaps(
            "Custom", ["https://x.example/sitemap_products_1.xml"]
        )
        is False
    )

    fetched: list[str] = []

    def get_text(url: str) -> str:
        fetched.append(url)
        if url.endswith("robots.txt"):
            return "User-agent: *\nSitemap: https://directplastics.co.uk/sitemap.xml\n"
        raise AssertionError(f"unexpected fetch during seed collection: {url}")

    urls, notes = collect_sitemap_seed_urls(
        "https://directplastics.co.uk",
        get_text,
        platform="Custom",
    )
    assert any("sitemap.xml" in u for u in urls)
    assert not any("sitemap_products" in u for u in urls)
    assert any("Skipping Shopify sitemap probe (platform=Custom)" in n for n in notes)


def test_sitemap_failure_cache_skips_repeat():
    from sentivo_extractor.core.product_discovery import (
        _fetch_sitemap_text,
        clear_sitemap_failure_cache,
    )

    clear_sitemap_failure_cache()
    calls = {"n": 0}

    def get_text(url: str) -> str:
        calls["n"] += 1
        raise RuntimeError("404 Client Error: Not Found")

    url = "https://x.example/sitemap.xml"
    assert _fetch_sitemap_text(get_text, url) is None
    assert calls["n"] == 1
    assert _fetch_sitemap_text(get_text, url) is None
    assert calls["n"] == 1  # cached — not requested again

    # Invented Shopify product sitemaps are blocked unless explicitly allowed
    assert (
        _fetch_sitemap_text(get_text, "https://x.example/sitemap_products_1.xml") is None
    )
    assert calls["n"] == 1
    assert (
        _fetch_sitemap_text(
            get_text,
            "https://x.example/sitemap_products_1.xml",
            allow_shopify_product_sitemaps=True,
        )
        is None
    )
    assert calls["n"] == 2
    assert (
        _fetch_sitemap_text(
            get_text,
            "https://x.example/sitemap_products_1.xml",
            allow_shopify_product_sitemaps=True,
        )
        is None
    )
    assert calls["n"] == 2


def test_shopify_platform_still_probes_product_sitemaps():
    from sentivo_extractor.core.product_discovery import (
        clear_sitemap_failure_cache,
        collect_sitemap_seed_urls,
    )

    clear_sitemap_failure_cache()

    def get_text(url: str) -> str:
        if url.endswith("robots.txt"):
            return "User-agent: *\n"
        raise AssertionError(url)

    urls, notes = collect_sitemap_seed_urls(
        "https://shop.example",
        get_text,
        platform="Shopify",
        max_shopify_files=3,
    )
    assert "https://shop.example/sitemap_products_1.xml" in urls
    assert "https://shop.example/sitemap_products_3.xml" in urls
    assert not any("Skipping Shopify" in n for n in notes)


def test_custom_discovery_never_requests_shopify_product_sitemaps():
    """Regression: Custom sites must not hit sitemap_products_1..20."""
    from sentivo_extractor.core.product_discovery import (
        PRODUCT_DISCOVERY_VERSION,
        clear_sitemap_failure_cache,
        discover_domain_products,
    )

    clear_sitemap_failure_cache()
    fetched: list[str] = []

    def get_text(url: str) -> str:
        fetched.append(url)
        if url.endswith("robots.txt"):
            return "User-agent: *\n"
        if url.endswith("sitemap.xml"):
            return """<?xml version="1.0"?><urlset>
              <url><loc>https://www.directplastics.co.uk/acrylic-sheet</loc></url>
            </urlset>"""
        if "directplastics" in url:
            return "<html><body><a href='/acrylic-sheet'>Acrylic</a></body></html>"
        return "<html></html>"

    result = discover_domain_products(
        "https://www.directplastics.co.uk/",
        get_text,
        platform="Custom",
        max_products=20,
        follow_sitemaps=True,
    )
    assert result["discovery_version"] == PRODUCT_DISCOVERY_VERSION == "Unified"
    assert any("Skipping Shopify sitemap probe (platform=Custom)" in n for n in result["notes"])
    assert not any("sitemap_products_" in u for u in fetched)
    assert not any("sitemap_products_" in u for u in (result.get("sitemap_urls") or []))


def test_retry_skips_permanent_http_codes():
    from sentivo_extractor.core.utils import retry_call

    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("404 Client Error: Not Found for url: https://x/a")

    try:
        retry_call(boom, retries=3, logger=None)
        assert False, "expected raise"
    except RuntimeError:
        pass
    assert calls["n"] == 1


def test_retry_retries_transient_http_codes():
    from sentivo_extractor.core.utils import retry_call

    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("503 Server Error: Service Unavailable")
        return "ok"

    assert retry_call(boom, retries=3, backoff=1.0, logger=None) == "ok"
    assert calls["n"] == 3


def test_shopify_products_1_permanent_stops_further_probes():
    from sentivo_extractor.core.product_discovery import (
        clear_sitemap_failure_cache,
        discover_domain_products,
    )

    clear_sitemap_failure_cache()
    fetched: list[str] = []

    def get_text(url: str) -> str:
        fetched.append(url)
        if url.endswith("robots.txt"):
            return "User-agent: *\n"
        if url.endswith("/sitemap.xml"):
            return "<?xml version='1.0'?><urlset></urlset>"
        if "sitemap_products_1.xml" in url:
            raise RuntimeError("404 Client Error: Not Found for url: " + url)
        if "sitemap_products_" in url:
            raise AssertionError(f"must not probe after products_1 permanent fail: {url}")
        return "<html><body>Shopify store</body></html>"

    result = discover_domain_products(
        "https://shop.example/",
        get_text,
        platform="Shopify",
        max_products=10,
        follow_sitemaps=True,
    )
    assert any("Shopify product sitemap unavailable" in n for n in result["notes"])
    assert sum(1 for u in fetched if "sitemap_products_" in u) == 1
    assert any("sitemap_products_1.xml" in u for u in fetched)


def test_duplicate_url_cleanup():
    from sentivo_extractor.core.product_discovery import (
        canonicalize_product_url,
        unique_preserve,
    )

    urls = [
        "https://Shop.Example/products/A/",
        "https://shop.example/products/a?utm_source=google",
        "https://shop.example/products/b",
        "https://shop.example/products/b?fbclid=123",
    ]
    cleaned = unique_preserve(urls)
    assert len(cleaned) == 2
    assert canonicalize_product_url(urls[0]) == cleaned[0]


def test_confidence_scoring_bands():
    from sentivo_extractor.core.confidence import (
        apply_confidence,
        confidence_band,
        score_product,
    )

    weak = {"title": "", "variants": [], "images": [], "description_html": ""}
    assert score_product(weak) < 0.70
    assert confidence_band(score_product(weak)) == "red"

    strong = {
        "title": "Good Product",
        "description_html": "<p>" + ("word " * 30) + "</p>",
        "images": [{"src": "https://cdn.example/a.jpg"}],
        "options": [{"name": "Size", "values": ["S", "M"]}],
        "variants": [
            {"option1": "S", "option2": "", "option3": "", "price": "10.00"},
            {"option1": "M", "option2": "", "option3": "", "price": "10.00"},
        ],
        "extraction_method": "shopify",
    }
    scored = apply_confidence(strong)
    assert scored["confidence_score"] >= 0.90
    assert scored["confidence_band"] == "green"


def test_playwright_variant_combination_mapping():
    from sentivo_extractor.extractors.playwright_extractor import PlaywrightExtractor

    html = (FIXTURES / "js_rendered_product.html").read_text(encoding="utf-8")
    # Simulate network JSON product payload
    network = [
        {
            "url": "https://spa.example.com/api/product.json",
            "data": {
                "product": {
                    "title": "City Runner Sneaker",
                    "handle": "city-runner-sneaker",
                    "description": "Lightweight sneaker engineered for all-day urban comfort.",
                    "images": [{"src": "/img/city-runner.jpg"}],
                    "options": [
                        {"name": "Size", "values": ["40", "41"]},
                        {"name": "Color", "values": ["Navy"]},
                    ],
                    "variants": [
                        {
                            "sku": "CR-40-NV",
                            "option1": "40",
                            "option2": "Navy",
                            "price": "89.00",
                            "available": True,
                        },
                        {
                            "sku": "CR-41-NV",
                            "option1": "41",
                            "option2": "Navy",
                            "price": "89.00",
                            "available": True,
                        },
                        {
                            "sku": "CR-40-NV-DUP",
                            "option1": "40",
                            "option2": "Navy",
                            "price": "89.00",
                            "available": True,
                        },
                    ],
                }
            },
        }
    ]
    raw = PlaywrightExtractor().extract(
        "https://spa.example.com/sneakers/city",
        html="",
        context={
            "use_playwright": True,
            "rendered_html": html,
            "network_json": network,
        },
    )
    assert raw is not None
    assert raw["title"] == "City Runner Sneaker"
    assert "network_json" in raw["extraction_method"]
    keys = {
        (v.get("option1"), v.get("option2"), v.get("option3")) for v in raw["variants"]
    }
    assert len(keys) == len(raw["variants"])  # no duplicate combos after merge
    # Network variants may still contain dup before merge — extractor should keep unique if probed.
    # For network path, dedupe in normalizer:
    from sentivo_extractor.core.normalizer import normalize_product

    product = normalize_product(raw)
    keys2 = {
        (v.get("option1"), v.get("option2"), v.get("option3"))
        for v in product["variants"]
    }
    assert len(keys2) == len(product["variants"])


def test_playwright_merge_probe_dedupes():
    from sentivo_extractor.extractors.playwright_extractor import PlaywrightExtractor

    pw = PlaywrightExtractor()
    product = {
        "title": "Panel",
        "options": [],
        "variants": [],
        "confidence_score": 0.5,
    }
    probe = {
        "options": [{"name": "Size", "values": ["A4", "A3"]}],
        "variants": [
            {"option1": "A4", "option2": "", "option3": "", "price": "10.00"},
            {"option1": "A4", "option2": "", "option3": "", "price": "10.00"},
            {"option1": "A3", "option2": "", "option3": "", "price": "12.00"},
        ],
    }
    merged = pw._merge_variant_probe(product, probe)
    assert len(merged["variants"]) == 2
    assert merged["options"][0]["name"] == "Size"


def test_magento_category_discovers_product_ids_without_hrefs():
    """Magento grids with data-product-id / cart forms must yield PDP URLs."""
    from sentivo_extractor.core.product_discovery import (
        discover_domain_products,
        discover_magento_from_category_html,
    )

    html = """
    <html><body class="catalog-category-view">
      <script>mage/requirejs</script>
      <div class="product-item">
        <strong class="product-item-name">Acetal Sheet 2mm</strong>
        <div class="price-box" data-product-id="517" data-price-box="product-id-517"></div>
        <form action="https://mage.example/checkout/cart/add/uenc/abc/product/517/"
              data-role="tocart-form" method="post">
          <input name="product" type="hidden" value="517"/>
        </form>
        <a href="#">Wishlist</a>
      </div>
      <div class="product-item">
        <div class="price-box" data-product-id="520"></div>
        <form action="https://mage.example/checkout/cart/add/uenc/xyz/product/520/"
              method="post">
          <input name="product" type="hidden" value="520"/>
        </form>
      </div>
      <div class="product-item">
        <a class="product-item-link" href="/acetal-sheet-black.html">Black</a>
      </div>
    </body></html>
    """
    found = discover_magento_from_category_html(
        html, "https://mage.example/acetal-sheet"
    )
    assert "https://mage.example/catalog/product/view/id/517" in found
    assert "https://mage.example/catalog/product/view/id/520" in found
    assert "https://mage.example/acetal-sheet-black.html" in found

    def get_text(url: str) -> str:
        if "catalogsearch" in url or url.endswith("robots.txt") or url.endswith(".xml"):
            raise RuntimeError("optional sources should not be required")
        return html

    result = discover_domain_products(
        "https://mage.example/acetal-sheet",
        get_text,
        max_products=50,
        follow_sitemaps=False,
        platform="Magento",
    )
    assert len(result["product_urls"]) >= 3
    assert any("category_html_products:" in n for n in result["notes"])
    assert "https://mage.example/catalog/product/view/id/517" in result["product_urls"]


def test_audit_command_offline(monkeypatch=None):
    """Run DomainAuditor with stubbed HTTP — no live network."""
    from sentivo_extractor.core.audit import DomainAuditor

    listing_html = """
    <html><body>
      <a href="/products/one">One</a>
      <a href="/products/two">Two</a>
      <a rel="next" href="/collections/all?page=2">Next</a>
    </body></html>
    """
    pdp_html = (FIXTURES / "jsonld_product.html").read_text(encoding="utf-8")
    sitemap = """<?xml version="1.0"?><urlset>
      <url><loc>https://audit.example/products/one</loc></url>
      <url><loc>https://audit.example/products/two</loc></url>
    </urlset>"""

    def fake_get_text(url: str) -> str:
        if "robots.txt" in url:
            return "Sitemap: https://audit.example/sitemap.xml\n"
        if "sitemap" in url:
            return sitemap
        if "/products/" in url:
            return pdp_html
        return listing_html

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "audit"
        seeds = Path(tmp) / "seeds.csv"
        seeds.write_text(
            "url,type,department,vendor\n"
            "https://audit.example/collections/all,category,Drinkware,Drinkware Co\n",
            encoding="utf-8",
        )
        auditor = DomainAuditor(
            {
                "output": str(out),
                "use_playwright": False,
                "sample_size": 2,
                "max_products_per_domain": 50,
                "respect_robots": False,
                "delay": 0,
            }
        )
        auditor.http.get_text = fake_get_text  # type: ignore[method-assign]
        summary = auditor.run(seeds)
        assert summary["domains"] == 1
        assert summary["product_urls_found"] >= 2
        assert (out / "product_discovery_report.csv").exists()
        assert (out / "domain_audit.xlsx").exists() or (out / "domain_audit.csv").exists()
        assert (out / "sample_products_raw").exists()
        # suggestions may or may not exist depending on confidence
        assert (out / "site_rule_suggestions").is_dir()


def test_cli_audit_subcommand_help():
    from sentivo_extractor import cli as cli_mod

    with tempfile.TemporaryDirectory() as tmp:
        seeds = Path(tmp) / "s.csv"
        seeds.write_text(
            "url,type\nhttps://example.com/products/x,product\n", encoding="utf-8"
        )
        out = Path(tmp) / "audit"

        class FakeAuditor:
            def __init__(self, options):
                self.options = options
                Path(options["output"]).mkdir(parents=True, exist_ok=True)

            def run(self, path):
                return {
                    "domains": 1,
                    "seeds": 1,
                    "product_urls_found": 0,
                    "output_dir": self.options["output"],
                }

        original = cli_mod.DomainAuditor if hasattr(cli_mod, "DomainAuditor") else None

        def fake_run_audit(args):
            options = cli_mod._options_from_args(args)
            options["output"] = str(out)
            summary = FakeAuditor(options).run(Path(args.input))
            print("\n=== Audit complete ===")
            for k, v in summary.items():
                print(f"{k}: {v}")
            return 0

        old = cli_mod.run_audit
        cli_mod.run_audit = fake_run_audit
        try:
            code = cli_mod.run_cli(
                [
                    "audit",
                    "--input",
                    str(seeds),
                    "--output",
                    str(out),
                    "--use-playwright",
                    "false",
                ]
            )
        finally:
            cli_mod.run_audit = old
        assert code == 0
        assert out.exists()


if __name__ == "__main__":
    tests = [
        test_extended_csv_columns,
        test_sitemap_index_parsing,
        test_skip_shopify_sitemap_probe_for_custom_platform,
        test_sitemap_failure_cache_skips_repeat,
        test_shopify_platform_still_probes_product_sitemaps,
        test_custom_discovery_never_requests_shopify_product_sitemaps,
        test_retry_skips_permanent_http_codes,
        test_retry_retries_transient_http_codes,
        test_shopify_products_1_permanent_stops_further_probes,
        test_duplicate_url_cleanup,
        test_confidence_scoring_bands,
        test_playwright_variant_combination_mapping,
        test_playwright_merge_probe_dedupes,
        test_magento_category_discovers_product_ids_without_hrefs,
        test_audit_command_offline,
        test_cli_audit_subcommand_help,
    ]
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
