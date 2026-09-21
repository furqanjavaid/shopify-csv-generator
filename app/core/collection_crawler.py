"""Crawl Shopify collection pages via the public JSON / product.js APIs."""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests

ProgressCallback = Callable[[str], None]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_DELAY_SEC = 1.0
PAGE_LIMIT = 250

# Column headers for MappingScreen (match Shopify field names for clean auto-map)
DRAFT_HEADERS = [
    "Title",
    "URL handle",
    "Description",
    "Vendor",
    "Product category",
    "Type",
    "Tags",
    "SKU",
    "Barcode",
    "Option1 name",
    "Option1 value",
    "Option2 name",
    "Option2 value",
    "Option3 name",
    "Option3 value",
    "Price",
    "Compare-at price",
    "Inventory quantity",
    "Product image URL",
    "Image position",
    "Status",
]

DRAFT_KEY_TO_HEADER = {
    "title": "Title",
    "url_handle": "URL handle",
    "description": "Description",
    "vendor": "Vendor",
    "product_category": "Product category",
    "type": "Type",
    "tags": "Tags",
    "sku": "SKU",
    "barcode": "Barcode",
    "option1_name": "Option1 name",
    "option1_value": "Option1 value",
    "option2_name": "Option2 name",
    "option2_value": "Option2 value",
    "option3_name": "Option3 name",
    "option3_value": "Option3 value",
    "price": "Price",
    "compare_at_price": "Compare-at price",
    "inventory_quantity": "Inventory quantity",
    "product_image_url": "Product image URL",
    "image_position": "Image position",
    "status": "Status",
}


class CollectionCrawlError(Exception):
    """Raised when a collection crawl cannot proceed."""


