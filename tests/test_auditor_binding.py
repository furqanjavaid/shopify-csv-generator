"""Regression tests: auditor UI binding + report consistency."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _sample_findings():
    from app.core.store_auditor import _finding

    return [
        _finding(
            2,
            "Reviews Present",
            "HIGH",
            "product_page",
            False,
            "No reviews or star ratings on product page",
            "No review widgets found",
            "Trust gap",
            "Install a review app",
            ["https://shop.example/products/a"],
        ),
        _finding(
            4,
            "Price Visibility",
            "HIGH",
            "product_page",
            True,
            "Price clearly visible",
            "4 reviews",  # misleading capture — must not imply reviews pass
            "Pricing clarity",
            "Show price clearly",
            ["https://shop.example/products/a"],
        ),
        _finding(
            6,
            "Mobile ATC Tap Target",
            "HIGH",
            "mobile",
            True,
            "Mobile ATC tap target OK",
            "tap target ≥44px: True",
            "Tap size",
            "Keep 48px min",
            ["https://shop.example/products/a"],
        ),
        _finding(
            61,
            "Mobile ATC Above Fold",
            "HIGH",
            "mobile",
            False,
            "Mobile ATC below fold or missing",
            "mobile_atc_above_fold=False",
            "Fold visibility",
            "Keep ATC above fold",
            ["https://shop.example/products/a"],
        ),
        _finding(
            1,
            "ATC Button Above Fold",
            "HIGH",
            "product_page",
            False,
            "Add to Cart button is below the fold or missing",
            "ATC y-position: 1200px",
            "Missed CTA",
            "Move ATC above fold",
            ["https://shop.example/products/a"],
        ),
    ]


def test_issue_field_preferred_over_literal_issue():
    """UI binding must use finding['issue'], not a hardcoded 'Issue' fallback."""
    findings = _sample_findings()
    failed = [f for f in findings if not f.get("passed")]
    for f in failed:
        text = f.get("issue") or f.get("check_name") or "—"
        assert text != "Issue"
        assert "Issue" != text  # literal placeholder must not win
        assert len(str(text)) > 4


def test_recommendation_title_not_generic():
    findings = _sample_findings()
    recs = [f for f in findings if not f.get("passed") and f.get("fix")]
    assert recs
    for f in recs:
        title = f.get("check_name") or f.get("issue") or "—"
        assert title != "Recommendation"
        assert "Reviews" in title or "ATC" in title or "Add to Cart" in title or "Mobile" in title


def test_score_cards_do_not_duplicate_overall_onto_missing_metrics():
    """SEO / Content must not fall back to overall when those keys are absent."""
    scores = {
        "overall": 5.1,
        "categories": {
            "product_page": 4.0,
            "cart_checkout": 6.0,
            "mobile": 5.0,
            "speed": 7.0,
            "marketing": 5.0,
        },
    }
    cats = scores.get("categories") or {}
    overall = scores.get("overall", "—")
    seo = scores.get("seo", scores.get("SEO", cats.get("seo", "—")))
    content = scores.get("content", cats.get("content", "—"))

    assert overall == 5.1
    assert seo == "—"
    assert content == "—"
    # Unrelated category scores must not silently become the three cards
    assert seo != overall
    assert content != overall


def test_review_fail_not_in_passing_and_price_message_not_claim_reviews():
    from app.core.audit_report import format_passing_message

    findings = _sample_findings()
    raw = {
        "product_has_reviews": False,
        "price_text": "4 reviews",
        "price_visible": True,
        "mobile_atc_above_fold": False,
        "mobile_atc_tap_target_ok": True,
    }

    failed_ids = {
        f.get("check_id")
        for f in findings
        if f.get("passed") is False or f.get("status") == "fail"
    }
    passes = [
        f
        for f in findings
        if f.get("passed") is True
        and f.get("status") == "pass"
        and f.get("check_id") not in failed_ids
    ]
    pass_ids = {f.get("check_id") for f in passes}
    assert 2 not in pass_ids  # reviews fail must not pass
    assert 4 in pass_ids

    price_msg = format_passing_message(next(f for f in passes if f["check_id"] == 4), raw)
    assert "4 reviews" not in price_msg.lower()
    assert "review widget" not in price_msg.lower()


def test_mobile_above_fold_fail_and_tap_pass_are_distinct():
    from app.core.audit_report import enrich_finding, format_passing_message
    from app.core.store_auditor import build_report_findings

    raw = {
        "product_url": "https://shop.example/products/a",
        "mobile_atc_above_fold": False,
        "mobile_atc_tap_target_ok": True,
        "atc_above_fold": True,
        "product_has_reviews": False,
        "trust_badge_count": 0,
        "price_visible": True,
        "price_text": "$19.99",
        "cart_has_checkout_btn": True,
        "homepage_load_time": 0.8,
        "has_email_capture": True,
        "screenshots": {},
    }
    findings = build_report_findings(raw, "https://shop.example")
    by_id = {f["check_id"]: f for f in findings}
    assert by_id[6]["passed"] is True
    assert by_id[61]["passed"] is False

    tap_msg = format_passing_message(by_id[6], raw).lower()
    assert "tap target" in tap_msg
    assert "above fold" not in tap_msg

    # Failed fold check must not appear among passes
    passes = [f for f in findings if f.get("passed") is True and f.get("status") == "pass"]
    assert all(f.get("check_id") != 61 for f in passes)

    enriched_fold = enrich_finding(by_id[61], raw)
    assert "Below the Fold" in enriched_fold.get("issue", "")
    fix = (enriched_fold.get("fix") or "").lower()
    assert "sticky" in fix or "above the fold" in fix or "position" in fix
    assert "44" not in fix  # must not recommend tap-target size when fold failed


def test_build_report_findings_reviews_follow_raw_flag_only():
    from app.core.store_auditor import build_report_findings

    raw = {
        "product_url": "https://shop.example/products/a",
        "product_has_reviews": False,
        "price_visible": True,
        "price_text": "4 reviews",
        "atc_above_fold": True,
        "mobile_atc_above_fold": True,
        "mobile_atc_tap_target_ok": True,
        "cart_has_checkout_btn": True,
        "homepage_load_time": 1.0,
        "screenshots": {},
    }
    findings = build_report_findings(raw, "https://shop.example")
    reviews = next(f for f in findings if f["check_id"] == 2)
    assert reviews["passed"] is False
    assert "No reviews" in reviews["issue"]


def test_product_gallery_images_not_zero_for_shopify_lazy_gallery():
    """Lazy-loaded Shopify galleries must not yield product_image_count=0."""
    from app.core.store_auditor import (
        build_report_findings,
        canonical_product_image_key,
        extract_product_gallery_image_urls,
        normalize_product_image_url,
    )

    html = """
    <html><body>
      <div class="product__media-list">
        <img data-src="https://cdn.shopify.com/s/files/1/0001/products/hero_100x.jpg"
             srcset="https://cdn.shopify.com/s/files/1/0001/products/hero_200x.jpg 200w,
                     https://cdn.shopify.com/s/files/1/0001/products/hero_800x.jpg 800w"
             alt="Hero" />
        <picture>
          <source srcset="https://cdn.shopify.com/s/files/1/0001/products/side.webp" />
          <img data-lazy-src="https://cdn.shopify.com/s/files/1/0001/products/side_400x.webp" />
        </picture>
        <div class="product__media" data-media-id="3"
             style="background-image:url('https://cdn.shopify.com/s/files/1/0001/products/detail.jpg')"></div>
        <img src="/assets/logo.png" class="header__logo" />
      </div>
      <script type="application/ld+json">
      {"@type":"Product","image":[
        "https://cdn.shopify.com/s/files/1/0001/products/hero.jpg",
        "https://cdn.shopify.com/s/files/1/0001/products/lifestyle.jpg"
      ]}
      </script>
    </body></html>
    """
    urls = extract_product_gallery_image_urls(html)
    assert len(urls) >= 3
    assert normalize_product_image_url(
        "https://cdn.shopify.com/s/files/1/0001/products/hero_800x.jpg"
    ) == normalize_product_image_url(
        "https://cdn.shopify.com/s/files/1/0001/products/hero.jpg"
    )
    assert canonical_product_image_key(
        "https://shop.example/cdn/shop/files/hero.jpg?width=800"
    ) == canonical_product_image_key(
        "https://cdn.shopify.com/s/files/1/0001/files/hero_200x.jpg"
    )
    assert canonical_product_image_key(
        "https://cdn.shopify.com/s/files/1/0001/products/hero.jpg?width=400"
    ) == canonical_product_image_key(
        "https://shop.myshopify.com/cdn/shop/products/hero_800x.jpg"
    )
    assert not any("logo" in u for u in urls)

    raw = {
        "product_url": "https://shop.example/products/a",
        "product_image_count": len(urls),
        "atc_above_fold": True,
        "product_has_reviews": True,
        "price_visible": True,
        "mobile_atc_above_fold": True,
        "mobile_atc_tap_target_ok": True,
        "cart_has_checkout_btn": True,
        "homepage_load_time": 1.0,
        "screenshots": {},
    }
    findings = build_report_findings(raw, "https://shop.example")
    img = next(f for f in findings if f["check_id"] == 12)
    assert img["passed"] is True
    assert "0 image" not in img["issue"]


def test_shopify_srcset_width_variants_count_as_one_each():
    """5 logical images × many width variants → count == 5."""
    from app.core.store_auditor import extract_product_gallery_image_urls

    blocks = []
    for name in ("alpha", "bravo", "charlie", "delta", "echo"):
        blocks.append(
            f"""
            <img
              src="https://cdn.shopify.com/s/files/1/9/products/{name}.jpg?v=1&amp;width=100"
              srcset="
                https://cdn.shopify.com/s/files/1/9/products/{name}.jpg?width=200 200w,
                https://cdn.shopify.com/s/files/1/9/products/{name}.jpg?width=400&height=400&crop=center 400w,
                https://cdn.shopify.com/s/files/1/9/products/{name}_800x.jpg 800w,
                //cdn.shopify.com/s/files/1/9/products/{name}_grande.jpg 1200w
              "
              data-src="https://shop.myshopify.com/cdn/shop/products/{name}.jpg?width=1600&format=webp"
            />
            """
        )
    html = f'<div class="product__media-list">{"".join(blocks)}</div>'
    urls = extract_product_gallery_image_urls(html)
    assert len(urls) == 5


def test_same_image_src_lazy_and_jsonld_counts_once():
    from app.core.store_auditor import extract_product_gallery_image_urls

    html = """
    <div class="product-gallery">
      <img src="https://cdn.shopify.com/s/files/1/1/products/solo_200x.jpg"
           data-src="https://cdn.shopify.com/s/files/1/1/products/solo.jpg?width=800"
           data-lazy-src="//cdn.shopify.com/s/files/1/1/products/solo_grande.jpg" />
    </div>
    <script type="application/ld+json">
    {"@type":"Product","image":"https://cdn.shopify.com/s/files/1/1/products/solo.jpg?v=99"}
    </script>
    """
    assert len(extract_product_gallery_image_urls(html)) == 1


def test_distinct_product_filenames_remain_separate():
    from app.core.store_auditor import (
        canonical_product_image_key,
        extract_product_gallery_image_urls,
    )

    html = """
    <div class="product__media">
      <img src="https://cdn.shopify.com/s/files/1/1/products/front.jpg?width=400" />
      <img src="https://cdn.shopify.com/s/files/1/1/products/back.jpg?width=400" />
      <img src="https://cdn.shopify.com/s/files/1/1/products/detail.jpg?width=400" />
    </div>
    """
    urls = extract_product_gallery_image_urls(html)
    assert len(urls) == 3
    assert canonical_product_image_key(
        "https://cdn.shopify.com/s/files/1/1/products/front.jpg"
    ) != canonical_product_image_key(
        "https://cdn.shopify.com/s/files/1/1/products/back.jpg"
    )


def test_logo_and_icon_assets_excluded_from_gallery_count():
    from app.core.store_auditor import extract_product_gallery_image_urls

    html = """
    <div class="product__media-list">
      <img src="https://cdn.shopify.com/s/files/1/1/products/item.jpg?width=600" />
      <img src="https://cdn.shopify.com/s/files/1/1/files/logo.png" class="header-logo" />
      <img src="/assets/icon-cart.svg" />
      <img src="https://cdn.shopify.com/s/files/1/1/files/payment-badge.png" />
    </div>
    """
    urls = extract_product_gallery_image_urls(html)
    assert len(urls) == 1
    assert all("logo" not in u and "badge" not in u and "icon" not in u for u in urls)


def test_passing_checks_use_canonical_email_and_nav_counts():
    from app.core.audit_report import format_passing_message
    from app.core.store_auditor import _finding

    raw = {
        "email_capture_count": 3,
        "has_email_capture": True,
        "nav_links_count": 4,
        "trust_badge_count": 0,
        "trust_text_match": None,
        "homepage_load_time": 0.9,
        "meta_title": "Demo",
    }
    email = _finding(
        8, "Email Capture", "MEDIUM", "marketing", True,
        "Email capture present", "ok", "impact", "fix",
    )
    nav = _finding(
        201, "Desktop Nav Depth", "MEDIUM", "marketing", True,
        "Expanded navigation", "ok", "impact", "fix",
    )
    email_msg = format_passing_message(email, raw)
    nav_msg = format_passing_message(nav, raw)
    assert "3" in email_msg
    assert "0 capture" not in email_msg.lower()
    assert "4" in nav_msg
    assert "0 visible" not in nav_msg.lower()


if __name__ == "__main__":
    test_issue_field_preferred_over_literal_issue()
    test_recommendation_title_not_generic()
    test_score_cards_do_not_duplicate_overall_onto_missing_metrics()
    test_review_fail_not_in_passing_and_price_message_not_claim_reviews()
    test_mobile_above_fold_fail_and_tap_pass_are_distinct()
    test_build_report_findings_reviews_follow_raw_flag_only()
    test_product_gallery_images_not_zero_for_shopify_lazy_gallery()
    test_shopify_srcset_width_variants_count_as_one_each()
    test_same_image_src_lazy_and_jsonld_counts_once()
    test_distinct_product_filenames_remain_separate()
    test_logo_and_icon_assets_excluded_from_gallery_count()
    test_passing_checks_use_canonical_email_and_nav_counts()
    print("OK")
