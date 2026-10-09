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
from sentivo_extractor.core.utils import (
    absolute_url,
    format_cut_to_size_price,
    normalize_price,
    sanitize_sku,
)
from sentivo_extractor.extractors.base import BaseExtractor

logger = logging.getLogger(__name__)

CUT_TO_SIZE_TAG = "cut-to-size-pricing"

_SKU_BODY_RE = re.compile(r"\(SKU:\s*([^\)]+)\)", re.I)
# Alpine/Hyva scalar only — must NOT match Magento spConfig
# `"finalPrice": {"amount": 12.5}` (optionPrices).
_ALPINE_SCALAR_FINAL_PRICE_RE = re.compile(
    r"""\bfinalPrice\s*:\s*['"]?£?\s*(\d+(?:\.\d+)?)""",
    re.I,
)
_PRICE_BOX_FINAL_AMOUNT_PATTERNS = (
    re.compile(
        r"""data-price-type=['"]finalPrice['"][^>]*data-price-amount=['"](\d+(?:\.\d+)?)""",
        re.I,
    ),
    re.compile(
        r"""data-price-amount=['"](\d+(?:\.\d+)?)[^>]*data-price-type=['"]finalPrice['"]""",
        re.I,
    ),
)

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
    # Prefer real product copy — never review markup first.
    ".product-info-main .product.attribute.description .value",
    ".product.attribute.description .value",
    ".product.attribute.overview .value",
    "#description .product.attribute.description .value",
    '[data-content-type="text"]',
    ".product-description",
    "#description",
    ".product-info-main .description",
)
_REVIEW_DESC_RE = re.compile(
    r"(?i)\b(review by|customer review|was this review|stars? out of|"
    r"i bought this|used this for|great service|highly recommend|"
    r"delivery was|arrived (quickly|fast)|would (order|buy) again)\b"
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

        sku = self._sku(soup, html, logger=log)
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

        price, cut_to_size = self._price(soup, html)
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
        product["tags"] = [CUT_TO_SIZE_TAG] if cut_to_size else []
        if cut_to_size or "£" in (price or ""):
            product["currency"] = "GBP"

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

    def _sku(
        self,
        soup: BeautifulSoup,
        html: str,
        *,
        logger: logging.Logger | None = None,
    ) -> str:
        # Prefer explicit "(SKU: CODE)" in page body (sheetplastics / Hyva).
        body_text = soup.get_text(" ", strip=True)
        m = _SKU_BODY_RE.search(body_text or "")
        if not m:
            m = _SKU_BODY_RE.search(html or "")
        if m:
            sku = sanitize_sku(m.group(1).strip(), logger=logger)
            if sku:
                return sku

        return sanitize_sku(self._first_text(soup, _SKU_SELECTORS), logger=logger)

    def _description(self, soup: BeautifulSoup) -> tuple[str, str]:
        for sel in _DESC_SELECTORS:
            try:
                els = soup.select(sel)
            except Exception:
                continue
            for el in els:
                if el is None:
                    continue
                # Skip review widgets / review itemprop blocks.
                if self._looks_like_review_node(el):
                    continue
                html = (
                    el.decode_contents().strip()
                    if hasattr(el, "decode_contents")
                    else ""
                )
                text = el.get_text(" ", strip=True)
                if not text or self._looks_like_review_text(text):
                    continue
                if html or text:
                    return html or f"<p>{text}</p>", text
        return "", ""

    @staticmethod
    def _looks_like_review_text(text: str) -> bool:
        return bool(_REVIEW_DESC_RE.search(text or ""))

    @staticmethod
    def _looks_like_review_node(el: Any) -> bool:
        try:
            classes = " ".join(el.get("class") or []).lower()
        except Exception:
            classes = ""
        parent_cls = ""
        try:
            parent = el.parent
            for _ in range(4):
                if parent is None:
                    break
                parent_cls += " " + " ".join(parent.get("class") or []).lower()
                parent_cls += " " + str(parent.get("id") or "").lower()
                parent = parent.parent
        except Exception:
            pass
        blob = f"{classes} {parent_cls}"
        if any(tok in blob for tok in ("review", "rating", "testimonial")):
            return True
        # Sheetplastics: review snippets misuse itemprop=description.
        try:
            if (el.get("itemprop") or "").lower() == "description":
                if MagentoProductExtractor._looks_like_review_text(
                    el.get_text(" ", strip=True)
                ):
                    return True
                # Bare itemprop=description under reviews section → skip.
                if "review" in blob:
                    return True
        except Exception:
            pass
        return False

    def _alpine_final_price(self, soup: BeautifulSoup, html: str) -> str:
        """Scalar Alpine/Hyva finalPrice only (not Magento optionPrices objects)."""

        def _scalar_from(text: str) -> str:
            for m in _ALPINE_SCALAR_FINAL_PRICE_RE.finditer(text or ""):
                # Reject Magento JSON objects: finalPrice: { "amount": ... }
                tail = (text or "")[m.end() : m.end() + 24].lstrip()
                if tail.startswith("{"):
                    continue
                window = (text or "")[m.start() : m.start() + 48]
                if re.search(r"finalPrice\s*:\s*\{", window, re.I):
                    continue
                amount = normalize_price(m.group(1))
                if amount:
                    return amount
            return ""

        for el in soup.select("[x-data]"):
            amount = _scalar_from(str(el.get("x-data") or ""))
            if amount:
                return amount

        amount = _scalar_from(html or "")
        if amount:
            return amount

        for pat in _PRICE_BOX_FINAL_AMOUNT_PATTERNS:
            m = pat.search(html or "")
            if m:
                got = normalize_price(m.group(1))
                if got:
                    return got
        return ""

    def _price(self, soup: BeautifulSoup, html: str = "") -> tuple[str, bool]:
        """
        Magento price:
        1) Alpine / Hyva scalar finalPrice
        2) .price-wrapper .price (and related DOM)
        Cut-to-size PDPs store price as '£X.XX per cm²'.
        Returns (price, is_cut_to_size).
        """
        blob = html or str(soup)
        cut_hint = bool(
            re.search(r"cut[\s-]?to[\s-]?size|per\s*cm", blob, re.I)
        )

        alpine = self._alpine_final_price(soup, blob)
        if alpine:
            # Alpine scalar finalPrice on cut-to-size storefronts is per cm².
            return format_cut_to_size_price(alpine), True

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
            amount = normalize_price(raw)
            if not amount:
                continue
            if cut_hint:
                return format_cut_to_size_price(amount), True
            return amount, False

        for el in soup.select("[data-price-amount]"):
            raw = el.get("data-price-amount") or ""
            amount = normalize_price(str(raw))
            if not amount:
                continue
            if cut_hint:
                return format_cut_to_size_price(amount), True
            return amount, False
        return "", cut_hint

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