class CollectionCrawler:
    """Fetch all products from a Shopify collection URL."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/javascript, */*;q=0.8",
            }
        )
        self.errors: list[str] = []

    def crawl(
        self,
        url: str,
        progress: Optional[ProgressCallback] = None,
    ) -> dict[str, Any]:
        """
        Crawl a Shopify collection (or single product) URL.

        Returns parsed_data compatible with MappingScreen:
        {"headers", "rows", "row_count", "strategy_used", "errors"}
        """
        self.errors = []
        base_url, collection_handle, product_handle = self._parse_url(url)

        self._emit(progress, "Checking Shopify store...")
        self._assert_shopify_store(base_url)

        is_uk = self._is_uk_store(base_url)
        drafts: list[dict[str, str]] = []

        if product_handle and not collection_handle:
            self._emit(progress, f"Fetching product: {product_handle}")
            draft_rows = self._fetch_product_drafts(
                base_url, product_handle, is_uk, progress
            )
            drafts.extend(draft_rows)
        else:
            if not collection_handle:
                raise CollectionCrawlError(
                    "URL must be a Shopify collection "
                    "(…/collections/handle) or product (…/products/handle)."
                )
            handles = self._paginate_collection(base_url, collection_handle, progress)
            total = len(handles)
            self._emit(progress, f"Found {total} products. Fetching details...")

            for i, handle in enumerate(handles, start=1):
                self._emit(
                    progress,
                    f"Fetching product {i}/{total}: {handle}",
                )
                try:
                    draft_rows = self._fetch_product_drafts(
                        base_url, handle, is_uk, progress
                    )
                    drafts.extend(draft_rows)
                    self._emit(
                        progress,
                        f"Loaded {len(drafts)} variants from {i}/{total} products...",
                    )
                except Exception as exc:  # noqa: BLE001
                    msg = f"Failed product '{handle}': {exc}"
                    self.errors.append(msg)
                    self._emit(progress, msg)

        if not drafts:
            raise CollectionCrawlError(
                "No products found. Check the collection URL or try again."
            )

        parsed = drafts_to_parsed_data(drafts)
        parsed["strategy_used"] = "Shopify Collection JSON"
        parsed["errors"] = list(self.errors)
        self._emit(progress, f"Done — {parsed['row_count']} rows from collection crawl")
        return parsed

    # ------------------------------------------------------------------
    # URL / store helpers
    # ------------------------------------------------------------------

    def _parse_url(self, url: str) -> tuple[str, Optional[str], Optional[str]]:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise CollectionCrawlError("URL must start with http:// or https://")

        parsed = urlparse(url)
        if not parsed.netloc:
            raise CollectionCrawlError("Invalid URL.")

        base_url = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        path = parsed.path or ""

        collection_handle = None
        product_handle = None

        col_match = re.search(r"/collections/([^/?#]+)", path, re.I)
        if col_match:
            collection_handle = col_match.group(1).strip()

        prod_match = re.search(r"/products/([^/?#]+)", path, re.I)
        if prod_match:
            product_handle = prod_match.group(1).strip()

        # Explicit product URL (…/products/handle) → single product fetch
        if product_handle:
            return base_url, None, product_handle

        return base_url, collection_handle, None

    def _assert_shopify_store(self, base_url: str) -> None:
        """Hit a lightweight Shopify endpoint; raise if not a Shopify store."""
        probe = f"{base_url}/products.json?limit=1"
        try:
            resp = self._get(probe)
        except CollectionCrawlError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CollectionCrawlError(
                "This URL is not a Shopify store. Only Shopify stores "
                "support JSON product endpoints."
            ) from exc

        content_type = (resp.headers.get("Content-Type") or "").lower()
        if resp.status_code == 404 or "json" not in content_type:
            # Some stores block /products.json but allow collections — try meta
            if resp.status_code >= 400:
                raise CollectionCrawlError(
                    "This URL is not a Shopify store. Only Shopify stores "
                    "support JSON product endpoints."
                )
        try:
            data = resp.json()
        except ValueError as exc:
            raise CollectionCrawlError(
                "This URL is not a Shopify store. Only Shopify stores "
                "support JSON product endpoints."
            ) from exc

        if not isinstance(data, dict) or "products" not in data:
            raise CollectionCrawlError(
                "This URL is not a Shopify store. Only Shopify stores "
                "support JSON product endpoints."
            )

    @staticmethod
    def _is_uk_store(base_url: str) -> bool:
        host = urlparse(base_url).netloc.lower()
        return host.endswith(".co.uk") or host.endswith(".uk") or ".co.uk" in host

    # ------------------------------------------------------------------
    # Collection pagination
    # ------------------------------------------------------------------

    def _paginate_collection(
        self,
        base_url: str,
        handle: str,
        progress: Optional[ProgressCallback],
    ) -> list[str]:
        handles: list[str] = []
        page = 1
        while True:
            self._emit(progress, f"Fetching page {page}...")
            endpoint = (
                f"{base_url}/collections/{handle}/products.json"
                f"?limit={PAGE_LIMIT}&page={page}"
            )
            resp = self._get(endpoint)
            try:
                data = resp.json()
            except ValueError as exc:
                raise CollectionCrawlError(
                    "This URL is not a Shopify store. Only Shopify stores "
                    "support JSON product endpoints."
                ) from exc

            products = data.get("products") or []
            if not products:
                break

            for product in products:
                h = (product.get("handle") or "").strip()
                if h:
                    handles.append(h)

            self._emit(
                progress,
                f"Fetching page {page}... ({len(handles)} products so far)",
            )
            page += 1
            # Safety cap
            if page > 200:
                break

        return handles

    # ------------------------------------------------------------------
    # Product fetch → ProductDraft rows (one per variant)
    # ------------------------------------------------------------------

    def _fetch_product_drafts(
        self,
        base_url: str,
        handle: str,
        is_uk: bool,
        progress: Optional[ProgressCallback],
    ) -> list[dict[str, str]]:
        endpoint = f"{base_url}/products/{handle}.js"
        resp = self._get(endpoint)
        try:
            product = resp.json()
        except ValueError as exc:
            raise CollectionCrawlError(f"Invalid product.js for '{handle}'") from exc

        title = str(product.get("title") or "").strip()
        url_handle = str(product.get("handle") or handle).strip()
        # body_html / description must stay ONE string — never split on commas/tags
        description = self._as_single_html_string(
            product.get("body_html")
            if product.get("body_html") not in (None, "")
            else product.get("description")
        )
        vendor = str(product.get("vendor") or "").strip()
        product_type = str(product.get("type") or product.get("product_type") or "").strip()

        tags_raw = product.get("tags") or []
        if isinstance(tags_raw, str):
            tags = tags_raw
        else:
            tags = ", ".join(str(t) for t in tags_raw if t)

        options = product.get("options") or []
        opt_names = ["", "", ""]
        for i, opt in enumerate(options[:3]):
            if isinstance(opt, dict):
                opt_names[i] = str(opt.get("name") or "").strip()
            else:
                opt_names[i] = str(opt).strip()

        # Skip default "Title" option name when it's the sole Default Title variant
        for i, name in enumerate(opt_names):
            if name.lower() == "title":
                opt_names[i] = ""

        images = product.get("images") or []
        featured = product.get("featured_image")
        image_url = ""
        if isinstance(featured, dict):
            image_url = str(featured.get("src") or "")
        elif isinstance(featured, str):
            image_url = featured
        if not image_url and images:
            first = images[0]
            if isinstance(first, dict):
                image_url = str(first.get("src") or "")
            else:
                image_url = str(first)

        variants = product.get("variants") or []
        image_url = self._absolute_url(image_url)

        if not variants:
            price, compare = self._normalize_prices(
                product.get("price"), product.get("compare_at_price"), is_uk
            )
            return [
                self._make_draft(
                    title=title,
                    url_handle=url_handle,
                    description=description,
                    vendor=vendor,
                    product_type=product_type,
                    tags=tags,
                    sku="",
                    barcode="",
                    opt_names=opt_names,
                    opt_values=["", "", ""],
                    price=price,
                    compare_at_price=compare,
                    inventory_quantity="",
                    image_url=image_url,
                    is_first=True,
                )
            ]

        drafts: list[dict[str, str]] = []
        for index, variant in enumerate(variants):
            price, compare = self._normalize_prices(
                variant.get("price"),
                variant.get("compare_at_price"),
                is_uk,
            )
            opt_values = [
                str(variant.get("option1") or "").strip(),
                str(variant.get("option2") or "").strip(),
                str(variant.get("option3") or "").strip(),
            ]
            for i, val in enumerate(opt_values):
                if val.lower() in {"default title", "default"}:
                    opt_values[i] = ""

            inv = variant.get("inventory_quantity")
            if inv is None:
                inv_str = ""
            else:
                try:
                    inv_str = str(max(0, int(inv)))
                except (TypeError, ValueError):
                    inv_str = str(inv)

            v_image = image_url
            feat = variant.get("featured_image")
            if isinstance(feat, dict) and feat.get("src"):
                v_image = self._absolute_url(str(feat["src"]))
            elif isinstance(feat, str):
                v_image = self._absolute_url(feat)

            is_first = index == 0
            drafts.append(
                self._make_draft(
                    title=title if is_first else "",
                    url_handle=url_handle,
                    description=description if is_first else "",
                    vendor=vendor if is_first else "",
                    product_type=product_type if is_first else "",
                    tags=tags if is_first else "",
                    sku=str(variant.get("sku") or "").strip(),
                    barcode=str(variant.get("barcode") or "").strip() if is_first else "",
                    opt_names=opt_names if is_first else ["", "", ""],
                    opt_values=opt_values,
                    price=price,
                    compare_at_price=compare,
                    inventory_quantity=inv_str,
                    # Image only on first (product) row
                    image_url=v_image if is_first else "",
                    is_first=is_first,
                )
            )
        return drafts

    def _make_draft(
        self,
        *,
        title: str,
        url_handle: str,
        description: str,
        vendor: str,
        product_type: str,
        tags: str,
        sku: str,
        barcode: str,
        opt_names: list[str],
        opt_values: list[str],
        price: str,
        compare_at_price: str,
        inventory_quantity: str,
        image_url: str,
        is_first: bool = True,
    ) -> dict[str, str]:
        names = list(opt_names)
        for i in range(3):
            if not opt_values[i]:
                names[i] = ""

        image_url = self._absolute_url(image_url)

        if is_first:
            return {
                "title": title,
                "url_handle": url_handle,
                "description": description,
                "vendor": vendor,
                "product_category": "",
                "type": product_type,
                "tags": tags,
                "sku": sku,
                "barcode": barcode,
                "option1_name": names[0],
                "option1_value": opt_values[0],
                "option2_name": names[1],
                "option2_value": opt_values[1],
                "option3_name": names[2],
                "option3_value": opt_values[2],
                "price": price,
                "compare_at_price": compare_at_price,
                "inventory_quantity": inventory_quantity,
                "product_image_url": image_url,
                "image_position": "1" if image_url else "",
                "status": "Active",
            }

        # Subsequent variant rows: same handle, variant fields only
        return {
            "title": "",
            "url_handle": url_handle,
            "description": "",
            "vendor": "",
            "product_category": "",
            "type": "",
            "tags": "",
            "sku": sku,
            "barcode": "",
            "option1_name": "",
            "option1_value": opt_values[0],
            "option2_name": "",
            "option2_value": opt_values[1],
            "option3_name": "",
            "option3_value": opt_values[2],
            "price": price,
            "compare_at_price": compare_at_price,
            "inventory_quantity": inventory_quantity,
            "product_image_url": "",
            "image_position": "",
            "status": "Active",
        }

    @staticmethod
    def _as_single_html_string(value: Any) -> str:
        """
        Coerce Shopify body_html / description to one cell value.

        Never iterate or split on commas — HTML often contains commas inside
        tags and prose; splitting would create bogus extra product rows.
        """
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            # Some payloads rarely nest fragments — join, do not emit multiple rows
            value = "".join(str(part) for part in value if part is not None)
        text = str(value)
        # Normalize newlines to spaces so one logical cell stays one row when
        # viewed in naive editors; csv.QUOTE_ALL still preserves content safely.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Keep paragraph breaks as spaces (Shopify import accepts either)
        text = re.sub(r"\n+", " ", text)
        return text.strip()

    @staticmethod
    def _absolute_url(url: str) -> str:
        """Ensure protocol-relative Shopify CDN URLs use https:."""
        url = (url or "").strip()
        if url.startswith("//"):
            return "https:" + url
        return url

    def _normalize_prices(
        self,
        raw_price: Any,
        raw_compare: Any,
        is_uk: bool,
    ) -> tuple[str, str]:
        """
        Convert Shopify product.js prices to display pounds.

        Note: Shopify's product.js returns prices in the shop's subunit
        (pence for GBP) as integers — e.g. 989 → £9.89. We convert to pounds,
        then for UK stores strip VAT (÷ 1.2) and set compare-at to the
        original inc-VAT amount.
        """
        inc_vat = self._to_pounds(raw_price)
        compare_raw = self._to_pounds(raw_compare) if raw_compare not in (None, "", 0, "0") else None

        if inc_vat is None:
            return "", self._fmt(compare_raw) if compare_raw is not None else ""

        if is_uk:
            ex_vat = round(inc_vat / 1.2, 2)
            # Compare-at = original inc-VAT (or higher existing compare-at)
            higher = inc_vat
            if compare_raw is not None and compare_raw > higher:
                higher = compare_raw
            return self._fmt(ex_vat), self._fmt(higher)

        compare = compare_raw if compare_raw is not None else None
        return self._fmt(inc_vat), self._fmt(compare) if compare is not None else ""

    @staticmethod
    def _to_pounds(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            if isinstance(value, bool):
                return None
            if isinstance(value, int):
                # product.js integer = pence/cents
                return round(value / 100.0, 2)
            if isinstance(value, float):
                # Already major units if small fractional, else treat as pounds
                return round(value, 2)
            text = str(value).strip().replace(",", "")
            if not text:
                return None
            # String from some endpoints is already pounds ("9.89")
            if "." in text:
                return round(float(text), 2)
            # Integer string — treat as pence (product.js style)
            return round(int(text) / 100.0, 2)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _fmt(value: Optional[float]) -> str:
        if value is None:
            return ""
        return f"{value:.2f}"

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _get(self, url: str, retries: int = 3) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                time.sleep(REQUEST_DELAY_SEC)
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 429:
                    self._emit(None, "Rate limited (429) — waiting 5 seconds...")
                    time.sleep(5)
                    continue
                if resp.status_code >= 400:
                    raise CollectionCrawlError(
                        f"HTTP {resp.status_code} for {url}"
                    )
                return resp
            except CollectionCrawlError:
                raise
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(2)
        raise CollectionCrawlError(f"Request failed for {url}: {last_exc}")

    @staticmethod
    def _emit(progress: Optional[ProgressCallback], message: str) -> None:
        if progress:
            try:
                progress(message)
            except Exception:  # noqa: BLE001
                pass


def drafts_to_parsed_data(drafts: list[dict[str, str]]) -> dict[str, Any]:
    """Convert ProductDraft dicts into MappingScreen parsed_data format."""
    rows: list[dict[str, str]] = []
    for draft in drafts:
        row: dict[str, str] = {}
        for key, header in DRAFT_KEY_TO_HEADER.items():
            raw = draft.get(key, "") or ""
            # Description must remain a single string cell (never list/iterable)
            if key == "description" and not isinstance(raw, str):
                raw = CollectionCrawler._as_single_html_string(raw)
            else:
                raw = str(raw)
            row[header] = raw
        rows.append(row)

    return {
        "headers": list(DRAFT_HEADERS),
        "rows": rows,
        "row_count": len(rows),
    }


def crawl(url: str, progress: Optional[ProgressCallback] = None) -> dict[str, Any]:
    """
    Module-level entry point used by the UI.

    Detects Shopify via /products.json; otherwise uses Playwright HTML scraping
    (WooCommerce / Custom).
    """
    from app.core.html_catalog_scraper import (
        HtmlCatalogScraper,
        detect_platform,
        detect_shopify,
    )

    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise CollectionCrawlError("URL must start with http:// or https://")

    parsed = urlparse(url)
    if not parsed.netloc:
        raise CollectionCrawlError("Invalid URL.")
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    CollectionCrawler._emit(progress, "Detecting platform…")
    try:
        is_shopify = detect_shopify(base_url)
    except Exception:
        is_shopify = False

    if is_shopify:
        CollectionCrawler._emit(progress, "Platform: Shopify")
        result = CollectionCrawler().crawl(url, progress=progress)
        result["platform"] = "Shopify"
        return result

    platform = detect_platform(url)
    if platform == "Shopify":
        # detect_platform confirmed Shopify via a second path — still use JSON crawler
        result = CollectionCrawler().crawl(url, progress=progress)
        result["platform"] = "Shopify"
        return result

    CollectionCrawler._emit(progress, f"Platform: {platform}")
    return HtmlCatalogScraper().scrape(url, progress=progress, platform=platform)
