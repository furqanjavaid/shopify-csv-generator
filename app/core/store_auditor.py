"""
Shopify Store CRO Auditor — conversion-impact priority.

Static checks: requests + BeautifulSoup (ThreadPoolExecutor)
Playwright only: ATC fold, cart flow, mobile tap target, load time
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

ProgressCallback = Optional[Callable[[str], None]]

SKIP_PATHS = [
    "/cart", "/checkout", "/account", "/search",
    ".pdf", ".zip", "javascript:", "#", "/cdn/", "/s/files/",
    "/customer_authentication", "/services/", "/.well-known/",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

CATEGORY_WEIGHTS = {
    "product_page": 0.30,
    "cart_checkout": 0.25,
    "mobile": 0.20,
    "speed": 0.15,
    "marketing": 0.10,
}

DEDUCTIONS = {"HIGH": 3.0, "MEDIUM": 1.5, "LOW": 0.5}


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _emit(progress: ProgressCallback, message: str) -> None:
    print(message)
    if progress:
        try:
            progress(message)
        except Exception:
            pass


def clean_domain(url: str) -> str:
    parsed = urlparse(url)
    domain = (parsed.netloc or parsed.path).replace("www.", "")
    return re.sub(r"[^\w\-.]", "_", domain)


def make_output_dir(base_url: str) -> str:
    domain = clean_domain(base_url)
    date_str = datetime.now().strftime("%Y-%m-%d")
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    output_dir = os.path.join(project_root, "outputs", f"{domain}_{date_str}")
    os.makedirs(os.path.join(output_dir, "screenshots"), exist_ok=True)
    return output_dir


def safe_filename(url: str) -> str:
    path = urlparse(url).path.strip("/").replace("/", "_") or "homepage"
    return re.sub(r"[^\w\-]", "_", path)[:80]


def is_skip_url(url: str) -> bool:
    return any(p in url for p in SKIP_PATHS)


def _finding(
    check_id: int,
    check_name: str,
    severity: str,
    category: str,
    passed: bool,
    issue: str,
    evidence: str,
    impact: str,
    fix: str,
    urls: list[str] | None = None,
    screenshot: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    if status is None:
        status = "pass" if passed else "fail"
    return {
        "check_id": check_id,
        "check_name": check_name,
        "severity": severity,
        "category": category,
        "passed": passed,
        "status": status,
        "issue": issue,
        "evidence": evidence,
        "impact": impact,
        "fix": fix,
        "urls": urls or [],
        "screenshot": screenshot or "",
        "is_sitewide": len(urls or []) >= 3,
    }


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch_soup(session: requests.Session, url: str, timeout: int = 15) -> BeautifulSoup | None:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None
        return BeautifulSoup(r.text, "lxml")
    except Exception:
        return None


# ─────────────────────────────────────────────
# Crawl (requests + BS4)
# ─────────────────────────────────────────────

def crawl_pages(
    base_url: str,
    max_pages: int = 60,
    progress: ProgressCallback = None,
) -> list[str]:
    session = _session()
    base_domain = urlparse(base_url).netloc
    visited: set[str] = set()
    queue = [base_url.rstrip("/") + "/" if urlparse(base_url).path in ("", "/") else base_url]
    if base_url not in queue:
        queue.insert(0, base_url)
    found: list[str] = []

    while queue and len(found) < max_pages:
        url = queue.pop(0).split("#")[0]
        if url in visited or is_skip_url(url):
            continue
        visited.add(url)
        soup = fetch_soup(session, url)
        if not soup:
            continue
        found.append(url)
        if len(found) % 10 == 0:
            _emit(progress, f"      Crawled {len(found)} pages...")
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"]).split("#")[0]
            parsed = urlparse(href)
            if parsed.netloc != base_domain:
                continue
            if href not in visited and not is_skip_url(href):
                queue.append(href)
    return found


# ─────────────────────────────────────────────
# Static product-page checks (BS4)
# ─────────────────────────────────────────────

def _has_reviews(soup: BeautifulSoup) -> tuple[bool, str]:
    # Class patterns
    for el in soup.find_all(True, class_=True):
        classes = " ".join(el.get("class", [])).lower()
        for kw in (
            "review", "rating", "stars", "judge-me", "jdgm", "stamped",
            "yotpo", "loox", "okendo", "rivyo",
        ):
            if kw in classes:
                return True, f"Element class match: '{kw}' in {classes[:60]}"

    text = soup.get_text(" ", strip=True).lower()
    for phrase in (
        "customer review", "customer reviews", "verified buyer",
        "out of 5", "stars", "reviews",
    ):
        if phrase in text:
            return True, f"Text match: '{phrase}'"

    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        if "aggregaterating" in raw.lower():
            return True, "JSON-LD aggregateRating found"
    return False, "No review/rating signals found"


def _has_trust(soup: BeautifulSoup) -> tuple[bool, str]:
    keywords = (
        "money back", "guarantee", "secure checkout", "free return",
        "easy return", "ssl", "safe checkout", "protected",
    )
    text = soup.get_text(" ", strip=True).lower()
    for kw in keywords:
        if kw in text:
            return True, f"Text match: '{kw}'"

    for img in soup.find_all("img", alt=True):
        alt = (img.get("alt") or "").lower()
        for kw in keywords:
            if kw in alt:
                return True, f"Image alt match: '{kw}'"

    for el in soup.find_all(True, class_=True):
        classes = " ".join(el.get("class", [])).lower()
        for kw in ("trust", "badge", "guarantee", "secure"):
            if kw in classes:
                return True, f"Class match: '{kw}'"
    return False, "No trust badge signals found"


def _has_price(soup: BeautifulSoup) -> tuple[bool, str]:
    currency = re.compile(r"[$£€]\s*\d|^\d+[.,]\d{2}")
    compare_note = ""
    for el in soup.find_all(True, class_=re.compile(r"compare|sale|was-price|price--on-sale", re.I)):
        style = (el.get("style") or "").lower()
        tag = el.name or ""
        if tag == "s" or "line-through" in style or "compare" in " ".join(el.get("class", [])).lower():
            t = el.get_text(" ", strip=True)
            if currency.search(t) or re.search(r"\d", t):
                compare_note = f"; compare-at: '{t[:30]}'"
                break
    for el in soup.find_all(["s", "del"]):
        t = el.get_text(" ", strip=True)
        if currency.search(t):
            compare_note = f"; compare-at: '{t[:30]}'"
            break

    for el in soup.find_all(True, class_=re.compile(r"price", re.I)):
        t = el.get_text(" ", strip=True)
        if currency.search(t) or re.search(r"\d", t):
            return True, f"Price element: '{t[:40]}'{compare_note}"
    body = soup.get_text(" ", strip=True)
    if re.search(r"[$£€]\s*\d", body):
        return True, f"Currency symbol with number found on page{compare_note}"
    return False, "No clear price element with currency found"



def _has_urgency(soup: BeautifulSoup) -> tuple[bool, str]:
    text = soup.get_text(" ", strip=True).lower()
    phrases = (
        "only", "left in stock", "selling fast", "low stock",
        "limited", "sold", "people viewing",
    )
    # "only" alone is too noisy — require context
    for phrase in phrases:
        if phrase == "only":
            if re.search(r"only\s+\d+\s+left|only\s+\d+\s+in\s+stock", text):
                return True, "Text match: low-stock style 'only X left'"
            continue
        if phrase == "sold":
            if re.search(r"\d+\s+sold", text):
                return True, "Text match: 'X sold'"
            continue
        if phrase in text:
            return True, f"Text match: '{phrase}'"
    return False, "No urgency signals found"


def _has_upsell(soup: BeautifulSoup) -> tuple[bool, str]:
    text = soup.get_text(" ", strip=True).lower()
    phrases = (
        "you may also like", "frequently bought together",
        "customers also bought", "recommended for you", "complete the look",
    )
    for phrase in phrases:
        if phrase in text:
            return True, f"Text match: '{phrase}'"
    for el in soup.find_all(True, class_=True):
        classes = " ".join(el.get("class", [])).lower()
        if any(k in classes for k in ("upsell", "cross-sell", "related", "recommended", "complementary")):
            return True, f"Class match related/upsell: {classes[:50]}"
    return False, "No upsell/cross-sell section found"


def _has_shipping(soup: BeautifulSoup) -> tuple[bool, str]:
    text = soup.get_text(" ", strip=True).lower()
    phrases = ("ships in", "delivery", "free shipping", "arrives by", "shipping")
    for phrase in phrases:
        if phrase in text:
            return True, f"Text match: '{phrase}'"
    return False, "No shipping/delivery info found near product content"


def _image_count(soup: BeautifulSoup) -> tuple[int, str]:
    gallery = soup.find(
        True,
        class_=re.compile(r"product__media|product-image|gallery|product__photos|media-gallery", re.I),
    )
    root = gallery or soup
    imgs = []
    for img in root.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src or src.startswith("data:"):
            continue
        low = src.lower()
        if any(x in low for x in ("icon", "logo", "pixel", "1x1", "sprite")):
            continue
        imgs.append(src)
    # unique
    count = len(list(dict.fromkeys(imgs)))
    return count, f"{count} product image(s) detected"


def _has_subscription(soup: BeautifulSoup) -> tuple[bool, str]:
    text = soup.get_text(" ", strip=True).lower()
    html = str(soup).lower()
    for kw in ("seal", "recharge", "bold subscriptions", "appstle", "subscribe & save", "subscribe and save"):
        if kw in text or kw in html:
            return True, f"Subscription signal: '{kw}'"
    for el in soup.find_all(True, class_=True):
        classes = " ".join(el.get("class", [])).lower()
        if "subscription" in classes or "subscribe" in classes:
            return True, f"Class match: {classes[:50]}"
    return False, "No subscription option found"


def analyze_product_html(url: str, html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    reviews_ok, reviews_ev = _has_reviews(soup)
    trust_ok, trust_ev = _has_trust(soup)
    price_ok, price_ev = _has_price(soup)
    urgency_ok, urgency_ev = _has_urgency(soup)
    upsell_ok, upsell_ev = _has_upsell(soup)
    shipping_ok, shipping_ev = _has_shipping(soup)
    img_count, img_ev = _image_count(soup)
    sub_ok, sub_ev = _has_subscription(soup)
    return {
        "url": url,
        "reviews": (reviews_ok, reviews_ev),
        "trust": (trust_ok, trust_ev),
        "price": (price_ok, price_ev),
        "urgency": (urgency_ok, urgency_ev),
        "upsell": (upsell_ok, upsell_ev),
        "shipping": (shipping_ok, shipping_ev),
        "images": (img_count, img_ev),
        "subscription": (sub_ok, sub_ev),
    }


def fetch_and_analyze_product(url: str) -> dict[str, Any] | None:
    session = _session()
    try:
        r = session.get(url, timeout=15, allow_redirects=True)
        if r.status_code >= 400:
            return None
        return analyze_product_html(url, r.text)
    except Exception:
        return None


# ─────────────────────────────────────────────
# Homepage static checks
# ─────────────────────────────────────────────

def analyze_homepage(url: str, soup: BeautifulSoup) -> dict[str, Any]:
    text = soup.get_text(" ", strip=True).lower()
    html = str(soup).lower()

    # Email capture
    email_ok = False
    email_ev = "No email capture found"
    if soup.find("input", attrs={"type": "email"}):
        email_ok, email_ev = True, "Email input found"
    else:
        for kw in ("klaviyo", "mailchimp", "omnisend", "popup", "flyout", "newsletter"):
            if kw in html:
                email_ok, email_ev = True, f"Capture signal: '{kw}'"
                break

    # Trust on homepage
    trust_ok, trust_ev = _has_trust(soup)

    # Hero CTA
    h1 = soup.find("h1")
    has_h1 = bool(h1 and h1.get_text(strip=True))
    cta_keywords = ("shop now", "shop all", "buy now", "get started", "explore", "discover")
    has_cta = any(k in text for k in cta_keywords)
    if not has_cta:
        hero = soup.find(True, class_=re.compile(r"hero|banner|slideshow", re.I))
        if hero and hero.find(["a", "button"]):
            has_cta = True
    hero_ok = has_h1 and has_cta
    hero_ev = f"H1={'yes' if has_h1 else 'no'}, CTA={'yes' if has_cta else 'no'}"

    # Social proof
    social_ok = False
    social_ev = "No social proof found"
    for phrase in ("as seen in", "testimonial", "trusted by", "customers", "press"):
        if phrase in text:
            social_ok, social_ev = True, f"Text match: '{phrase}'"
            break
    if not social_ok:
        for el in soup.find_all(True, class_=True):
            classes = " ".join(el.get("class", [])).lower()
            if any(k in classes for k in ("testimonial", "press", "logo-bar", "social-proof")):
                social_ok, social_ev = True, f"Class match: {classes[:50]}"
                break

    return {
        "email": (email_ok, email_ev),
        "trust": (trust_ok, trust_ev),
        "hero": (hero_ok, hero_ev),
        "social": (social_ok, social_ev),
    }


# ─────────────────────────────────────────────
# Aggregate static findings
# ─────────────────────────────────────────────

def build_static_findings(
    homepage_url: str,
    homepage_data: dict,
    product_results: list[dict],
) -> list[dict]:
    findings: list[dict] = []
    if not product_results:
        product_results = []

    # 2 Reviews
    fail_urls = [p["url"] for p in product_results if not p["reviews"][0]]
    pass_ev = next((p["reviews"][1] for p in product_results if p["reviews"][0]), "")
    if fail_urls and len(fail_urls) == len(product_results):
        findings.append(_finding(
            2, "Reviews Present", "HIGH", "product_page", False,
            "No reviews or star ratings on product pages",
            "Checked all product pages — no review widgets or aggregateRating found",
            "93% of buyers read reviews before purchasing",
            "Install Judge.me / Okendo / Loox and display stars on product pages",
            fail_urls,
        ))
    elif fail_urls:
        findings.append(_finding(
            2, "Reviews Present", "HIGH", "product_page", False,
            f"Reviews missing on {len(fail_urls)} product page(s)",
            f"Missing on: {', '.join(fail_urls[:3])}…",
            "Missing reviews reduce conversion on those products",
            "Ensure reviews app loads on all product templates",
            fail_urls,
        ))
    else:
        findings.append(_finding(
            2, "Reviews Present", "HIGH", "product_page", True,
            "Reviews / ratings present",
            pass_ev or "Review signals found",
            "", "", [p["url"] for p in product_results[:1]],
        ))

    # 3 Trust badges — fail only if missing on ALL product pages AND homepage
    any_prod_trust = any(p["trust"][0] for p in product_results)
    home_trust_ok = homepage_data["trust"][0]
    if any_prod_trust or home_trust_ok:
        ev = homepage_data["trust"][1] if home_trust_ok else next(
            (p["trust"][1] for p in product_results if p["trust"][0]), "Trust found"
        )
        findings.append(_finding(
            3, "Trust Badges", "HIGH", "product_page", True,
            "Trust signals present", ev, "", "",
        ))
    else:
        findings.append(_finding(
            3, "Trust Badges", "HIGH", "product_page", False,
            "No trust badges on product pages or homepage",
            f"Homepage: {homepage_data['trust'][1]}; Products: no trust signals",
            "Customers hesitate without trust indicators",
            "Add trust strip: Secure Checkout, Money-Back Guarantee, Free Returns",
            [p["url"] for p in product_results[:5]] + [homepage_url],
        ))

    # 4 Price visibility
    price_fail = [p["url"] for p in product_results if not p["price"][0]]
    if price_fail and len(price_fail) == len(product_results):
        findings.append(_finding(
            4, "Price Visibility", "HIGH", "product_page", False,
            "No clear price element found on product pages",
            "No currency-marked price element detected",
            "Hidden or unclear pricing kills conversion",
            "Ensure .price element shows currency and amount above the fold",
            price_fail,
        ))
    elif price_fail:
        findings.append(_finding(
            4, "Price Visibility", "HIGH", "product_page", False,
            f"Price unclear on {len(price_fail)} product page(s)",
            f"Affected: {', '.join(price_fail[:3])}",
            "Unclear pricing reduces add-to-cart rate",
            "Fix price rendering on affected templates",
            price_fail,
        ))
    else:
        ev = next((p["price"][1] for p in product_results if p["price"][0]), "Price found")
        findings.append(_finding(
            4, "Price Visibility", "HIGH", "product_page", True,
            "Price clearly visible", ev, "", "",
        ))

    # 8 Email
    email_ok, email_ev = homepage_data["email"]
    findings.append(_finding(
        8, "Email Capture", "MEDIUM", "marketing", email_ok,
        "Email capture present" if email_ok else "No email capture / popup detected",
        email_ev,
        "Without capture you lose most non-buyers forever",
        "Add Klaviyo/Omnisend popup with a first-order incentive",
        [homepage_url],
    ))

    # 9 Urgency
    urgency_pass = [p for p in product_results if p["urgency"][0]]
    if urgency_pass:
        findings.append(_finding(
            9, "Urgency Signals", "MEDIUM", "marketing", True,
            "Urgency signals found",
            urgency_pass[0]["urgency"][1],
            "", "", [urgency_pass[0]["url"]],
        ))
    else:
        findings.append(_finding(
            9, "Urgency Signals", "MEDIUM", "marketing", False,
            "No urgency signals on any product page",
            "No low-stock / limited / selling-fast messaging found",
            "Without urgency, shoppers delay and often don't return",
            "Show low-stock using inventory_quantity or a countdown for promos",
            [p["url"] for p in product_results[:5]],
        ))

    # 10 Upsell
    upsell_pass = [p for p in product_results if p["upsell"][0]]
    if upsell_pass:
        findings.append(_finding(
            10, "Upsell / Cross-sell", "MEDIUM", "marketing", True,
            "Upsell/cross-sell section found",
            upsell_pass[0]["upsell"][1],
            "", "", [upsell_pass[0]["url"]],
        ))
    else:
        findings.append(_finding(
            10, "Upsell / Cross-sell", "MEDIUM", "marketing", False,
            "No upsell or cross-sell section on product pages",
            "No 'You may also like' / FBT sections detected",
            "Missing upsells reduce average order value",
            "Add Related Products or Frequently Bought Together below description",
            [p["url"] for p in product_results[:5]],
        ))

    # 11 Shipping
    ship_pass = [p for p in product_results if p["shipping"][0]]
    if ship_pass:
        findings.append(_finding(
            11, "Shipping Info Visible", "MEDIUM", "marketing", True,
            "Shipping/delivery info found",
            ship_pass[0]["shipping"][1],
            "", "", [ship_pass[0]["url"]],
        ))
    else:
        findings.append(_finding(
            11, "Shipping Info Visible", "MEDIUM", "marketing", False,
            "No shipping info on product pages",
            "No ships-in / free shipping / delivery messaging found",
            "Shipping clarity reduces checkout anxiety",
            "Add delivery estimate or free-shipping threshold near ATC",
            [p["url"] for p in product_results[:5]],
        ))

    # 12 Image count
    img_fail = [p["url"] for p in product_results if p["images"][0] < 2]
    img_warn = [p["url"] for p in product_results if p["images"][0] == 2]
    img_pass = [p for p in product_results if p["images"][0] >= 3]
    if img_fail:
        findings.append(_finding(
            12, "Image Count", "MEDIUM", "product_page", False,
            f"Only 1 image on {len(img_fail)} product page(s)",
            f"Single-image products: {', '.join(img_fail[:3])}",
            "Thin galleries reduce confidence and conversion",
            "Add at least 3 lifestyle/detail images per product",
            img_fail,
            status="fail",
        ))
    elif img_warn and not img_pass:
        findings.append(_finding(
            12, "Image Count", "MEDIUM", "product_page", False,
            "Only 2 images on product pages",
            img_warn[0] if img_warn else "2 images",
            "More images usually lift conversion",
            "Aim for 3+ images per product",
            img_warn,
            status="warning",
        ))
    else:
        findings.append(_finding(
            12, "Image Count", "MEDIUM", "product_page", True,
            "Product galleries have 3+ images",
            img_pass[0]["images"][1] if img_pass else "3+ images",
            "", "",
        ))

    # 13 Subscription LOW
    sub_pass = [p for p in product_results if p["subscription"][0]]
    findings.append(_finding(
        13, "Subscription Option", "LOW", "product_page", bool(sub_pass),
        "Subscription option found" if sub_pass else "No subscription / Subscribe & Save option",
        sub_pass[0]["subscription"][1] if sub_pass else "No Seal/Recharge/Appstle signals",
        "Subscriptions increase LTV",
        "Enable Subscribe & Save with a small discount",
        [p["url"] for p in product_results[:3]],
    ))

    # 14 Hero CTA
    hero_ok, hero_ev = homepage_data["hero"]
    findings.append(_finding(
        14, "Homepage Hero CTA", "LOW", "marketing", hero_ok,
        "Homepage hero has H1 + CTA" if hero_ok else "Homepage missing clear H1 or hero CTA",
        hero_ev,
        "Weak hero CTAs increase bounce",
        "Add a clear H1 and primary Shop Now button in the hero",
        [homepage_url],
    ))

    # 15 Social proof homepage
    social_ok, social_ev = homepage_data["social"]
    findings.append(_finding(
        15, "Social Proof on Homepage", "LOW", "marketing", social_ok,
        "Homepage social proof present" if social_ok else "No social proof on homepage",
        social_ev,
        "New visitors need proof before exploring",
        "Add testimonials, press logos, or review summary on homepage",
        [homepage_url],
    ))

    return findings


# ─────────────────────────────────────────────
# Playwright checks
# ─────────────────────────────────────────────

ATC_SELECTORS = [
    'button[name="add"]',
    'button.product-form__submit',
    '.product-form__submit',
    'button[type="submit"]',
    '[data-testid="AddToCart"]',
    'form[action*="/cart/add"] button',
]

ATC_TEXT_RE = re.compile(r"add to cart|buy now|add to bag|add to basket", re.I)


async def _find_atc(page):
    for sel in ATC_SELECTORS:
        try:
            els = await page.query_selector_all(sel)
            for el in els:
                if await el.is_visible():
                    return el
        except Exception:
            pass
    try:
        for el in await page.query_selector_all("button, a[href], input[type='submit']"):
            if not await el.is_visible():
                continue
            text = (await el.inner_text() or await el.get_attribute("value") or "").strip()
            if ATC_TEXT_RE.search(text):
                return el
    except Exception:
        pass
    return None


async def playwright_checks(
    base_url: str,
    product_urls: list[str],
    screenshot_dir: str,
    progress: ProgressCallback = None,
) -> list[dict]:
    findings: list[dict] = []
    if not product_urls:
        findings.append(_finding(
            1, "ATC Button Above Fold", "HIGH", "product_page", False,
            "No product pages found to test ATC",
            "Crawl returned zero /products/ URLs",
            "Cannot sell without product pages",
            "Ensure products are published and linked from the storefront",
            [base_url],
        ))
        return findings

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=HEADERS["User-Agent"],
        )
        page = await context.new_page()

        # 1 ATC above fold — all products (dedupe)
        _emit(progress, f"[Playwright] ATC above-fold check on {len(product_urls)} products...")
        atc_fail: list[str] = []
        atc_missing: list[str] = []
        atc_shot = ""
        for i, url in enumerate(product_urls):
            if i % 5 == 0:
                _emit(progress, f"      ATC check {i+1}/{len(product_urls)}")
            try:
                await page.set_viewport_size({"width": 1440, "height": 900})
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                await page.evaluate("window.scrollTo(0, 0)")
                await asyncio.sleep(0.5)
                atc = await _find_atc(page)
                if not atc:
                    atc_missing.append(url)
                    continue
                box = await atc.bounding_box()
                if box and box["y"] >= 600:
                    atc_fail.append(url)
                    if not atc_shot:
                        fname = f"cro_atc_below_fold_{safe_filename(url)}.png"
                        await page.screenshot(
                            path=os.path.join(screenshot_dir, fname), full_page=False
                        )
                        atc_shot = fname
                elif box and not atc_shot and i == 0:
                    fname = f"cro_atc_ok_{safe_filename(url)}.png"
                    await page.screenshot(
                        path=os.path.join(screenshot_dir, fname), full_page=False
                    )
                    atc_shot = fname
            except Exception:
                atc_missing.append(url)

        if atc_missing and len(atc_missing) == len(product_urls):
            findings.append(_finding(
                1, "ATC Button Above Fold", "HIGH", "product_page", False,
                "Add to Cart button not detected on product pages",
                "No ATC / Buy Now control found in DOM",
                "Visitors cannot purchase — direct revenue loss",
                "Ensure product form submit button is visible in the theme",
                atc_missing,
                atc_shot,
            ))
        elif atc_fail:
            findings.append(_finding(
                1, "ATC Button Above Fold", "HIGH", "product_page", False,
                "Add to Cart button is below the fold",
                f"ATC y-position ≥ 600px on {len(atc_fail)} product page(s)",
                "Shoppers who don't scroll miss the buy button",
                "Move ATC above the fold or add a sticky ATC bar",
                atc_fail,
                atc_shot,
            ))
        else:
            findings.append(_finding(
                1, "ATC Button Above Fold", "HIGH", "product_page", True,
                "ATC button visible above the fold",
                "ATC visible within first 600px on checked products",
                "", "", product_urls[:1], atc_shot,
            ))

        # 5 Cart checkout
        _emit(progress, "[Playwright] Cart & checkout flow...")
        cart_ok = False
        cart_ev = "Cart flow failed"
        cart_shot = ""
        test_url = product_urls[0]
        try:
            await page.set_viewport_size({"width": 1440, "height": 900})
            await page.goto(test_url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(1.0)
            atc = await _find_atc(page)
            if atc:
                await atc.click(timeout=5000)
                await asyncio.sleep(1.8)
                fname = "cro_cart_after_atc.png"
                await page.screenshot(path=os.path.join(screenshot_dir, fname), full_page=False)
                cart_shot = fname
                # drawer or /cart
                checkout = None
                for sel in (
                    'button[name="checkout"]',
                    'a[href*="/checkout"]',
                    'button:has-text("Checkout")',
                    'a:has-text("Checkout")',
                    '[name="checkout"]',
                ):
                    try:
                        el = await page.query_selector(sel)
                        if el and await el.is_visible():
                            checkout = el
                            break
                    except Exception:
                        pass
                if not checkout and "/cart" not in page.url:
                    try:
                        await page.goto(urljoin(base_url, "/cart"), wait_until="domcontentloaded", timeout=20000)
                        await asyncio.sleep(1)
                        fname = "cro_cart_page.png"
                        await page.screenshot(path=os.path.join(screenshot_dir, fname), full_page=False)
                        cart_shot = fname
                        for sel in (
                            'button[name="checkout"]',
                            'a[href*="/checkout"]',
                            '[name="checkout"]',
                        ):
                            el = await page.query_selector(sel)
                            if el and await el.is_visible():
                                checkout = el
                                break
                    except Exception:
                        pass
                if checkout:
                    cart_ok = True
                    cart_ev = "Checkout button visible after ATC"
                else:
                    cart_ev = "ATC clicked but checkout button not found"
            else:
                cart_ev = "Could not click ATC — button not found"
        except Exception as exc:
            cart_ev = f"Cart flow error: {exc}"

        findings.append(_finding(
            5, "Cart Checkout Working", "HIGH", "cart_checkout", cart_ok,
            "Checkout reachable after ATC" if cart_ok else "Cart/checkout flow broken or incomplete",
            cart_ev,
            "Broken checkout = zero revenue",
            "Fix ATC → cart drawer/page and ensure Checkout button is visible",
            [test_url],
            cart_shot,
        ))

        # 6 Mobile ATC tap target
        _emit(progress, "[Playwright] Mobile ATC tap target...")
        mobile_ok = False
        mobile_ev = "Mobile ATC not measured"
        mobile_shot = ""
        try:
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.goto(test_url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(1.5)
            atc = await _find_atc(page)
            fname = f"cro_mobile_{safe_filename(test_url)}.png"
            await page.screenshot(path=os.path.join(screenshot_dir, fname), full_page=False)
            mobile_shot = fname
            if atc:
                box = await atc.bounding_box()
                if box:
                    w, h = box["width"], box["height"]
                    if w >= 44 and h >= 44:
                        mobile_ok = True
                        mobile_ev = f"ATC tap target {int(w)}×{int(h)}px"
                    else:
                        mobile_ev = f"ATC tap target too small: {int(w)}×{int(h)}px (min 44×44)"
                else:
                    mobile_ev = "ATC found but no bounding box"
            else:
                mobile_ev = "ATC not found on mobile viewport"
        except Exception as exc:
            mobile_ev = f"Mobile check error: {exc}"

        findings.append(_finding(
            6, "Mobile ATC Tap Target", "HIGH", "mobile", mobile_ok,
            "Mobile ATC tap target OK" if mobile_ok else "Mobile ATC tap target too small or missing",
            mobile_ev,
            "Small tap targets cause mis-taps and abandoned carts on mobile",
            "Set min-height/min-width of ATC to at least 48px on mobile",
            [test_url],
            mobile_shot,
        ))

        # 7 Page speed
        _emit(progress, "[Playwright] Measuring page speed...")

        async def measure_load(url: str) -> float | None:
            try:
                t0 = time.time()
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                return round(time.time() - t0, 2)
            except Exception:
                return None

        await page.set_viewport_size({"width": 1440, "height": 900})
        home_t = await measure_load(base_url)
        prod_t = await measure_load(test_url)

        def speed_status(t: float | None) -> tuple[bool, str, str]:
            if t is None:
                return False, "fail", "Could not measure"
            if t < 3:
                return True, "pass", f"{t}s"
            if t <= 5:
                return False, "warning", f"{t}s (3–5s)"
            return False, "fail", f"{t}s (>5s)"

        home_ok, home_st, home_ev = speed_status(home_t)
        prod_ok, prod_st, prod_ev = speed_status(prod_t)
        # Overall pass only if both under 3; fail if any over 5; else warning
        if home_ok and prod_ok:
            speed_passed, speed_status_val = True, "pass"
            speed_issue = "Page speed healthy"
            speed_ev = f"Homepage {home_ev}; Product {prod_ev}"
            sev_note = "HIGH"
        elif (home_t and home_t > 5) or (prod_t and prod_t > 5):
            speed_passed, speed_status_val = False, "fail"
            speed_issue = "Page load time critical (>5s)"
            speed_ev = f"Homepage {home_ev}; Product {prod_ev}"
            sev_note = "HIGH"
        else:
            speed_passed, speed_status_val = False, "warning"
            speed_issue = "Page load time slow (3–5s)"
            speed_ev = f"Homepage {home_ev}; Product {prod_ev}"
            sev_note = "HIGH"

        findings.append(_finding(
            7, "Page Speed", sev_note, "speed", speed_passed,
            speed_issue, speed_ev,
            "53% of mobile users abandon pages taking over 3s",
            "Remove unused apps, compress images, defer non-critical JS",
            [base_url, test_url],
            status=speed_status_val,
        ))

        # Homepage screenshots
        try:
            await page.set_viewport_size({"width": 1440, "height": 900})
            await page.goto(base_url, wait_until="domcontentloaded", timeout=20000)
            await page.screenshot(
                path=os.path.join(screenshot_dir, "homepage_desktop.png"), full_page=False
            )
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.goto(base_url, wait_until="domcontentloaded", timeout=20000)
            await page.screenshot(
                path=os.path.join(screenshot_dir, "homepage_mobile.png"), full_page=False
            )
        except Exception:
            pass

        await browser.close()

    return findings


# ─────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────

def compute_scores(findings: list[dict]) -> dict:
    cats = {k: 10.0 for k in CATEGORY_WEIGHTS}
    for f in findings:
        if f.get("passed") and f.get("status") != "warning":
            continue
        cat = f.get("category")
        if cat not in cats:
            continue
        ded = DEDUCTIONS.get(f.get("severity", "LOW"), 0.5)
        if f.get("status") == "warning":
            ded = min(ded, 1.5)
        cats[cat] = max(0.0, cats[cat] - ded)

    overall = round(sum(cats[c] * CATEGORY_WEIGHTS[c] for c in cats), 1)
    return {"categories": {k: round(v, 1) for k, v in cats.items()}, "overall": overall}


def findings_to_legacy_results(findings: list[dict]) -> list[dict]:
    """Compatibility shape for older UI counters."""
    results = []
    for f in findings:
        if f.get("passed") and f.get("status") == "pass":
            continue
        results.append({
            "url": (f.get("urls") or [""])[0],
            "check": f"cro_{f.get('category', 'other')}",
            "issues": [{
                "issue": f.get("issue", ""),
                "severity": f.get("severity", "MEDIUM"),
                "label": "CONFIRMED",
                "cro_category": f.get("category", ""),
                "impact": f.get("impact", ""),
                "fix": f.get("fix", ""),
                "_screenshot": f.get("screenshot", ""),
            }],
        })
    return results


# ─────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────

async def run_audit(base_url: str, mode: str = "cro", progress: ProgressCallback = None) -> tuple:
    if not base_url.startswith("http"):
        base_url = "https://" + base_url
    base_url = base_url.rstrip("/")

    _emit(progress, f"Starting CRO audit for {base_url}...")
    output_dir = make_output_dir(base_url)
    screenshot_dir = os.path.join(output_dir, "screenshots")

    # 1. Crawl
    _emit(progress, "[1/4] Crawling pages (requests)...")
    all_pages = await asyncio.to_thread(crawl_pages, base_url, 60, progress)
    product_pages = [u for u in all_pages if "/products/" in urlparse(u).path]
    _emit(progress, f"      {len(all_pages)} pages · {len(product_pages)} products")

    # 2. Homepage static
    _emit(progress, "[2/4] Static checks — homepage + all products...")
    session = _session()
    home_soup = await asyncio.to_thread(fetch_soup, session, base_url)
    homepage_data = (
        analyze_homepage(base_url, home_soup)
        if home_soup
        else {
            "email": (False, "Homepage unreachable"),
            "trust": (False, "Homepage unreachable"),
            "hero": (False, "Homepage unreachable"),
            "social": (False, "Homepage unreachable"),
        }
    )

    # Product static concurrent
    product_results: list[dict] = []
    if product_pages:
        def _job(u: str):
            return fetch_and_analyze_product(u)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(_job, u): u for u in product_pages}
            done = 0
            for fut in as_completed(futures):
                done += 1
                if done % 5 == 0 or done == len(product_pages):
                    _emit(progress, f"      Analyzed {done}/{len(product_pages)} products")
                try:
                    res = fut.result()
                    if res:
                        product_results.append(res)
                except Exception:
                    pass

    static_findings = build_static_findings(base_url, homepage_data, product_results)

    # 3. Playwright
    _emit(progress, "[3/4] Playwright — ATC, cart, mobile, speed...")
    pw_findings = await playwright_checks(
        base_url, product_pages, screenshot_dir, progress
    )

    # Merge: playwright findings for checks 1,5,6,7 override if present
    by_id = {f["check_id"]: f for f in static_findings}
    for f in pw_findings:
        by_id[f["check_id"]] = f
    findings = sorted(by_id.values(), key=lambda x: (x["severity"] != "HIGH", x["severity"] != "MEDIUM", x["check_id"]))

    # Full mode: light SEO extras (optional)
    if mode == "full":
        _emit(progress, "      Full mode: meta SEO sample...")
        # Keep CRO-focused; optional quick title check on homepage
        if home_soup:
            title = home_soup.find("title")
            if not title or not title.get_text(strip=True):
                findings.append(_finding(
                    100, "Homepage Title Tag", "MEDIUM", "marketing", False,
                    "Homepage missing <title>",
                    "No title element",
                    "Hurts SEO & CTR",
                    "Add a unique title tag under 60 characters",
                    [base_url],
                ))

    scores = compute_scores(findings)
    _emit(progress, f"[4/4] CRO Score: {scores['overall']}/10")

    audit_data = {
        "store_url": base_url,
        "audit_date": datetime.now().isoformat(),
        "audit_mode": mode,
        "pages_crawled": all_pages,
        "product_pages": product_pages,
        "load_times": {},
        "findings": findings,
        "scores": scores,
        "results": findings_to_legacy_results(findings),
    }

    json_path = os.path.join(output_dir, "audit_data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(audit_data, f, indent=2, ensure_ascii=False)

    fails = sum(1 for x in findings if not x.get("passed"))
    _emit(progress, f"Done — {fails} issues · score {scores['overall']}/10")
    _emit(progress, f"Saved: {output_dir}")
    return audit_data, output_dir
