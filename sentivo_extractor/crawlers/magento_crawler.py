"""
Magento 2 category + product URL discoverer.

Strategy:
1. Parse the main nav <nav> element for category links
2. For each category, paginate through ?p=1, ?p=2... until no products found
3. Collect all product URLs (product-item links / Magento SEO slugs)
4. Deduplicate and return
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from sentivo_extractor.core.product_discovery import (
    canonicalize_product_url,
    unique_preserve,
)
from sentivo_extractor.core.utils import BROWSER_HEADERS

GetTextFn = Callable[[str], str]


class _PlaywrightSession:
    """
    Reusable headed Chrome session for Magento nav crawl.

    Cloudflare blocks requests/httpx (502) and headless Chromium (challenge).
    Headed system Chrome + networkidle + settle wait passes CF. One browser is
    kept open for the whole discover() run so cookies persist across pages.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def __enter__(self) -> "_PlaywrightSession":
        from playwright.sync_api import sync_playwright

        from sentivo_extractor.core.platform_detector import (
            _launch_chromium_for_cloudflare,
        )

        self._pw = sync_playwright().start()
        self._browser = _launch_chromium_for_cloudflare(self._pw)
        extra = {
            k: v
            for k, v in BROWSER_HEADERS.items()
            if k.lower() != "user-agent"
        }
        self._context = self._browser.new_context(
            extra_http_headers=extra,
            viewport={"width": 1440, "height": 900},
            locale="en-GB",
        )
        self._page = self._context.new_page()
        self.logger.info(
            "[Magento] Playwright session started (headed Chrome, Cloudflare bypass)"
        )
        return self

    def get_text(self, url: str) -> str:
        assert self._page is not None
        self._page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            self._page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        self._page.wait_for_timeout(2500)
        text = self._page.content() or ""
        low = text.lower()
        if (
            "just a moment" in low
            or "cf-browser-verification" in low
            or "attention required" in low
        ):
            self._page.wait_for_timeout(3000)
            try:
                self._page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            text = self._page.content() or ""
        return text

    def __exit__(self, *exc: object) -> None:
        for obj in (self._page, self._context, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._page = self._context = self._browser = self._pw = None

_SKIP_NAV_FRAGMENTS = (
    "/cart",
    "/checkout",
    "/customer",
    "/account",
    "/login",
    "/wishlist",
    "/search",
    "/catalogsearch",
    "/contact",
    "/blog",
    "/privacy",
    "/terms",
    "/cookie",
    "#",
    "mailto:",
    "tel:",
    "javascript:",
)

_CATEGORY_HINTS = (
    "/shop",
    "/category",
    "/categories",
    "/plastics",
    "/sheets",
    "/rods",
    "/tubes",
    "/acrylic",
    "/polycarbonate",
    "/cut-to-size",
    "/materials",
    "/products",
    "/collections",
)

_PRODUCT_LINK_SELECTORS = (
    "a.product-item-link",
    "li.product-item a.product-item-link",
    "li.product-item a.product-item-photo",
    ".product-item-name a[href]",
    ".product-item a[href]",
    "a.product-item-photo",
)


class MagentoCategoryCrawler:
    """Discover Magento category pages from nav, then product URLs with ?p= pagination."""

    def __init__(
        self,
        get_text: GetTextFn,
        *,
        max_products: int = 500,
        max_pages_per_category: int = 40,
        max_categories: int = 80,
        logger: logging.Logger | None = None,
    ) -> None:
        self.get_text = get_text
        self.max_products = max(1, int(max_products))
        self.max_pages_per_category = max(1, int(max_pages_per_category))
        self.max_categories = max(1, int(max_categories))
        self.logger = logger or logging.getLogger(__name__)

    def discover(self, seed_url: str) -> dict[str, Any]:
        seed = (seed_url or "").strip()
        if not seed:
            return {
                "product_urls": [],
                "category_urls": [],
                "notes": ["empty_seed"],
                "platform": "Magento",
            }

        notes: list[str] = []
        origin = self._origin(seed)
        domain = urlparse(origin).netloc.lower().removeprefix("www.")
        self.logger.info("[Magento] Starting nav-based category crawl for %s", domain)

        # Prefer Playwright (CF bypass). Fall back to injected get_text only if PW fails to start.
        fetch: GetTextFn = self.get_text
        pw_session: _PlaywrightSession | None = None
        try:
            pw_session = _PlaywrightSession(self.logger)
            pw_session.__enter__()
            fetch = pw_session.get_text
            notes.append("magento_playwright_fetch")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"playwright_unavailable:{exc}")
            self.logger.warning(
                "[Magento] Playwright unavailable (%s) — falling back to HTTP get_text",
                exc,
            )
            pw_session = None

        try:
            return self._discover_with_fetch(seed, origin, domain, notes, fetch)
        finally:
            if pw_session is not None:
                pw_session.__exit__(None, None, None)

    def _discover_with_fetch(
        self,
        seed: str,
        origin: str,
        domain: str,
        notes: list[str],
        fetch: GetTextFn,
    ) -> dict[str, Any]:
        try:
            home_html = fetch(seed)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"seed_fetch_failed:{exc}")
            self.logger.error("[Magento] Homepage fetch failed: %s", exc)
            return {
                "product_urls": [],
                "category_urls": [],
                "notes": notes,
                "platform": "Magento",
            }

        if not (home_html or "").strip():
            notes.append("seed_html_empty")
            self.logger.error("[Magento] Homepage HTML empty for %s", domain)
            return {
                "product_urls": [],
                "category_urls": [],
                "notes": notes,
                "platform": "Magento",
            }

        categories = self.extract_nav_categories(home_html, seed)
        if not categories:
            categories = [canonicalize_product_url(seed) or seed]
            notes.append("nav_empty_using_seed_as_category")
        else:
            notes.append(f"nav_categories:{len(categories)}")
        self.logger.info("[Magento] Found %s category URLs", len(categories))

        product_urls: list[str] = []
        for cat in categories[: self.max_categories]:
            if len(product_urls) >= self.max_products:
                break
            found = self.crawl_category(cat, fetch=fetch)
            notes.append(f"category:{cat}:{len(found)}")
            product_urls.extend(found)
            self.logger.info(
                "[Magento] Category crawl: %s product URL(s) from %s",
                len(found),
                cat,
            )

        home_products = self.extract_product_urls(home_html, seed)
        if home_products:
            notes.append(f"homepage_products:{len(home_products)}")
            product_urls.extend(home_products)

        deduped = unique_preserve(product_urls)[: self.max_products]
        notes.append(f"total_products:{len(deduped)}")
        self.logger.info("[Magento] Discovered %s product URLs", len(deduped))
        return {
            "product_urls": deduped,
            "category_urls": categories[: self.max_categories],
            "notes": notes,
            "platform": "Magento",
            "origin": origin,
        }

    def extract_nav_categories(self, html: str, base_url: str) -> list[str]:
        soup = BeautifulSoup(html or "", "lxml")
        origin = self._origin(base_url)
        host = urlparse(origin).netloc.lower()
        found: list[str] = []
        seen: set[str] = set()

        anchors = soup.select("nav a[href], .navigation a[href], .nav-sections a[href]")
        if not anchors:
            anchors = soup.select("header a[href], .page-header a[href]")

        for a in anchors:
            href = (a.get("href") or "").strip()
            if not href:
                continue
            low = href.lower()
            if any(s in low for s in _SKIP_NAV_FRAGMENTS):
                continue
            full = urljoin(base_url, href).split("#")[0]
            parsed = urlparse(full)
            if parsed.netloc.lower() != host:
                continue
            path = (parsed.path or "/").rstrip("/") or "/"
            if path == "/":
                continue
            # Prefer category-ish paths; still accept deep internal paths with 2+ segments.
            path_low = path.lower()
            looks_category = any(h in path_low for h in _CATEGORY_HINTS) or path.count("/") >= 1
            if not looks_category:
                continue
            # Skip obvious PDP view-by-id links from nav.
            if "/catalog/product/" in path_low:
                continue
            key = canonicalize_product_url(full) or full.lower().rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            found.append(full)
            if len(found) >= self.max_categories:
                break
        return found

    def crawl_category(
        self, category_url: str, *, fetch: GetTextFn | None = None
    ) -> list[str]:
        """Paginate Magento category with ?p=N until a page yields no products."""
        get_html = fetch or self.get_text
        collected: list[str] = []
        seen: set[str] = set()
        for page_num in range(1, self.max_pages_per_category + 1):
            page_url = self._with_page(category_url, page_num)
            try:
                html = get_html(page_url)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Magento category page failed %s: %s", page_url, exc)
                break

            products = self.extract_product_urls(html, page_url)
            new_count = 0
            for pu in products:
                key = canonicalize_product_url(pu) or pu
                if key in seen:
                    continue
                seen.add(key)
                collected.append(pu)
                new_count += 1
                if len(collected) >= self.max_products:
                    return collected

            amount = self._toolbar_amount(html)
            self.logger.info(
                "Magento page %s: %s new product link(s)%s",
                page_url,
                new_count,
                f" (toolbar: {amount})" if amount else "",
            )

            if new_count == 0:
                # First page empty → stop; later pages empty → end of pagination.
                break
            if not self._has_next_page(html, page_num):
                # Heuristic: if toolbar says we're done, stop; else try one more.
                if amount and self._toolbar_exhausted(amount, len(seen)):
                    break
                # Magento often still has next even without "Next" title — continue
                # only while new products appear (already handled by new_count==0).
                if not self._page_param_present(html) and page_num >= 2:
                    break
        return collected

    def extract_product_urls(self, html: str, base_url: str) -> list[str]:
        soup = BeautifulSoup(html or "", "lxml")
        host = urlparse(base_url).netloc.lower()
        found: list[str] = []
        seen: set[str] = set()

        anchors = []
        for sel in _PRODUCT_LINK_SELECTORS:
            try:
                anchors.extend(soup.select(sel))
            except Exception:
                continue

        for a in anchors:
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            full = urljoin(base_url, href).split("#")[0]
            parsed = urlparse(full)
            if parsed.netloc.lower() != host:
                continue
            path = (parsed.path or "").lower()
            if any(s in path for s in _SKIP_NAV_FRAGMENTS):
                continue
            # Category pagination / filter URLs are not products.
            if re.search(r"[?&]p=\d+", full) and "product-item" not in (
                " ".join(a.get("class") or [])
            ).lower():
                # Still allow if it's clearly a product-item-link.
                classes = " ".join(a.get("class") or []).lower()
                if "product-item-link" not in classes and "product-item-photo" not in classes:
                    continue
            cu = canonicalize_product_url(full) or full
            if cu in seen:
                continue
            seen.add(cu)
            found.append(cu)
        return found

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _with_page(url: str, page: int) -> str:
        if page <= 1:
            # Strip existing p= for a clean first page.
            parsed = urlparse(url)
            qs = parse_qs(parsed.query, keep_blank_values=False)
            qs.pop("p", None)
            query = urlencode({k: v[0] if len(v) == 1 else v for k, v in qs.items()}, doseq=True)
            return urlunparse(
                (parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, "")
            )
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=False)
        qs["p"] = [str(page)]
        query = urlencode({k: v[0] if len(v) == 1 else v for k, v in qs.items()}, doseq=True)
        return urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, "")
        )

    @staticmethod
    def _toolbar_amount(html: str) -> str:
        soup = BeautifulSoup(html or "", "lxml")
        el = soup.select_one(".toolbar-amount, .toolbar-number, #toolbar-amount")
        if el is None:
            return ""
        return el.get_text(" ", strip=True)

    @staticmethod
    def _toolbar_exhausted(amount_text: str, collected: int) -> bool:
        nums = [int(x) for x in re.findall(r"\d+", amount_text or "")]
        if len(nums) >= 3:
            # "Items 1 to 12 of 48" → last is total
            return collected >= nums[-1]
        if len(nums) == 1:
            return collected >= nums[0]
        return False

    @staticmethod
    def _has_next_page(html: str, current_page: int) -> bool:
        soup = BeautifulSoup(html or "", "lxml")
        if soup.select_one('a[title="Next"], a.action.next, .pages-item-next a'):
            return True
        for a in soup.select(".pages a[href], .pager a[href], .toolbar a[href]"):
            href = a.get("href") or ""
            m = re.search(r"[?&]p=(\d+)", href)
            if m and int(m.group(1)) > current_page:
                return True
        return False

    @staticmethod
    def _page_param_present(html: str) -> bool:
        return bool(re.search(r"[?&]p=\d+", html or "", re.I)) or bool(
            BeautifulSoup(html or "", "lxml").select_one(".pages, .pager, .toolbar-amount")
        )


def discover_magento_products(
    seed_url: str,
    get_text: GetTextFn,
    *,
    max_products: int = 500,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Convenience wrapper used by unified discovery."""
    crawler = MagentoCategoryCrawler(
        get_text,
        max_products=max_products,
        logger=logger,
    )
    return crawler.discover(seed_url)


def magento_request_headers() -> dict[str, str]:
    """Headers that avoid 403 on Magento storefronts like sheetplastics.co.uk."""
    return dict(BROWSER_HEADERS)
