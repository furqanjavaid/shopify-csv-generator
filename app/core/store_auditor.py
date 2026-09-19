import asyncio
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import requests


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

# Pages to skip — Shopify internal, not client-facing
SKIP_PATHS = [
    "/cart", "/checkout", "/account", "/search",
    ".pdf", ".zip", "javascript:", "#", "/cdn/", "/s/files/",
    "/customer_authentication", "/services/", "/.well-known/"
]

def clean_domain(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path
    domain = domain.replace("www.", "")
    return re.sub(r"[^\w\-.]", "_", domain)


def make_output_dir(base_url: str) -> str:
    """Create audits/{domain}_{date}/ under the project root."""
    domain = clean_domain(base_url)
    date_str = datetime.now().strftime("%Y-%m-%d")
    folder_name = f"{domain}_{date_str}"
    # app/core/store_auditor.py → project root is two levels up
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    output_dir = os.path.join(project_root, "audits", folder_name)
    os.makedirs(os.path.join(output_dir, "screenshots"), exist_ok=True)
    return output_dir


def safe_filename(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/").replace("/", "_") or "homepage"
    return re.sub(r"[^\w\-]", "_", path)[:80]


def is_skip_url(url: str) -> bool:
    return any(p in url for p in SKIP_PATHS)


# ─────────────────────────────────────────────
#  CRAWLER
# ─────────────────────────────────────────────

async def crawl_all_pages(base_url: str, page, max_pages: int = 60):
    visited = set()
    queue   = [base_url]
    found   = []
    base_domain = urlparse(base_url).netloc

    while queue and len(found) < max_pages:
        url = queue.pop(0)
        if url in visited or is_skip_url(url):
            continue
        visited.add(url)

        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            if resp and resp.status < 400:
                found.append(url)
                html = await page.content()
                soup = BeautifulSoup(html, "lxml")
                for a in soup.find_all("a", href=True):
                    href = urljoin(url, a["href"])
                    parsed = urlparse(href)
                    if parsed.netloc == base_domain and href not in visited and not is_skip_url(href):
                        queue.append(href)
        except Exception:
            pass

    return found


# ─────────────────────────────────────────────
#  WAIT FOR FULL RENDER HELPER
# ─────────────────────────────────────────────

async def goto_and_wait(page, url: str, wait_ms: int = 2000):
    """Go to URL, wait for network idle, then extra ms for JS to render."""
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            return False
    await asyncio.sleep(wait_ms / 1000)
    return True


# ─────────────────────────────────────────────
#  SEO CHECKS
# ─────────────────────────────────────────────

async def check_page_meta(url: str, soup: BeautifulSoup) -> dict:
    issues = []

    title = soup.find("title")
    if not title or not title.get_text(strip=True):
        issues.append({"issue": "Missing page title", "severity": "HIGH", "label": "CONFIRMED"})
    elif len(title.get_text(strip=True)) < 10:
        issues.append({"issue": f"Page title too short: '{title.get_text(strip=True)}'", "severity": "MEDIUM", "label": "CONFIRMED"})
    elif len(title.get_text(strip=True)) > 70:
        issues.append({"issue": f"Page title too long ({len(title.get_text(strip=True))} chars)", "severity": "LOW", "label": "CONFIRMED"})

    desc = soup.find("meta", attrs={"name": "description"})
    if not desc or not desc.get("content", "").strip():
        issues.append({"issue": "Missing meta description", "severity": "HIGH", "label": "CONFIRMED"})
    elif len(desc.get("content", "")) > 160:
        issues.append({"issue": "Meta description too long (160+ chars)", "severity": "LOW", "label": "CONFIRMED"})

    h1s = soup.find_all("h1")
    if not h1s:
        issues.append({"issue": "No H1 tag found", "severity": "HIGH", "label": "CONFIRMED"})
    elif len(h1s) > 1:
        issues.append({"issue": f"Multiple H1 tags found ({len(h1s)})", "severity": "MEDIUM", "label": "CONFIRMED"})

    return {"url": url, "check": "meta_seo", "issues": issues}


async def check_images(url: str, soup: BeautifulSoup) -> dict:
    issues = []
    images = soup.find_all("img")

    missing_alt = [img.get("src", "") for img in images if not img.get("alt", "").strip()]
    if missing_alt:
        issues.append({"issue": f"{len(missing_alt)} image(s) missing alt text", "severity": "MEDIUM", "label": "CONFIRMED", "detail": list(dict.fromkeys(missing_alt))[:3]})

    no_dims = [img.get("src", "") for img in images if img.get("src") and not img.get("src", "").startswith("data:") and (not img.get("width") or not img.get("height"))]
    if no_dims:
        issues.append({"issue": f"{len(no_dims)} image(s) missing width/height (causes layout shift)", "severity": "MEDIUM", "label": "CONFIRMED", "detail": list(dict.fromkeys(no_dims))[:3]})

    return {"url": url, "check": "images", "issues": issues}


async def check_speed_signals(url: str, soup: BeautifulSoup) -> dict:
    issues = []
    head = soup.find("head")
    if head:
        blocking = [s for s in head.find_all("script", src=True) if not s.get("async") and not s.get("defer")]
        if len(blocking) > 3:
            issues.append({"issue": f"{len(blocking)} render-blocking scripts in <head>", "severity": "MEDIUM", "label": "CONFIRMED"})

    images = soup.find_all("img")
    no_lazy = [img.get("src", "") for img in images if not img.get("loading") and img.get("src", "")]
    if len(no_lazy) > 3:
        issues.append({"issue": f"{len(no_lazy)} images without lazy loading", "severity": "MEDIUM", "label": "CONFIRMED"})

    return {"url": url, "check": "speed", "issues": issues}


async def check_structured_data(url: str, soup: BeautifulSoup) -> dict:
    issues = []
    scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    if not scripts:
        issues.append({"issue": "No structured data (JSON-LD) found", "severity": "MEDIUM", "label": "CONFIRMED"})
    elif "/products/" in url:
        has_product = False
        for s in scripts:
            try:
                data = json.loads(s.string or "")
                if isinstance(data, dict) and data.get("@type") == "Product":
                    has_product = True
            except Exception:
                pass
        if not has_product:
            issues.append({"issue": "Product page missing Product schema markup", "severity": "MEDIUM", "label": "CONFIRMED"})
    return {"url": url, "check": "schema", "issues": issues}


async def check_broken_links(base_url: str, soup: BeautifulSoup) -> dict:
    issues  = []
    broken  = []
    checked = set()
    base_domain = urlparse(base_url).netloc

    for a in soup.find_all("a", href=True)[:40]:
        href = urljoin(base_url, a["href"])
        parsed = urlparse(href)
        if parsed.netloc != base_domain or href in checked or is_skip_url(href):
            continue
        checked.add(href)
        try:
            r = requests.head(href, timeout=6, allow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 404:
                broken.append(href)
        except Exception:
            pass

    if broken:
        issues.append({"issue": f"{len(broken)} broken link(s) (404)", "severity": "HIGH", "label": "CONFIRMED", "detail": broken[:5]})

    return {"url": base_url, "check": "broken_links", "issues": issues}


async def check_navigation(base_url: str, soup: BeautifulSoup) -> dict:
    issues = []
    nav = (soup.find("nav") or
           soup.find(attrs={"role": "navigation"}) or
           soup.find(id=re.compile(r"nav|menu|header", re.I)))

    if not nav:
        issues.append({"issue": "No navigation menu detected", "severity": "HIGH", "label": "NEEDS VERIFICATION"})
        return {"url": base_url, "check": "navigation", "issues": issues}

    footer = soup.find("footer")
    if footer:
        footer_text = footer.get_text().lower()
        missing = [p for p in ["privacy", "terms", "refund", "shipping"] if p not in footer_text]
        if missing:
            issues.append({"issue": f"Footer missing policy links: {missing}", "severity": "MEDIUM", "label": "CONFIRMED"})

    return {"url": base_url, "check": "navigation", "issues": issues}


# ─────────────────────────────────────────────
#  CRO — PRODUCT PAGE (FULLY RENDERED)
# ─────────────────────────────────────────────

async def check_cro_product_page(url: str, page, screenshot_dir: str) -> dict:
    issues = []

    ok = await goto_and_wait(page, url, wait_ms=2500)
    if not ok:
        return {"url": url, "check": "cro_product", "issues": []}

    await page.set_viewport_size({"width": 1440, "height": 900})
    soup = BeautifulSoup(await page.content(), "lxml")
    page_text = soup.get_text(" ", strip=True).lower()
    viewport_h = 900

    # ── ATC above/below fold ──
    atc_selectors = [
        'button[name="add"]',
        'button.product-form__submit',
        '.product-form__submit',
        'button.add-to-cart',
        '[data-testid="add-to-cart"]',
        'input[type="submit"][value*="Add"]',
        'button[id*="add-to-cart"]',
        'button[class*="add-to-cart"]',
        'button[class*="addtocart"]',
    ]
    atc_el = None
    for sel in atc_selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                atc_el = el
                break
        except Exception:
            pass

    if not atc_el:
        issues.append({
            "issue": "Add to Cart button not detected on product page",
            "severity": "HIGH",
            "label": "NEEDS VERIFICATION",
            "cro_category": "cart_flow",
            "impact": "Visitors cannot add products — direct revenue loss",
            "fix": "Check product template — ensure ATC button is present and not hidden by CSS"
        })
    else:
        try:
            box = await atc_el.bounding_box()
            if box and box["y"] > viewport_h:
                fname = f"cro_atc_below_fold_{safe_filename(url)}.png"
                await page.screenshot(path=os.path.join(screenshot_dir, fname), full_page=False)
                issues.append({
                    "issue": "Add to Cart button is below the fold on product pages",
                    "severity": "HIGH",
                    "label": "CONFIRMED",
                    "cro_category": "cart_flow",
                    "impact": "Visitors who don't scroll miss the buy button — conversion drops significantly",
                    "fix": "Move product images and ATC button higher, or add a sticky ATC bar on scroll",
                    "_screenshot": fname
                })
        except Exception:
            pass

    # ── Trust badges — rendered DOM ──
    trust_selectors = [
        '[class*="trust"]', '[class*="badge"]', '[class*="guarantee"]',
        '[class*="secure"]', '[id*="trust"]', '[id*="badge"]',
    ]
    trust_found = False
    for sel in trust_selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                trust_found = True
                break
        except Exception:
            pass

    # Also check text
    trust_keywords = ["secure checkout", "money back", "free return", "guarantee", "ssl secured", "safe checkout"]
    if not trust_found:
        trust_found = any(kw in page_text for kw in trust_keywords)

    if not trust_found:
        issues.append({
            "issue": "No trust badges or trust signals found on product page",
            "severity": "MEDIUM",
            "label": "CONFIRMED",
            "cro_category": "trust",
            "impact": "Customers hesitate to buy without trust indicators — increases cart abandonment",
            "fix": "Add trust badge strip below ATC: Secure Checkout, Money-Back Guarantee, Free Returns"
        })

    # ── Reviews — rendered DOM ──
    review_selectors = [
        '[class*="review"]', '[class*="rating"]', '[class*="star"]',
        '[id*="review"]', '[id*="rating"]',
        '.judge-me', '.yotpo', '.okendo', '.stamped',
        '[data-judge-me]', '[data-yotpo]',
        'span[class*="stars"]', 'div[class*="reviews"]',
    ]
    reviews_found = False
    for sel in review_selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                reviews_found = True
                break
        except Exception:
            pass

    if not reviews_found:
        review_keywords = ["reviews", "rated", "stars", "verified buyer", "customer review", "write a review"]
        reviews_found = any(kw in page_text for kw in review_keywords)

    if not reviews_found:
        issues.append({
            "issue": "No reviews or star ratings visible on product page",
            "severity": "HIGH",
            "label": "CONFIRMED",
            "cro_category": "social_proof",
            "impact": "93% of buyers read reviews before purchasing — missing reviews directly reduce conversion",
            "fix": "Install or activate a reviews app (Judge.me, Okendo) and ensure it loads on product pages"
        })

    # ── Urgency signals ──
    urgency_keywords = ["only", "left in stock", "limited", "hurry", "selling fast", "low stock", "countdown", "sale ends", "offer ends", "today only"]
    urgency_found = any(kw in page_text for kw in urgency_keywords)
    try:
        urgency_el = await page.query_selector('[class*="countdown"], [class*="urgency"], [class*="low-stock"], [id*="countdown"]')
        if urgency_el:
            urgency_found = True
    except Exception:
        pass

    if not urgency_found:
        issues.append({
            "issue": "No urgency signals on product page (low stock, countdown, limited offer)",
            "severity": "MEDIUM",
            "label": "CONFIRMED",
            "cro_category": "urgency",
            "impact": "Without urgency, customers delay decisions and often don't return",
            "fix": "Add low stock indicator using Liquid inventory_quantity, or add a countdown timer for promotions"
        })

    # ── Upsell / Cross-sell ──
    upsell_selectors = [
        '[class*="upsell"]', '[class*="cross-sell"]', '[class*="related"]',
        '[class*="recommended"]', '[class*="frequently-bought"]',
        '[id*="upsell"]', '[id*="related"]',
    ]
    upsell_found = False
    for sel in upsell_selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                upsell_found = True
                break
        except Exception:
            pass

    upsell_keywords = ["you may also like", "frequently bought", "customers also", "related products", "complete the look", "recommended"]
    if not upsell_found:
        upsell_found = any(kw in page_text for kw in upsell_keywords)

    if not upsell_found:
        issues.append({
            "issue": "No upsell or cross-sell section on product page",
            "severity": "MEDIUM",
            "label": "CONFIRMED",
            "cro_category": "aov",
            "impact": "Missing upsells reduce average order value — customers leave without seeing other products",
            "fix": "Add 'Frequently Bought Together' or 'You May Also Like' section below product description"
        })

    # ── Subscription option ──
    sub_selectors = ['[class*="subscription"]', '[class*="subscribe"]', '[id*="subscription"]', '.seal-subscription']
    sub_found = False
    for sel in sub_selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                sub_found = True
                break
        except Exception:
            pass

    if not sub_found:
        sub_keywords = ["subscribe", "subscribe & save", "auto-ship", "recurring", "subscription"]
        sub_found = any(kw in page_text for kw in sub_keywords)

    if not sub_found:
        issues.append({
            "issue": "No subscription / Subscribe & Save option on product page",
            "severity": "LOW",
            "label": "CONFIRMED",
            "cro_category": "retention",
            "impact": "One-time buyers have lower LTV — subscription options increase recurring revenue",
            "fix": "Enable Seal Subscriptions on product pages with a discount incentive (e.g. 10% off)"
        })

    return {"url": url, "check": "cro_product", "issues": issues}


# ─────────────────────────────────────────────
#  CRO — HOMEPAGE (FULLY RENDERED)
# ─────────────────────────────────────────────

async def check_cro_homepage(url: str, page, screenshot_dir: str) -> dict:
    issues = []

    if urlparse(url).path not in ["/", ""]:
        return {"url": url, "check": "cro_homepage", "issues": []}

    await goto_and_wait(page, url, wait_ms=3000)
    soup = BeautifulSoup(await page.content(), "lxml")
    page_text = soup.get_text(" ", strip=True).lower()

    # ── Hero CTA ──
    hero_cta_keywords = ["shop now", "shop all", "get started", "buy now", "explore", "discover", "order now", "shop the collection"]
    hero_found = any(kw in page_text for kw in hero_cta_keywords)
    try:
        hero_btn = await page.query_selector('.hero a, .hero button, [class*="hero"] a, [class*="banner"] a, [class*="slideshow"] a')
        if hero_btn and await hero_btn.is_visible():
            hero_found = True
    except Exception:
        pass

    if not hero_found:
        issues.append({
            "issue": "No clear hero CTA button detected on homepage",
            "severity": "HIGH",
            "label": "NEEDS VERIFICATION",
            "cro_category": "homepage",
            "impact": "Homepage visitors without a clear next step bounce immediately",
            "fix": "Add a prominent CTA button in hero section linking to best-selling collection"
        })

    # ── Email popup — wait and check ──
    await asyncio.sleep(3)  # wait for popup to trigger
    popup_selectors = [
        '[class*="popup"]', '[class*="modal"]', '[class*="newsletter"]',
        '[id*="popup"]', '[id*="modal"]', '[class*="klaviyo"]',
        'input[type="email"]', '[class*="email-capture"]',
    ]
    popup_found = False
    for sel in popup_selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                popup_found = True
                break
        except Exception:
            pass

    if not popup_found:
        issues.append({
            "issue": "No email capture popup or newsletter form detected",
            "severity": "MEDIUM",
            "label": "NEEDS VERIFICATION",
            "cro_category": "email_capture",
            "impact": "Without email capture, you lose 95%+ of visitors who don't buy on first visit",
            "fix": "Add exit-intent popup or homepage newsletter with discount incentive (e.g. 10% off first order)"
        })

    # ── Social proof on homepage ──
    review_selectors = ['[class*="review"]', '[class*="testimonial"]', '[class*="rating"]', '[class*="star"]']
    social_found = False
    for sel in review_selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                social_found = True
                break
        except Exception:
            pass

    if not social_found:
        social_keywords = ["reviews", "customers love", "trusted by", "5 star", "rated", "testimonial"]
        social_found = any(kw in page_text for kw in social_keywords)

    if not social_found:
        issues.append({
            "issue": "No social proof or testimonials section on homepage",
            "severity": "MEDIUM",
            "label": "CONFIRMED",
            "cro_category": "social_proof",
            "impact": "Homepage without social proof fails to build trust for new visitors",
            "fix": "Add testimonials section or star rating summary on homepage"
        })

    # ── Bestsellers / Featured products ──
    bestseller_selectors = [
        '[class*="featured"]', '[class*="bestseller"]', '[class*="best-seller"]',
        '[class*="popular"]', '[class*="product-grid"]', '[class*="collection"]',
    ]
    bs_found = False
    for sel in bestseller_selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                bs_found = True
                break
        except Exception:
            pass

    if not bs_found:
        bs_keywords = ["best seller", "bestseller", "popular", "top rated", "most loved", "featured", "shop our"]
        bs_found = any(kw in page_text for kw in bs_keywords)

    if not bs_found:
        issues.append({
            "issue": "No bestsellers or featured products section on homepage",
            "severity": "MEDIUM",
            "label": "NEEDS VERIFICATION",
            "cro_category": "homepage",
            "impact": "New visitors don't know where to start — featuring top sellers guides them to purchase",
            "fix": "Add a 'Best Sellers' product grid section on homepage"
        })

    return {"url": url, "check": "cro_homepage", "issues": issues}


# ─────────────────────────────────────────────
#  CRO — CART FLOW
# ─────────────────────────────────────────────

async def check_cart_flow(base_url: str, page, screenshot_dir: str) -> dict:
    issues = []

    try:
        # Find first product
        await goto_and_wait(page, base_url, wait_ms=1500)
        soup = BeautifulSoup(await page.content(), "lxml")
        product_link = None
        for a in soup.find_all("a", href=True):
            if "/products/" in a["href"] and not is_skip_url(a["href"]):
                product_link = urljoin(base_url, a["href"])
                break

        if not product_link:
            issues.append({
                "issue": "No product links found on homepage",
                "severity": "HIGH", "label": "CONFIRMED",
                "cro_category": "cart_flow",
                "impact": "Homepage doesn't link to any products",
                "fix": "Add product or collection links to homepage"
            })
            return {"url": base_url, "check": "cro_cart", "issues": issues}

        # Go to product page
        await goto_and_wait(page, product_link, wait_ms=2000)
        await page.screenshot(path=os.path.join(screenshot_dir, "cro_cart_product_page.png"), full_page=False)

        # Try ATC
        atc_selectors = [
            'button[name="add"]', 'button.product-form__submit',
            '.product-form__submit', 'button.add-to-cart',
            'input[type="submit"][value*="Add"]',
        ]
        clicked = False
        for sel in atc_selectors:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(2)
                    clicked = True
                    break
            except Exception:
                pass

        await page.screenshot(path=os.path.join(screenshot_dir, "cro_cart_after_atc.png"), full_page=False)

        if not clicked:
            issues.append({
                "issue": "Could not click Add to Cart button in automated test",
                "severity": "HIGH", "label": "NEEDS VERIFICATION",
                "cro_category": "cart_flow",
                "impact": "If ATC is non-functional, zero orders can complete",
                "fix": "Test ATC button manually on all product pages and check browser console for JS errors"
            })
            return {"url": base_url, "check": "cro_cart", "issues": issues}

        # Check if drawer opened
        drawer_selectors = [
            '[class*="drawer"]', '[class*="cart-drawer"]', '[class*="side-cart"]',
            '[id*="drawer"]', '[id*="cart-drawer"]', '[class*="mini-cart"]',
        ]
        drawer_opened = False
        for sel in drawer_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    is_vis = await el.is_visible()
                    if is_vis:
                        drawer_opened = True
                        break
            except Exception:
                pass

        if drawer_opened:
            # Drawer cart — check checkout button inside drawer
            await page.screenshot(path=os.path.join(screenshot_dir, "cro_cart_drawer.png"), full_page=False)
            checkout_in_drawer = False
            drawer_checkout_sels = [
                '[class*="drawer"] a[href*="checkout"]',
                '[class*="drawer"] button[name="checkout"]',
                '[class*="cart-drawer"] a[href*="checkout"]',
            ]
            for sel in drawer_checkout_sels:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        checkout_in_drawer = True
                        break
                except Exception:
                    pass

            if not checkout_in_drawer:
                issues.append({
                    "issue": "Drawer cart opens but checkout button not found inside drawer",
                    "severity": "HIGH", "label": "NEEDS VERIFICATION",
                    "cro_category": "cart_flow",
                    "impact": "If checkout is not reachable from drawer, customers cannot complete purchase",
                    "fix": "Check cart drawer template — ensure checkout button is visible and functional"
                })
        else:
            # Check /cart page
            cart_url = urljoin(base_url, "/cart")
            await goto_and_wait(page, cart_url, wait_ms=1500)
            await page.screenshot(path=os.path.join(screenshot_dir, "cro_cart_page.png"), full_page=False)
            cart_text = (await page.content()).lower()

            if "your cart is empty" in cart_text or "cart is empty" in cart_text:
                issues.append({
                    "issue": "Drawer cart detected — verify cart flow manually",
                    "severity": "LOW", "label": "RECOMMENDATION",
                    "cro_category": "cart_flow",
                    "impact": "Automated test cannot verify drawer cart — manual check required",
                    "fix": "Open a product page, click Add to Cart, confirm drawer opens and checkout button is visible"
                })
            else:
                # Checkout button on cart page
                soup_cart = BeautifulSoup(await page.content(), "lxml")
                checkout_btns = (
                    soup_cart.find_all(["button", "a"], string=re.compile(r"checkout", re.I)) +
                    soup_cart.find_all(attrs={"name": "checkout"}) +
                    soup_cart.find_all("a", href=re.compile(r"checkout", re.I))
                )
                if not checkout_btns:
                    issues.append({
                        "issue": "Checkout button not found on cart page",
                        "severity": "HIGH", "label": "CONFIRMED",
                        "cro_category": "cart_flow",
                        "impact": "No checkout button means no orders can complete",
                        "fix": "Add checkout button to cart.liquid template"
                    })

    except Exception as e:
        issues.append({
            "issue": f"Cart flow test could not complete: {str(e)[:80]}",
            "severity": "MEDIUM", "label": "NEEDS VERIFICATION",
            "cro_category": "cart_flow",
            "impact": "Could not verify cart flow",
            "fix": "Test cart flow manually end to end"
        })

    return {"url": base_url, "check": "cro_cart", "issues": issues}


# ─────────────────────────────────────────────
#  CRO — MOBILE
# ─────────────────────────────────────────────

async def check_cro_mobile(url: str, page, screenshot_dir: str) -> dict:
    issues = []

    try:
        await page.set_viewport_size({"width": 390, "height": 844})
        await goto_and_wait(page, url, wait_ms=2000)

        # Horizontal scroll
        scroll_w = await page.evaluate("document.documentElement.scrollWidth")
        client_w = await page.evaluate("document.documentElement.clientWidth")
        if scroll_w > client_w + 5:
            fname = f"cro_mobile_scroll_{safe_filename(url)}.png"
            await page.screenshot(path=os.path.join(screenshot_dir, fname), full_page=False)
            issues.append({
                "issue": f"Horizontal scroll on mobile (overflows by {scroll_w - client_w}px)",
                "severity": "HIGH", "label": "CONFIRMED",
                "cro_category": "mobile_ux",
                "impact": "Horizontal scroll on mobile creates broken UX — visitors leave immediately",
                "fix": "Find overflowing elements and add max-width: 100%; overflow-x: hidden",
                "_screenshot": fname
            })

        # ATC tap target on product pages
        if "/products/" in url:
            atc_selectors = ['button[name="add"]', 'button.product-form__submit', '.product-form__submit']
            for sel in atc_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        box = await el.bounding_box()
                        if box and box["height"] < 44:
                            issues.append({
                                "issue": f"ATC button tap target too small on mobile ({int(box['height'])}px — min 44px)",
                                "severity": "MEDIUM", "label": "CONFIRMED",
                                "cro_category": "mobile_ux",
                                "impact": "Small tap targets cause mis-taps and frustration on mobile",
                                "fix": "Set min-height: 48px on Add to Cart button in mobile CSS"
                            })
                        break
                except Exception:
                    pass

        # Mobile screenshot
        fname = f"cro_mobile_{safe_filename(url)}.png"
        await page.screenshot(path=os.path.join(screenshot_dir, fname), full_page=False)

    except Exception:
        pass
    finally:
        await page.set_viewport_size({"width": 1440, "height": 900})

    return {"url": url, "check": "cro_mobile", "issues": issues}


# ─────────────────────────────────────────────
#  PAGE LOAD TIME
# ─────────────────────────────────────────────

async def check_page_load_time(url: str, page) -> dict:
    issues = []
    try:
        start = time.time()
        await page.goto(url, wait_until="networkidle", timeout=30000)
        load_time = round(time.time() - start, 2)

        if load_time > 5:
            issues.append({
                "issue": f"Page load time: {load_time}s — critical (above 5s)",
                "severity": "HIGH", "label": "CONFIRMED",
                "cro_category": "speed",
                "impact": "53% of mobile users abandon pages taking over 3s to load",
                "fix": "Audit installed apps — remove unused ones. Defer non-critical scripts. Compress images."
            })
        elif load_time > 3:
            issues.append({
                "issue": f"Page load time: {load_time}s — slow (above 3s)",
                "severity": "MEDIUM", "label": "CONFIRMED",
                "cro_category": "speed",
                "impact": "Slow pages increase bounce rate and reduce conversion",
                "fix": "Optimize images, remove unused apps, defer non-critical scripts"
            })

        return {"url": url, "check": "cro_speed", "issues": issues, "load_time": load_time}
    except Exception:
        return {"url": url, "check": "cro_speed", "issues": [], "load_time": None}


async def take_screenshot(page, url, screenshot_dir, label=""):
    fname = f"{label}_{safe_filename(url)}.png" if label else f"{safe_filename(url)}.png"
    fpath = os.path.join(screenshot_dir, fname)
    try:
        await page.screenshot(path=fpath, full_page=False)
    except Exception:
        pass
    return fpath


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def _emit(progress, message: str) -> None:
    """Send progress to UI callback and mirror to stdout."""
    print(message)
    if progress:
        try:
            progress(message)
        except Exception:
            pass


async def run_audit(base_url: str, mode: str = "full", progress=None) -> tuple:
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    _emit(progress, f"Starting {mode.upper()} audit for {base_url}...")
    _emit(progress, f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    output_dir     = make_output_dir(base_url)
    screenshot_dir = os.path.join(output_dir, "screenshots")
    all_results    = []
    load_times     = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # 1. Crawl
        _emit(progress, "[1/6] Crawling pages...")
        all_pages = await crawl_all_pages(base_url, page, max_pages=60)
        _emit(progress, f"      Found {len(all_pages)} pages.")

        # 2. Per-page SEO checks (full mode only)
        if mode == "full":
            _emit(progress, "[2/6] Running SEO checks on all pages...")
            for i, url in enumerate(all_pages):
                _emit(progress, f"      SEO [{i+1}/{len(all_pages)}] {url}")
                try:
                    await page.set_viewport_size({"width": 1440, "height": 900})
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(0.5)
                    soup = BeautifulSoup(await page.content(), "lxml")
                    for r in [
                        await check_page_meta(url, soup),
                        await check_images(url, soup),
                        await check_speed_signals(url, soup),
                        await check_structured_data(url, soup),
                    ]:
                        if r["issues"]: all_results.append(r)
                except Exception:
                    pass
        else:
            _emit(progress, "[2/6] Skipped SEO checks (CRO mode)")

        # 3. CRO — Homepage
        _emit(progress, "[3/6] Running CRO checks — Homepage...")
        hp_r = await check_cro_homepage(base_url, page, screenshot_dir)
        if hp_r["issues"]: all_results.append(hp_r)

        # 4. CRO — Product pages (first 5)
        _emit(progress, "[4/6] Running CRO checks — Product pages...")
        product_pages = [u for u in all_pages if "/products/" in u][:5]
        for url in product_pages:
            _emit(progress, f"      Product: {url}")
            await page.set_viewport_size({"width": 1440, "height": 900})
            prod_r = await check_cro_product_page(url, page, screenshot_dir)
            if prod_r["issues"]: all_results.append(prod_r)

        # 5. Cart flow
        _emit(progress, "[5/6] Testing cart flow...")
        await page.set_viewport_size({"width": 1440, "height": 900})
        cart_r = await check_cart_flow(base_url, page, screenshot_dir)
        if cart_r["issues"]: all_results.append(cart_r)

        # 6. Mobile + load time
        _emit(progress, "[6/6] Mobile checks + page load time...")
        mobile_urls = [base_url] + product_pages[:2]
        for url in mobile_urls:
            mob_r = await check_cro_mobile(url, page, screenshot_dir)
            if mob_r["issues"]: all_results.append(mob_r)

        lt_r = await check_page_load_time(base_url, page)
        load_times[base_url] = lt_r.get("load_time")
        if lt_r["issues"]: all_results.append(lt_r)

        if product_pages:
            lt_r2 = await check_page_load_time(product_pages[0], page)
            load_times[product_pages[0]] = lt_r2.get("load_time")
            if lt_r2["issues"]: all_results.append(lt_r2)

        if mode == "full":
            _emit(progress, "Checking broken links and navigation...")
            await page.set_viewport_size({"width": 1440, "height": 900})
            await page.goto(base_url, wait_until="domcontentloaded", timeout=20000)
            soup = BeautifulSoup(await page.content(), "lxml")
            for r in [
                await check_broken_links(base_url, soup),
                await check_navigation(base_url, soup),
            ]:
                if r["issues"]: all_results.append(r)

        # Screenshots
        _emit(progress, "Capturing homepage screenshots...")
        await page.set_viewport_size({"width": 1440, "height": 900})
        await goto_and_wait(page, base_url, wait_ms=1000)
        await take_screenshot(page, base_url, screenshot_dir, "homepage_desktop")
        await page.set_viewport_size({"width": 390, "height": 844})
        await goto_and_wait(page, base_url, wait_ms=1000)
        await take_screenshot(page, base_url, screenshot_dir, "homepage_mobile")

        await browser.close()

    audit_data = {
        "store_url":    base_url,
        "audit_date":   datetime.now().isoformat(),
        "audit_mode":   mode,
        "pages_crawled": all_pages,
        "load_times":   load_times,
        "results":      all_results,
    }

    json_path = os.path.join(output_dir, "audit_data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(audit_data, f, indent=2, ensure_ascii=False)

    total = sum(len(r["issues"]) for r in all_results)
    _emit(progress, f"Audit crawl done — {total} raw issues found.")
    _emit(progress, f"Saved data to: {output_dir}")

    return audit_data, output_dir
