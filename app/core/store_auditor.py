"""
Store Auditor — Playwright-powered visual CRO audit

Two phases:
1. requests phase — fast, basic checks (meta, speed, text)
2. Playwright phase — visual, JS-rendered, behavioral checks
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ProgressCallback = Optional[Callable[[str], None]]

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

SCORE_LABEL_TO_KEY = {
    "Product Page": "product_page",
    "Cart & Checkout": "cart_checkout",
    "Mobile": "mobile",
    "Speed": "speed",
    "Marketing": "marketing",
}

TRUST_KEYWORDS = (
    "guarantee",
    "secure",
    "trustpilot",
    "money back",
    "free return",
    "ssl",
    "safe checkout",
    "protected",
)

URGENCY_KEYWORDS = (
    "low stock",
    "only left",
    "selling fast",
    "limited",
    "hurry",
    "ends soon",
    "last few",
)


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


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


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


def take_screenshot(page, name: str, screenshot_dir: str) -> str:
    """Save viewport screenshot; return basename for report embedding."""
    os.makedirs(screenshot_dir, exist_ok=True)
    # Stable names for report cover + unique timestamped copies
    stable = {
        "homepage": "homepage_desktop.png",
        "mobile": "homepage_mobile.png",
        "product": "product_desktop.png",
        "cart": "cart_after_atc.png",
        "collections": "collections_all.png",
    }.get(name)
    fname = stable or f"{name}_{int(time.time())}.png"
    path = os.path.join(screenshot_dir, fname)
    page.screenshot(path=path, full_page=False)
    # Also keep a timestamped copy for history
    if stable:
        stamp = os.path.join(screenshot_dir, f"{name}_{int(time.time())}.png")
        try:
            page.screenshot(path=stamp, full_page=False)
        except Exception:
            pass
    return fname


def _normalize_price(text: str) -> str:
    """Extract a comparable price token like 29.99 from noisy label text."""
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    matches = re.findall(r"\d[\d,]*\.?\d*", cleaned.replace(",", ""))
    if not matches:
        return cleaned.lower()[:40]
    # Prefer the last amount (often the sale price after compare-at)
    return matches[-1]


def _safe_goto(page, target: str, timeout: int = 15000) -> bool:
    try:
        page.goto(target, wait_until="domcontentloaded", timeout=timeout)
        return True
    except Exception:
        try:
            page.goto(target, wait_until="load", timeout=timeout)
            return True
        except Exception:
            return False


def check_dead_links(page, base_url: str) -> list[str]:
    """Find internal links that 404."""
    dead_links: list[str] = []
    seen: set[str] = set()
    session = _session()
    links = page.query_selector_all("a[href]")

    for link in links[:20]:
        href = link.get_attribute("href") or ""
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if not (href.startswith("/") or base_url in href):
            continue
        full_url = urljoin(base_url, href).split("#")[0]
        if full_url in seen or full_url.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(full_url)
        try:
            resp = session.head(full_url, timeout=3, allow_redirects=True)
            if resp.status_code == 404:
                dead_links.append(full_url)
            elif resp.status_code >= 400:
                # Some stores block HEAD — retry with GET
                resp = session.get(full_url, timeout=5, allow_redirects=True, stream=True)
                resp.close()
                if resp.status_code == 404:
                    dead_links.append(full_url)
        except Exception:
            pass

    return dead_links


def check_price_consistency(browser, collection_page, base_url: str) -> list[dict]:
    """Compare collection card prices vs PDP prices for a few products."""
    issues: list[dict] = []
    anchors = collection_page.query_selector_all("a[href*='/products/']")
    checked = 0
    seen_urls: set[str] = set()

    for anchor in anchors:
        if checked >= 3:
            break
        try:
            href = anchor.get_attribute("href") or ""
            if "/products/" not in href:
                continue
            product_url = urljoin(base_url, href).split("#")[0]
            if product_url in seen_urls:
                continue
            seen_urls.add(product_url)

            collection_price_raw = ""
            try:
                collection_price_raw = anchor.evaluate(
                    """el => {
                        const n = el.closest(
                          '.card, .product-card, .card-wrapper, li, article, .grid__item'
                        ) || el.parentElement;
                        if (!n) return '';
                        const p = n.querySelector(
                          "[class*='price']:not([class*='compare'])"
                        ) || n.querySelector("[class*='price']");
                        return p ? (p.innerText || '') : '';
                    }"""
                ) or ""
            except Exception:
                collection_price_raw = ""
            collection_price = _normalize_price(collection_price_raw)
            if not collection_price:
                continue

            product_page = browser.new_page(viewport={"width": 1440, "height": 900})
            try:
                if not _safe_goto(product_page, product_url, timeout=15000):
                    continue
                product_page.wait_for_timeout(800)
                pdp_el = product_page.query_selector(
                    "[class*='price']:not([class*='compare']):not([class*='compare-at'])"
                )
                if not pdp_el:
                    pdp_el = product_page.query_selector("[class*='price']")
                pdp_raw = (pdp_el.inner_text().strip() if pdp_el else "")
                pdp_price = _normalize_price(pdp_raw)
                if pdp_price and collection_price and pdp_price != collection_price:
                    issues.append({
                        "product_url": product_url,
                        "collection_price": collection_price_raw[:60] or collection_price,
                        "pdp_price": pdp_raw[:60] or pdp_price,
                    })
                checked += 1
            finally:
                product_page.close()
        except Exception:
            continue

    return issues


def check_policy_pages(browser, base_url: str) -> dict[str, Any]:
    findings: dict[str, Any] = {}
    policy_urls = [
        "/policies/refund-policy",
        "/policies/terms-of-service",
        "/policies/shipping-policy",
        "/policies/privacy-policy",
    ]

    for path in policy_urls:
        page = None
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            target = urljoin(base_url + "/", path.lstrip("/"))
            ok = _safe_goto(page, target, timeout=10000)
            if not ok:
                findings[path] = {"exists": False, "word_count": 0, "thin": True}
                continue
            # Soft 404 / missing policy pages often still return 200 with little content
            title = (page.title() or "").lower()
            body = ""
            try:
                body = page.inner_text("main, .page-content, .shopify-policy__container, body")
            except Exception:
                body = page.inner_text("body")
            word_count = len(body.split())
            missing = (
                word_count < 40
                or "page not found" in title
                or "404" in title
                or "couldn't find" in body.lower()[:400]
            )
            findings[path] = {
                "exists": not missing,
                "word_count": word_count,
                "thin": (not missing) and word_count < 200,
            }
        except Exception:
            findings[path] = {"exists": False, "word_count": 0, "thin": True}
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    return findings


def crawl_extra_pages(browser, base_url: str, progress: ProgressCallback = None) -> dict[str, Any]:
    """Visit common Shopify pages; return reachable URLs + soft-fail info."""
    candidates = [
        base_url,
        urljoin(base_url + "/", "collections/all"),
        urljoin(base_url + "/", "cart"),
        urljoin(base_url + "/", "pages/about"),
        urljoin(base_url + "/", "pages/about-us"),
        urljoin(base_url + "/", "pages/contact"),
        urljoin(base_url + "/", "pages/contact-us"),
        urljoin(base_url + "/", "policies/refund-policy"),
        urljoin(base_url + "/", "policies/terms-of-service"),
        urljoin(base_url + "/", "policies/shipping-policy"),
    ]
    reachable: list[str] = []
    about_found = False
    contact_found = False
    collections_url = ""

    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        for target in candidates:
            try:
                if not _safe_goto(page, target, timeout=12000):
                    continue
                title = (page.title() or "").lower()
                if "404" in title or "not found" in title:
                    continue
                body_snip = ""
                try:
                    body_snip = page.inner_text("body")[:300].lower()
                except Exception:
                    pass
                if "couldn't find" in body_snip or "page not found" in body_snip:
                    continue
                final = page.url.split("#")[0]
                if final not in reachable:
                    reachable.append(final)
                path = urlparse(final).path.lower()
                if "/pages/about" in path:
                    about_found = True
                if "/pages/contact" in path:
                    contact_found = True
                if "/collections/all" in path or path.rstrip("/").endswith("/collections/all"):
                    collections_url = final
            except Exception:
                continue
    finally:
        page.close()

    return {
        "pages_crawled": reachable,
        "about_found": about_found,
        "contact_found": contact_found,
        "collections_url": collections_url,
    }


# ─────────────────────────────────────────────
# Phase 1 — requests (fast basics)
# ─────────────────────────────────────────────

def run_requests_audit(url: str, progress: ProgressCallback = None) -> dict[str, Any]:
    """Fast static checks: speed, meta, email, trust text, canonical, robots."""
    _emit(progress, "[1/2] Requests phase — meta, speed, trust text…")
    findings: dict[str, Any] = {
        "homepage_load_time": 999.0,
        "has_meta_title": False,
        "meta_title": "",
        "has_meta_description": False,
        "meta_description": "",
        "has_email_capture_static": False,
        "has_trust_text": False,
        "trust_text_match": "",
        "has_canonical": False,
        "canonical_href": "",
        "robots_meta": "",
        "has_robots_meta": False,
        "product_urls_hint": [],
        "status_code": 0,
        "requests_error": "",
    }

    session = _session()
    t0 = time.time()
    try:
        resp = session.get(url, timeout=20, allow_redirects=True)
        findings["homepage_load_time"] = round(time.time() - t0, 2)
        findings["status_code"] = resp.status_code
        html = resp.text
    except Exception as exc:
        findings["requests_error"] = str(exc)
        findings["homepage_load_time"] = round(time.time() - t0, 2)
        _emit(progress, f"      Requests error: {exc}")
        return findings

    soup = BeautifulSoup(html, "lxml")

    title = soup.find("title")
    title_text = (title.get_text(strip=True) if title else "") or ""
    findings["has_meta_title"] = bool(title_text)
    findings["meta_title"] = title_text[:120]

    desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if not desc:
        desc = soup.find("meta", attrs={"property": re.compile(r"og:description", re.I)})
    desc_text = (desc.get("content") or "").strip() if desc else ""
    findings["has_meta_description"] = bool(desc_text)
    findings["meta_description"] = desc_text[:160]

    if soup.find("input", attrs={"type": "email"}):
        findings["has_email_capture_static"] = True
    else:
        low = html.lower()
        for kw in ("klaviyo", "mailchimp", "omnisend", "newsletter"):
            if kw in low:
                findings["has_email_capture_static"] = True
                break

    body_text = soup.get_text(" ", strip=True).lower()
    for kw in TRUST_KEYWORDS:
        if kw in body_text:
            findings["has_trust_text"] = True
            findings["trust_text_match"] = kw
            break

    canonical = soup.find("link", rel=lambda v: v and "canonical" in str(v).lower())
    if canonical and canonical.get("href"):
        findings["has_canonical"] = True
        findings["canonical_href"] = canonical["href"]

    robots = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    if robots and robots.get("content"):
        findings["has_robots_meta"] = True
        findings["robots_meta"] = robots["content"]

    # Hint product URLs for Playwright
    product_urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"]).split("#")[0]
        if "/products/" in urlparse(href).path and href not in product_urls:
            product_urls.append(href)
        if len(product_urls) >= 8:
            break
    findings["product_urls_hint"] = product_urls

    _emit(
        progress,
        f"      Load {findings['homepage_load_time']}s · "
        f"title={'yes' if findings['has_meta_title'] else 'no'} · "
        f"desc={'yes' if findings['has_meta_description'] else 'no'} · "
        f"{len(product_urls)} product link(s)",
    )
    return findings


# ─────────────────────────────────────────────
# Phase 2 — Playwright (visual / behavioral)
# ─────────────────────────────────────────────

def run_playwright_audit(
    url: str,
    screenshot_dir: str,
    progress: ProgressCallback = None,
    product_url_hint: str | None = None,
) -> dict[str, Any]:
    findings: dict[str, Any] = {
        "has_h1": False,
        "h1_text": "",
        "h1_has_content": False,
        "hero_cta_count": 0,
        "nav_links_count": 0,
        "has_visible_nav": False,
        "nav_hamburger_only": False,
        "has_announcement_bar": False,
        "trust_badge_count": 0,
        "email_capture_count": 0,
        "has_email_capture": False,
        "has_carousel": False,
        "carousel_slide_count": 0,
        "product_url": None,
        "atc_y_position": 9999,
        "atc_above_fold": False,
        "product_image_count": 0,
        "price_visible": False,
        "price_text": "",
        "product_has_reviews": False,
        "has_urgency": False,
        "has_cross_sell": False,
        "has_volume_pricing": False,
        "has_sticky_atc": False,
        "mobile_has_hamburger": False,
        "mobile_atc_above_fold": False,
        "mobile_atc_tap_target_ok": False,
        "cart_has_checkout_btn": False,
        "cart_has_trust": False,
        "cart_has_upsell": False,
        "cart_flow_error": "",
        "dead_links": [],
        "policy_pages": {},
        "price_mismatches": [],
        "about_found": False,
        "contact_found": False,
        "extra_pages_crawled": [],
        "screenshots": {},
    }

    homepage_screenshot = ""
    product_screenshot = ""
    mobile_screenshot = ""
    cart_screenshot = ""
    product_url: str | None = product_url_hint

    _emit(progress, "Crawling homepage...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ── DESKTOP VIEWPORT ──
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

        homepage_screenshot = take_screenshot(page, "homepage", screenshot_dir)

        h1 = page.query_selector("h1")
        findings["has_h1"] = bool(h1)
        findings["h1_text"] = h1.inner_text().strip() if h1 else ""
        findings["h1_has_content"] = bool(findings["h1_text"])

        hero_btns = page.query_selector_all(
            "a.button, button, .btn, [class*='hero'] a, [class*='banner'] a"
        )
        findings["hero_cta_count"] = len(hero_btns)

        # Navigation depth — prefer visible nav links
        nav_links = page.query_selector_all("nav a, header nav a, header a")
        visible_nav = []
        for link in nav_links:
            try:
                if link.is_visible():
                    visible_nav.append(link)
            except Exception:
                continue
        nav_count = len(visible_nav) if visible_nav else len(nav_links)
        findings["nav_links_count"] = nav_count
        findings["has_visible_nav"] = nav_count > 3
        findings["nav_hamburger_only"] = nav_count < 3

        announcement = page.query_selector(
            ".announcement-bar, [class*='announcement'], [class*='promo-bar']"
        )
        findings["has_announcement_bar"] = bool(announcement)

        trust_elements = []
        for sel in (
            "[class*='trust']",
            "[class*='badge']",
            "[class*='review']",
            "[class*='rating']",
            ".trustpilot",
        ):
            trust_elements.extend(page.query_selector_all(sel))
        findings["trust_badge_count"] = len(trust_elements)

        email_inputs = page.query_selector_all("input[type='email']")
        findings["email_capture_count"] = len(email_inputs)
        findings["has_email_capture"] = len(email_inputs) > 0

        carousel = page.query_selector(
            "[class*='carousel'], [class*='slider'], .slick-slider, .swiper"
        )
        findings["has_carousel"] = bool(carousel)
        if carousel:
            slides = page.query_selector_all(
                "[class*='slide'], .slick-slide, .swiper-slide"
            )
            findings["carousel_slide_count"] = len(slides)

        # Dead links from homepage
        _emit(progress, "Validating internal links...")
        findings["dead_links"] = check_dead_links(page, url)

        # Multi-page crawl (about/contact/collections/cart/policies soft-check)
        crawl_info = crawl_extra_pages(browser, url, progress)
        findings["extra_pages_crawled"] = crawl_info.get("pages_crawled") or []
        findings["about_found"] = bool(crawl_info.get("about_found"))
        findings["contact_found"] = bool(crawl_info.get("contact_found"))

        # Find product link
        _emit(progress, "Checking product pages...")

        if not product_url:
            product_link = page.query_selector("a[href*='/products/']")
            if product_link:
                href = product_link.get_attribute("href") or ""
                product_url = urljoin(url, href) if href else None

        findings["product_url"] = product_url

        # Price consistency via /collections/all when available
        collections_url = crawl_info.get("collections_url") or urljoin(
            url + "/", "collections/all"
        )
        try:
            if _safe_goto(page, collections_url, timeout=15000):
                take_screenshot(page, "collections", screenshot_dir)
                findings["price_mismatches"] = check_price_consistency(
                    browser, page, url
                )
        except Exception:
            findings["price_mismatches"] = []

        # ── PRODUCT PAGE ──
        if product_url:
            _emit(progress, f"      Auditing product: {product_url}")
            try:
                page.goto(product_url, wait_until="networkidle", timeout=30000)
            except Exception:
                page.goto(product_url, wait_until="domcontentloaded", timeout=30000)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(3000)
            try:
                page.wait_for_selector("img[src*='cdn.shopify']", timeout=5000)
            except Exception:
                pass
            product_screenshot = take_screenshot(page, "product", screenshot_dir)

            atc = page.query_selector(
                "button[name='add'], [class*='add-to-cart'], button[class*='atc'], "
                "form[action*='/cart/add'] button, .product-form__submit"
            )
            if not atc:
                for el in page.query_selector_all("button, a, input[type='submit']"):
                    try:
                        if not el.is_visible():
                            continue
                        text = (el.inner_text() or el.get_attribute("value") or "").strip()
                        if re.search(r"add to cart|buy now|add to bag", text, re.I):
                            atc = el
                            break
                    except Exception:
                        continue

            if atc:
                box = atc.bounding_box()
                findings["atc_y_position"] = box["y"] if box else 9999
                findings["atc_above_fold"] = bool(box and box["y"] < 800)
            else:
                findings["atc_above_fold"] = False
                findings["atc_y_position"] = 9999

            all_images = page.query_selector_all(
                "img[src*='cdn.shopify'], img[src*='shopify.com']"
            )
            product_images = [
                img
                for img in all_images
                if img.is_visible()
                and (img.bounding_box() or {}).get("width", 0) > 100
            ]

            seen: set[str] = set()
            unique_images = []
            for img in product_images:
                src = img.get_attribute("src") or ""
                if src and src not in seen:
                    seen.add(src)
                    unique_images.append(img)

            findings["product_image_count"] = len(unique_images)

            price = page.query_selector(
                "[class*='price']:not([class*='compare']):not([class*='compare-at'])"
            )
            findings["price_visible"] = bool(price)
            findings["price_text"] = (price.inner_text().strip() if price else "")[:80]

            review_selectors = [
                ".stamped-badge",
                ".yotpo",
                ".judge-me-widget",
                "[class*='review-count']",
                "[class*='rating-count']",
                "span[class*='reviews']",
                ".spr-badge",
                "[data-rating]",
            ]
            review_found = False
            for sel in review_selectors:
                if page.query_selector(sel):
                    review_found = True
                    break
            review_count_el = page.query_selector(
                "[class*='review-count'], .stamped-badge-caption"
            )
            if review_count_el:
                count_text = review_count_el.inner_text()
                if "0" in count_text and len(count_text) < 5:
                    review_found = False
            findings["product_has_reviews"] = review_found

            page_text = page.inner_text("body").lower()
            findings["has_urgency"] = any(kw in page_text for kw in URGENCY_KEYWORDS)

            related = page.query_selector(
                "[class*='related'], [class*='upsell'], [class*='cross-sell'], "
                "[class*='recommended'], [class*='complementary']"
            )
            findings["has_cross_sell"] = bool(related)

            volume = page.query_selector(
                "[class*='volume'], [class*='tier'], [class*='bulk']"
            )
            findings["has_volume_pricing"] = bool(volume)

            sticky = page.query_selector(
                "[class*='sticky'][class*='cart'], [class*='sticky'][class*='atc'], "
                "[class*='sticky-add']"
            )
            findings["has_sticky_atc"] = bool(sticky)
        else:
            _emit(progress, "      No /products/ link found — skipping product checks")

        # ── MOBILE VIEWPORT ──
        _emit(progress, "Checking mobile experience...")
        mobile_page = browser.new_page(viewport={"width": 390, "height": 844})
        try:
            mobile_page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception:
            mobile_page.goto(url, wait_until="domcontentloaded", timeout=30000)
        mobile_screenshot = take_screenshot(mobile_page, "mobile", screenshot_dir)

        hamburger = mobile_page.query_selector(
            "[class*='hamburger'], [class*='menu-toggle'], [class*='nav-toggle'], "
            "button[aria-label*='menu' i], summary.header__icon--menu"
        )
        findings["mobile_has_hamburger"] = bool(hamburger)

        if product_url:
            try:
                mobile_page.goto(product_url, wait_until="networkidle", timeout=30000)
            except Exception:
                mobile_page.goto(product_url, wait_until="domcontentloaded", timeout=30000)
            mobile_page.evaluate("window.scrollTo(0, 0)")
            mobile_page.wait_for_timeout(500)
            mobile_atc = mobile_page.query_selector(
                "button[name='add'], [class*='add-to-cart'], .product-form__submit"
            )
            if not mobile_atc:
                for el in mobile_page.query_selector_all("button"):
                    try:
                        text = (el.inner_text() or "").strip()
                        if re.search(r"add to cart|buy now", text, re.I):
                            mobile_atc = el
                            break
                    except Exception:
                        continue
            if mobile_atc:
                mobile_box = mobile_atc.bounding_box()
                findings["mobile_atc_above_fold"] = bool(
                    mobile_box and mobile_box["y"] < 844
                )
                if mobile_box:
                    findings["mobile_atc_tap_target_ok"] = (
                        mobile_box["width"] >= 44 and mobile_box["height"] >= 44
                    )

        # ── CART FLOW ──
        _emit(progress, "Testing cart flow...")
        if product_url:
            cart_page = browser.new_page(viewport={"width": 1440, "height": 900})
            try:
                cart_page.goto(product_url, wait_until="networkidle", timeout=30000)
            except Exception:
                cart_page.goto(product_url, wait_until="domcontentloaded", timeout=30000)

            atc_btn = cart_page.query_selector(
                "button[name='add'], [class*='add-to-cart'], .product-form__submit"
            )
            if not atc_btn:
                for el in cart_page.query_selector_all("button"):
                    try:
                        text = (el.inner_text() or "").strip()
                        if re.search(r"add to cart|buy now", text, re.I):
                            atc_btn = el
                            break
                    except Exception:
                        continue

            if atc_btn:
                try:
                    atc_btn.click(timeout=5000)
                    cart_page.wait_for_timeout(2000)

                    cart_drawer = cart_page.query_selector(
                        "[class*='cart-drawer'], [class*='drawer'][class*='cart'], "
                        "#CartDrawer, .cart-notification"
                    )
                    if not cart_drawer:
                        cart_page.goto(
                            urljoin(url, "/cart"),
                            wait_until="networkidle",
                            timeout=30000,
                        )

                    cart_screenshot = take_screenshot(cart_page, "cart", screenshot_dir)

                    checkout_btn = cart_page.query_selector(
                        "[name='checkout'], button[class*='checkout'], "
                        "a[href*='/checkout'], button:has-text('Checkout')"
                    )
                    findings["cart_has_checkout_btn"] = bool(checkout_btn)

                    cart_trust = cart_page.query_selector(
                        "[class*='trust'], [class*='secure'], [class*='guarantee']"
                    )
                    findings["cart_has_trust"] = bool(cart_trust)

                    cart_upsell = cart_page.query_selector(
                        "[class*='upsell'], [class*='recommend'], [class*='cross']"
                    )
                    findings["cart_has_upsell"] = bool(cart_upsell)
                except Exception as e:
                    findings["cart_flow_error"] = str(e)
                    _emit(progress, f"      Cart flow error: {e}")
            else:
                findings["cart_flow_error"] = "ATC button not found for cart flow"
                _emit(progress, "      Cart flow skipped — ATC not found")

        # Policy pages
        _emit(progress, "Checking policy pages...")
        findings["policy_pages"] = check_policy_pages(browser, url)

        browser.close()

    findings["screenshots"] = {
        "homepage": homepage_screenshot or None,
        "product": product_screenshot or None,
        "mobile": mobile_screenshot or None,
        "cart": cart_screenshot or None,
    }
    return findings


# ─────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────

def calculate_scores(findings: dict) -> dict:
    scores_named: dict[str, float] = {}

    # Product Page (30%)
    pp = 10.0
    if not findings.get("atc_above_fold"):
        pp -= 3
    if not findings.get("product_has_reviews"):
        pp -= 2
    if findings.get("product_image_count", 0) < 3:
        pp -= 1.5
    if not findings.get("has_urgency"):
        pp -= 1
    if not findings.get("has_cross_sell"):
        pp -= 1
    if not findings.get("has_sticky_atc"):
        pp -= 0.5
    if not findings.get("price_visible"):
        pp -= 1
    scores_named["Product Page"] = max(0.0, round(pp, 1))

    # Cart (25%)
    cart = 10.0
    if not findings.get("cart_has_checkout_btn"):
        cart -= 4
    if not findings.get("cart_has_trust"):
        cart -= 3
    if not findings.get("cart_has_upsell"):
        cart -= 2
    if findings.get("cart_flow_error"):
        cart -= 1
    scores_named["Cart & Checkout"] = max(0.0, round(cart, 1))

    # Mobile (20%)
    mob = 10.0
    if not findings.get("mobile_atc_above_fold"):
        mob -= 3
    if not findings.get("mobile_atc_tap_target_ok"):
        mob -= 2
    if not findings.get("mobile_has_hamburger"):
        mob -= 1
    scores_named["Mobile"] = max(0.0, round(mob, 1))

    # Speed (15%) — even sub-1s loads get a small penalty above 0.8s
    spd = 10.0
    load = findings.get("homepage_load_time", 999)
    if load > 3:
        spd -= 5
    elif load > 2:
        spd -= 3
    elif load > 1.5:
        spd -= 2
    elif load > 1:
        spd -= 1
    elif load > 0.5:
        spd -= 0.5
    # Carousel penalty
    if findings.get("has_carousel"):
        spd -= 2
    if findings.get("carousel_slide_count", 0) > 2:
        spd -= 1
    scores_named["Speed"] = max(0.0, round(spd, 1))

    # Marketing (10%)
    mkt = 10.0
    if not findings.get("has_email_capture"):
        mkt -= 3
    if findings.get("trust_badge_count", 0) < 2:
        mkt -= 2
    if not findings.get("has_announcement_bar"):
        mkt -= 1
    if not findings.get("has_h1") or not findings.get("h1_has_content"):
        mkt -= 2
    if not findings.get("has_visible_nav") or findings.get("nav_hamburger_only"):
        mkt -= 1  # hamburger only = penalty
    if findings.get("has_carousel"):
        mkt -= 1  # carousel = CRO risk
    if findings.get("dead_links"):
        mkt -= min(2.0, 0.5 * len(findings.get("dead_links") or []))
    policies = findings.get("policy_pages") or {}
    missing_policies = sum(1 for v in policies.values() if not v.get("exists"))
    thin_policies = sum(1 for v in policies.values() if v.get("exists") and v.get("thin"))
    if missing_policies:
        mkt -= min(2.0, 0.5 * missing_policies)
    if thin_policies:
        mkt -= min(1.0, 0.25 * thin_policies)
    if findings.get("price_mismatches"):
        mkt -= min(2.0, len(findings.get("price_mismatches") or []))
    scores_named["Marketing"] = max(0.0, round(mkt, 1))

    overall = (
        scores_named["Product Page"] * 0.30
        + scores_named["Cart & Checkout"] * 0.25
        + scores_named["Mobile"] * 0.20
        + scores_named["Speed"] * 0.15
        + scores_named["Marketing"] * 0.10
    )

    categories = {
        SCORE_LABEL_TO_KEY[k]: v for k, v in scores_named.items() if k in SCORE_LABEL_TO_KEY
    }
    return {
        "categories": categories,
        "overall": round(overall, 1),
        "labels": scores_named,
    }


# ─────────────────────────────────────────────
# Map raw findings → report findings list
# ─────────────────────────────────────────────

def build_report_findings(raw: dict, base_url: str) -> list[dict]:
    findings: list[dict] = []
    product_url = raw.get("product_url") or base_url
    shots = raw.get("screenshots") or {}
    product_shot = shots.get("product") or ""
    cart_shot = shots.get("cart") or ""
    mobile_shot = shots.get("mobile") or ""
    home_shot = shots.get("homepage") or ""

    # 1 ATC above fold
    findings.append(_finding(
        1, "ATC Button Above Fold", "HIGH", "product_page",
        bool(raw.get("atc_above_fold")),
        "ATC button visible above the fold"
        if raw.get("atc_above_fold")
        else "Add to Cart button is below the fold or missing",
        f"ATC y-position: {raw.get('atc_y_position', 'n/a')}px (threshold 800px)",
        "Shoppers who don't scroll miss the buy button",
        "Move ATC above the fold or add a sticky ATC bar",
        [product_url],
        product_shot,
    ))

    # 2 Reviews
    findings.append(_finding(
        2, "Reviews Present", "HIGH", "product_page",
        bool(raw.get("product_has_reviews")),
        "Reviews / ratings present" if raw.get("product_has_reviews")
        else "No reviews or star ratings on product page",
        "Review/rating widget detected" if raw.get("product_has_reviews")
        else "No review widgets (.judge-me, .yotpo, stamped, etc.) found",
        "93% of buyers read reviews before purchasing",
        "Install Judge.me / Okendo / Loox and display stars on product pages",
        [product_url],
        product_shot,
    ))

    # 3 Trust badges
    trust_ok = raw.get("trust_badge_count", 0) >= 1 or raw.get("has_trust_text")
    findings.append(_finding(
        3, "Trust Badges", "HIGH", "product_page",
        bool(trust_ok),
        "Trust signals present" if trust_ok else "No trust badges detected",
        f"Trust elements: {raw.get('trust_badge_count', 0)}; "
        f"text match: {raw.get('trust_text_match') or 'none'}",
        "Customers hesitate without trust indicators",
        "Add trust strip: Secure Checkout, Money-Back Guarantee, Free Returns",
        [base_url, product_url],
        home_shot,
    ))

    # 4 Price
    findings.append(_finding(
        4, "Price Visibility", "HIGH", "product_page",
        bool(raw.get("price_visible")),
        "Price clearly visible" if raw.get("price_visible")
        else "No clear price element found on product page",
        raw.get("price_text") or "No price element detected",
        "Hidden or unclear pricing kills conversion",
        "Ensure .price shows currency and amount above the fold",
        [product_url],
        product_shot,
    ))

    # 5 Cart checkout
    cart_ok = bool(raw.get("cart_has_checkout_btn")) and not raw.get("cart_flow_error")
    findings.append(_finding(
        5, "Cart Checkout Working", "HIGH", "cart_checkout",
        cart_ok,
        "Checkout reachable after ATC" if cart_ok
        else "Cart/checkout flow broken or incomplete",
        raw.get("cart_flow_error")
        or (
            "Checkout button visible after ATC"
            if raw.get("cart_has_checkout_btn")
            else "Checkout button not found after ATC"
        ),
        "Broken checkout = zero revenue",
        "Fix ATC → cart drawer/page and ensure Checkout button is visible",
        [product_url],
        cart_shot,
    ))

    # 5b Cart trust / upsell (MEDIUM)
    findings.append(_finding(
        51, "Cart Trust Signals", "MEDIUM", "cart_checkout",
        bool(raw.get("cart_has_trust")),
        "Trust signals in cart" if raw.get("cart_has_trust")
        else "No trust signals in cart",
        "Secure/guarantee/trust element in cart UI"
        if raw.get("cart_has_trust") else "None found",
        "Cart is high-anxiety — reinforce trust before checkout",
        "Add secure checkout / guarantee copy near Checkout",
        [product_url],
        cart_shot,
    ))
    findings.append(_finding(
        52, "Cart Upsell", "MEDIUM", "cart_checkout",
        bool(raw.get("cart_has_upsell")),
        "Cart upsell present" if raw.get("cart_has_upsell")
        else "No upsell in cart",
        "Upsell/recommend block found" if raw.get("cart_has_upsell") else "None found",
        "Missing cart upsells reduce AOV",
        "Add cart drawer recommendations or FBT",
        [product_url],
        cart_shot,
    ))

    # 6 Mobile ATC tap
    mobile_tap_ok = bool(raw.get("mobile_atc_tap_target_ok"))
    findings.append(_finding(
        6, "Mobile ATC Tap Target", "HIGH", "mobile",
        mobile_tap_ok,
        "Mobile ATC tap target OK" if mobile_tap_ok
        else "Mobile ATC tap target too small or missing",
        f"Above fold: {raw.get('mobile_atc_above_fold')}; "
        f"tap target ≥44px: {raw.get('mobile_atc_tap_target_ok')}",
        "Small tap targets cause mis-taps and abandoned carts on mobile",
        "Set min-height/min-width of ATC to at least 48px on mobile",
        [product_url],
        mobile_shot,
    ))

    # 6b Mobile ATC fold
    findings.append(_finding(
        61, "Mobile ATC Above Fold", "HIGH", "mobile",
        bool(raw.get("mobile_atc_above_fold")),
        "Mobile ATC above fold" if raw.get("mobile_atc_above_fold")
        else "Mobile ATC below fold or missing",
        f"mobile_atc_above_fold={raw.get('mobile_atc_above_fold')}",
        "Mobile shoppers expect buy CTA without scrolling",
        "Keep ATC sticky or above the fold on mobile product pages",
        [product_url],
        mobile_shot,
    ))

    # 7 Speed
    load = raw.get("homepage_load_time", 999)
    if load < 3:
        speed_ok, speed_st = True, "pass"
        speed_issue = "Page speed healthy"
    elif load <= 5:
        speed_ok, speed_st = False, "warning"
        speed_issue = "Page load time slow (3–5s)"
    else:
        speed_ok, speed_st = False, "fail"
        speed_issue = "Page load time critical (>5s)"
    findings.append(_finding(
        7, "Page Speed", "HIGH", "speed",
        speed_ok,
        speed_issue,
        f"Homepage response {load}s"
        + ("; carousel detected" if raw.get("has_carousel") else ""),
        "53% of mobile users abandon pages taking over 3s",
        "Remove unused apps, compress images, defer non-critical JS",
        [base_url],
        home_shot,
        status=speed_st,
    ))

    # 8 Email
    email_ok = bool(raw.get("has_email_capture") or raw.get("has_email_capture_static"))
    findings.append(_finding(
        8, "Email Capture", "MEDIUM", "marketing",
        email_ok,
        "Email capture present" if email_ok else "No email capture detected",
        f"Playwright inputs: {raw.get('email_capture_count', 0)}; "
        f"static: {raw.get('has_email_capture_static')}",
        "Without capture you lose most non-buyers forever",
        "Add Klaviyo/Omnisend popup with a first-order incentive",
        [base_url],
        home_shot,
    ))

    # 9 Urgency
    findings.append(_finding(
        9, "Urgency Signals", "MEDIUM", "marketing",
        bool(raw.get("has_urgency")),
        "Urgency signals found" if raw.get("has_urgency")
        else "No urgency signals on product page",
        "low-stock / limited / selling-fast messaging"
        if raw.get("has_urgency") else "None found",
        "Without urgency, shoppers delay and often don't return",
        "Show low-stock using inventory_quantity or a promo countdown",
        [product_url],
        product_shot,
    ))

    # 10 Cross-sell
    findings.append(_finding(
        10, "Upsell / Cross-sell", "MEDIUM", "marketing",
        bool(raw.get("has_cross_sell")),
        "Upsell/cross-sell section found" if raw.get("has_cross_sell")
        else "No upsell or cross-sell section on product page",
        "Related/upsell block detected" if raw.get("has_cross_sell") else "None found",
        "Missing upsells reduce average order value",
        "Add Related Products or Frequently Bought Together below description",
        [product_url],
        product_shot,
    ))

    # 12 Images
    img_n = raw.get("product_image_count", 0)
    img_ok = img_n >= 3
    findings.append(_finding(
        12, "Image Count", "MEDIUM", "product_page",
        img_ok,
        f"Product gallery has {img_n} image(s)"
        + (" (3+)" if img_ok else " — below recommended 3"),
        f"{img_n} product image(s) detected",
        "Thin galleries reduce confidence and conversion",
        "Add at least 3 lifestyle/detail images per product",
        [product_url],
        product_shot,
        status="pass" if img_ok else ("warning" if img_n == 2 else "fail"),
    ))

    # 14 Hero / H1
    hero_ok = bool(raw.get("h1_has_content")) and raw.get("hero_cta_count", 0) > 0
    findings.append(_finding(
        14, "Homepage Hero CTA", "LOW", "marketing",
        hero_ok,
        "Homepage has H1 + CTA elements" if hero_ok
        else "Homepage missing clear H1 or hero CTA",
        f"H1={'yes' if raw.get('h1_has_content') else 'no'} "
        f"('{raw.get('h1_text', '')[:40]}'); "
        f"CTA-like elements: {raw.get('hero_cta_count', 0)}",
        "Weak hero CTAs increase bounce",
        "Add a clear H1 and primary Shop Now button in the hero",
        [base_url],
        home_shot,
    ))

    # Announcement bar
    findings.append(_finding(
        16, "Announcement Bar", "LOW", "marketing",
        bool(raw.get("has_announcement_bar")),
        "Announcement / promo bar present" if raw.get("has_announcement_bar")
        else "No announcement bar detected",
        "Promo/announcement bar element found"
        if raw.get("has_announcement_bar") else "None found",
        "Announcement bars drive promos and free-shipping thresholds",
        "Add a slim announcement bar with offer or shipping message",
        [base_url],
        home_shot,
    ))

    # Sticky ATC
    findings.append(_finding(
        17, "Sticky ATC", "LOW", "product_page",
        bool(raw.get("has_sticky_atc")),
        "Sticky ATC present" if raw.get("has_sticky_atc")
        else "No sticky ATC bar",
        "Sticky cart/ATC element found" if raw.get("has_sticky_atc") else "None found",
        "Sticky ATC recovers scrollers who miss the primary button",
        "Add a sticky add-to-cart bar on product pages",
        [product_url],
        product_shot,
    ))

    # Meta title / description (requests)
    findings.append(_finding(
        100, "Meta Title", "MEDIUM", "marketing",
        bool(raw.get("has_meta_title")),
        "Meta title present" if raw.get("has_meta_title") else "Homepage missing <title>",
        raw.get("meta_title") or "No title element",
        "Hurts SEO & CTR",
        "Add a unique title tag under 60 characters",
        [base_url],
    ))
    findings.append(_finding(
        101, "Meta Description", "MEDIUM", "marketing",
        bool(raw.get("has_meta_description")),
        "Meta description present" if raw.get("has_meta_description")
        else "Homepage missing meta description",
        raw.get("meta_description") or "No meta description",
        "Weak SERP snippets reduce click-through",
        "Add a compelling meta description under 155 characters",
        [base_url],
    ))
    findings.append(_finding(
        102, "Canonical Tag", "LOW", "marketing",
        bool(raw.get("has_canonical")),
        "Canonical tag present" if raw.get("has_canonical")
        else "No canonical tag on homepage",
        raw.get("canonical_href") or "None found",
        "Canonical tags prevent duplicate-content issues",
        "Add <link rel='canonical'> pointing to the preferred URL",
        [base_url],
    ))

    # Dead links
    dead = raw.get("dead_links") or []
    findings.append(_finding(
        200, "Dead Internal Links", "HIGH", "marketing",
        len(dead) == 0,
        "No dead internal links found" if not dead
        else f"{len(dead)} internal link(s) return 404",
        "; ".join(dead[:5]) if dead else "First 20 homepage links OK",
        "Broken links erode trust and waste crawl budget",
        "Fix or remove 404 links from header, footer, and homepage",
        dead[:5] or [base_url],
        home_shot,
        status="pass" if not dead else "fail",
    ))

    # Navigation depth
    nav_count = raw.get("nav_links_count", 0)
    hamburger_only = bool(raw.get("nav_hamburger_only"))
    findings.append(_finding(
        201, "Navigation Depth", "MEDIUM", "marketing",
        not hamburger_only,
        "Visible top-level navigation present" if not hamburger_only
        else "Navigation appears hamburger-only / very sparse",
        f"Visible nav links: {nav_count}; hamburger_only={hamburger_only}",
        "Sparse nav makes discovery harder and increases bounce",
        "Expose key collections/pages in a desktop header menu",
        [base_url],
        home_shot,
        status="pass" if not hamburger_only else "warning",
    ))

    # Policy pages
    policies = raw.get("policy_pages") or {}
    missing = [p for p, info in policies.items() if not info.get("exists")]
    thin = [
        f"{p} ({info.get('word_count', 0)} words)"
        for p, info in policies.items()
        if info.get("exists") and info.get("thin")
    ]
    policy_ok = not missing and not thin
    policy_evidence_parts = []
    for p, info in policies.items():
        if not info.get("exists"):
            policy_evidence_parts.append(f"{p}: missing")
        elif info.get("thin"):
            policy_evidence_parts.append(f"{p}: thin ({info.get('word_count')} words)")
        else:
            policy_evidence_parts.append(f"{p}: ok ({info.get('word_count')} words)")
    findings.append(_finding(
        202, "Policy Pages", "HIGH" if missing else "MEDIUM", "marketing",
        policy_ok,
        "Policy pages present with adequate content" if policy_ok
        else (
            f"Missing policies: {', '.join(missing)}"
            if missing
            else f"Thin policy content: {', '.join(thin)}"
        ),
        "; ".join(policy_evidence_parts) if policy_evidence_parts else "No policy scan data",
        "Missing/thin policies reduce trust and can cause checkout drop-off",
        "Publish refund, shipping, terms, and privacy policies with clear copy (200+ words)",
        [urljoin(base_url + "/", p.lstrip("/")) for p in (missing or list(policies.keys())[:4])],
        home_shot,
        status="pass" if policy_ok else ("fail" if missing else "warning"),
    ))

    # Price consistency
    mismatches = raw.get("price_mismatches") or []
    findings.append(_finding(
        203, "Price Consistency", "HIGH", "product_page",
        len(mismatches) == 0,
        "Collection and PDP prices match" if not mismatches
        else f"{len(mismatches)} collection vs PDP price mismatch(es)",
        "; ".join(
            f"{m.get('product_url', '')}: collection={m.get('collection_price')} "
            f"vs pdp={m.get('pdp_price')}"
            for m in mismatches[:3]
        )
        if mismatches
        else "Sampled collection cards match PDP prices",
        "Price mismatches create distrust and support tickets",
        "Ensure collection cards use the same price source as the product template",
        [m.get("product_url", base_url) for m in mismatches[:4]] or [base_url],
        product_shot,
        status="pass" if not mismatches else "fail",
    ))

    return findings


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
# Main entry (async — UI calls asyncio.run)
# ─────────────────────────────────────────────

async def run_audit(
    base_url: str,
    mode: str = "cro",
    progress: ProgressCallback = None,
) -> tuple:
    if not base_url.startswith("http"):
        base_url = "https://" + base_url
    base_url = base_url.rstrip("/")

    _emit(progress, f"Starting CRO audit for {base_url}...")
    output_dir = make_output_dir(base_url)
    screenshot_dir = os.path.join(output_dir, "screenshots")

    # Phase 1
    req = await asyncio.to_thread(run_requests_audit, base_url, progress)

    # Phase 2
    hint = (req.get("product_urls_hint") or [None])[0]
    pw = await asyncio.to_thread(
        run_playwright_audit, base_url, screenshot_dir, progress, hint
    )

    # Merge raw findings
    raw = {**req, **pw}
    # Prefer Playwright email if measured
    if pw.get("has_email_capture"):
        raw["has_email_capture"] = True
    elif req.get("has_email_capture_static"):
        raw["has_email_capture"] = True

    _emit(progress, "Building scores...")
    scores = calculate_scores(raw)
    findings = build_report_findings(raw, base_url)

    if mode == "full":
        # Extra full-mode note already covered by meta checks
        pass

    product_pages = []
    if raw.get("product_url"):
        product_pages = [raw["product_url"]]
    pages_crawled = []
    for u in [base_url] + list(raw.get("extra_pages_crawled") or []) + product_pages:
        if u and u not in pages_crawled:
            pages_crawled.append(u)

    _emit(progress, f"CRO Score: {scores['overall']}/10")

    audit_data = {
        "store_url": base_url,
        "audit_date": datetime.now().isoformat(),
        "audit_mode": mode,
        "pages_crawled": pages_crawled,
        "product_pages": product_pages,
        "load_times": {"homepage": raw.get("homepage_load_time")},
        "raw_findings": raw,
        "findings": findings,
        "scores": scores,
        "results": findings_to_legacy_results(findings),
        "dead_links": raw.get("dead_links") or [],
        "policy_pages": raw.get("policy_pages") or {},
        "price_mismatches": raw.get("price_mismatches") or [],
        "nav_summary": {
            "nav_link_count": raw.get("nav_links_count", 0),
            "nav_hamburger_only": bool(raw.get("nav_hamburger_only")),
        },
    }

    json_path = os.path.join(output_dir, "audit_data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(audit_data, f, indent=2, ensure_ascii=False)

    fails = sum(1 for x in findings if not x.get("passed"))
    _emit(progress, f"Done — {fails} issues · score {scores['overall']}/10")
    _emit(progress, f"Saved: {output_dir}")
    return audit_data, output_dir
