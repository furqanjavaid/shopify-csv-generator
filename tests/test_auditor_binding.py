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
    from app.core.audit_report import format_passing_message
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


if __name__ == "__main__":
    test_issue_field_preferred_over_literal_issue()
    test_recommendation_title_not_generic()
    test_score_cards_do_not_duplicate_overall_onto_missing_metrics()
    test_review_fail_not_in_passing_and_price_message_not_claim_reviews()
    test_mobile_above_fold_fail_and_tap_pass_are_distinct()
    test_build_report_findings_reviews_follow_raw_flag_only()
    print("OK")
