"""
Magento / Hyva PDP field extractor (sheetplastics.co.uk and similar).

Primary DOM selectors for cut-to-size Magento 2 storefronts. Returns the
standard internal product schema used by the Decision Engine.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from sentivo_extractor.core.schema import empty_image, empty_product, empty_variant
from sentivo_extractor.core.utils import absolute_url, normalize_price, sanitize_sku
from sentivo_extractor.extractors.base import BaseExtractor

logger = logging.getLogger(__name__)

CUT_TO_SIZE_TAG = "cut-to-size pricing"

_TITLE_SELECTORS = (
    "h1.page-title span",
    "h1.page-title",
    "h1[itemprop='name']",
    "h1",
)
_SKU_SELECTORS = (
    ".product-info-stock-sku .value",
    "span[itemprop='sku']",
    ".sku .value",
    ".product.attribute.sku .value",
    "[itemprop='sku']",
)
_DESC_SELECTORS = (
    ".product.attribute.description .value",
    ".product.attribute.overview .value",
    "#description .product.attribute.description .value",
    "#description",
    ".product-info-main .description",
    "[itemprop='description']",
)
_PRICE_SELECTORS = (
    ".price-wrapper .price",
    ".price-box .price",
    "span[data-price-type='finalPrice'] .price",
    "[itemprop='price']",
    ".price",
)
_IMAGE_SELECTORS = (
    "img.gallery-placeholder__image",
    ".fotorama__img",
    ".fotorama__stage__frame img",
    ".gallery-placeholder img",
    ".product.media img",
    "img[itemprop='image']",
)
_SPEC_ROW_SELECTORS = (
    ".data.table.additional-attributes tr",
    "#product-attribute-specs-table tr",
    "table.additional-attributes tr",
    ".additional-attributes-wrapper tr",
)


class MagentoProductExtractor(BaseExtractor):
    """Hyva / Magento 2 DOM extractor — Title, SKU, Description, Price, Images, Specs."""

    name = "magento_product"
    platforms = ("Magento",)

    def extract(
        self,
        url: str,
        html: str = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not html:
            return None
        log = (context or {}).get("logger") or logger
        soup = BeautifulSoup(html, "lxml")

        title = self._first_text(soup, _TITLE_SELECTORS)
        if not title:
            return None

        sku = sanitize_sku(self._first_text(soup, _SKU_SELECTORS))
        description_html, description_text = self._description(soup)
        specs = self._specs(soup)
        if specs:
            spec_line = " | ".join(f"{k}: {v}" for k, v in specs.items())
            if description_html:
                description_html = (
                    f"{description_html}<p><strong>Specifications</strong><br>{spec_line}</p>"
                )
            else:
                description_html = f"<p>{spec_line}</p>"
            if description_text:
                description_text = f"{description_text}\n{spec_line}"
            else:
                description_text = spec_line

        price = self._price(soup)
        images = self._images(soup, url)

        product = empty_product()
        product["source_url"] = url
        product["title"] = title
        product["description_html"] = description_html
        product["platform"] = "Magento"
        product["extraction_method"] = "magento_product_dom"
        product["confidence_score"] = 0.82
        product["images"] = images
        product["specifications"] = specs
        product["tags"] = [CUT_TO_SIZE_TAG]

        variant = empty_variant()
        variant["sku"] = sku
        variant["price"] = price
        variant["available"] = True
        product["variants"] = [variant]
        if sku:
            product["sku"] = sku
        if price:
            product["price"] = price

        log.info(
            "MagentoProductExtractor: title=%s sku=%s price=%s images=%s specs=%s",
            title[:60],
            sku or "-",
            price or "-",
            len(images),
            len(specs),
        )
        return product

    def _first_text(self, soup: BeautifulSoup, selectors: tuple[str, ...]) -> str:
        for sel in selectors:
            try:
                el = soup.select_one(sel)
            except Exception:
                continue
            if el is None:
                continue
            text = el.get_text(" ", strip=True)
            if text:
                return text
        return ""

    def _description(self, soup: BeautifulSoup) -> tuple[str, str]:
        for sel in _DESC_SELECTORS:
            try:
                el = soup.select_one(sel)
            except Exception:
                continue
            if el is None:
                continue
            html = el.decode_contents().strip() if hasattr(el, "decode_contents") else ""
            text = el.get_text(" ", strip=True)
            if html or text:
                return html or f"<p>{text}</p>", text
        return "", ""

    def _price(self, soup: BeautifulSoup) -> str:
        for sel in _PRICE_SELECTORS:
            try:
                el = soup.select_one(sel)
            except Exception:
                continue
            if el is None:
                continue
            raw = ""
            if el.has_attr("content"):
                raw = str(el.get("content") or "")
            if not raw:
                raw = el.get_text(" ", strip=True)
            price = normalize_price(raw)
            if price:
                return price
        # data-price-amount on wrappers
        for el in soup.select("[data-price-amount]"):
            raw = el.get("data-price-amount") or ""
            price = normalize_price(str(raw))
            if price:
                return price
        return ""

    def _specs(self, soup: BeautifulSoup) -> dict[str, str]:
        specs: dict[str, str] = {}
        for sel in _SPEC_ROW_SELECTORS:
            try:
                rows = soup.select(sel)
            except Exception:
                continue
            for tr in rows:
                th = tr.select_one("th, .col.label, td.label")
                td = tr.select_one("td:not(.label), .col.data, td.data")
                if th is None or td is None:
                    cells = tr.find_all(["th", "td"])
                    if len(cells) >= 2:
                        th, td = cells[0], cells[1]
                    else:
                        continue
                key = th.get_text(" ", strip=True)
                val = td.get_text(" ", strip=True)
                if key and val:
                    specs[key] = val
            if specs:
                break
        return specs

    def _images(self, soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        position = 1

        candidates: list[Any] = []
        for sel in _IMAGE_SELECTORS:
            try:
                candidates.extend(soup.select(sel))
            except Exception:
                continue

        # Magento JSON gallery blob
        for script in soup.select('script[type="text/x-magento-init"], script'):
            text = script.string or script.get_text() or ""
            if "mage/gallery" not in text and "data-gallery-role" not in text:
                if '"img"' not in text or "catalog/product" not in text:
                    continue
            for m in re.finditer(
                r'https?://[^"\'\s]+\.(?:jpg|jpeg|png|webp)', text, re.I
            ):
                src = m.group(0)
                if self._skip_image_url(src):
                    continue
                key = src.split("?")[0].lower()
                if key in seen:
                    continue
                seen.add(key)
                img = empty_image(position)
                img["src"] = src
                found.append(img)
                position += 1

        for el in candidates:
            src = (
                el.get("src")
                or el.get("data-src")
                or el.get("data-original")
                or ""
            ).strip()
            if not src and el.get("srcset"):
                src = str(el.get("srcset") or "").split(",")[0].strip().split(" ")[0]
            if not src:
                continue
            full = absolute_url(page_url, src)
            if self._skip_image_url(full, el):
                continue
            key = full.split("?")[0].lower()
            if key in seen:
                continue
            seen.add(key)
            img = empty_image(position)
            img["src"] = full
            img["alt"] = (el.get("alt") or "").strip()
            found.append(img)
            position += 1

        return found

    @staticmethod
    def _skip_image_url(src: str, el: Any = None) -> bool:
        low = (src or "").lower()
        if not low.startswith(("http://", "https://", "//")):
            return True
        if any(
            tok in low
            for tok in (
                "thumbnail",
                "cache/small",
                "/small_image/",
                "placeholder",
                "spacer.gif",
                "data:image",
            )
        ):
            return True
        if el is not None:
            try:
                w = int(el.get("width") or 0)
                if 0 < w < 200:
                    return True
            except Exception:
                pass
        # Magento swatch / tiny icons
        path = urlparse(src).path.lower()
        if "/swatch/" in path or "/icon/" in path:
            return True
        return False
