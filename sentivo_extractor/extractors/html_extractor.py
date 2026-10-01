"""Generic HTML product extractor using selectors + optional site YAML rules."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from sentivo_extractor.core.schema import empty_product
from sentivo_extractor.core.utils import (
    absolute_url,
    canonicalize_option_name,
    detect_currency,
    is_blank_price,
    normalize_price,
    sanitize_sku,
)
from sentivo_extractor.extractors.base import BaseExtractor


class HtmlExtractor(BaseExtractor):
    name = "html"
    platforms = ("Custom", "WooCommerce", "Magento", "Next.js", "Nuxt", "React")

    def extract(
        self,
        url: str,
        html: str = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not html:
            return None
        rules = (context or {}).get("site_rules") or {}
        if not isinstance(rules, dict):
            rules = {}
        soup = BeautifulSoup(html, "lxml")
        product = empty_product()
        product["source_url"] = url
        product["extraction_method"] = "html"
        product["confidence_score"] = 0.55

        title = self._select_text(soup, rules.get("title") or ["h1"])
        if not title:
            return None
        product["title"] = title

        desc_html = self._select_html(
            soup,
            rules.get("description")
            or [
                '[class*="product-description"]',
                '[itemprop="description"]',
                '[class*="description"]',
            ],
        )
        product["description_html"] = desc_html

        price_text = self._select_text(
            soup,
            rules.get("price")
            or ['[itemprop="price"]', ".price", '[class*="price"]'],
        )
        price = normalize_price(price_text)
        if is_blank_price(price):
            price = ""
        product["currency"] = detect_currency(price_text)

        compare_text = self._select_text(
            soup,
            rules.get("compare_at_price")
            or ["del", "s", '[class*="compare"]', "[class*='was']"],
        )
        compare = normalize_price(compare_text)
        if is_blank_price(compare):
            compare = ""

        sku = sanitize_sku(
            self._select_text(
                soup, rules.get("sku") or ['[itemprop="sku"]', '[class*="sku"]']
            )
        )
        vendor = self._select_text(
            soup,
            rules.get("vendor")
            or ['[itemprop="brand"]', '[class*="brand"]', '[class*="vendor"]'],
        )
        product["vendor"] = vendor
        product["brand"] = vendor

        images = self._extract_images(soup, url, rules.get("images"))
        product["images"] = images

        options = self._extract_options(soup, rules.get("options"))
        product["options"] = options

        if options:
            # leave variants to normalizer expansion; seed price on a single base
            product["price"] = price
            product["compare_at_price"] = compare
            product["variants"] = []
        else:
            product["options"] = [{"name": "Title", "values": ["Default Title"]}]
            product["variants"] = [
                {
                    "sku": sku,
                    "barcode": "",
                    "option1": "Default Title",
                    "option2": "",
                    "option3": "",
                    "price": price,
                    "compare_at_price": compare,
                    "inventory_qty": "",
                    "available": True,
                    "weight_grams": "",
                    "variant_image": "",
                }
            ]
            # stash for normalizer if variants empty expansion needed
            product["price"] = price
            product["compare_at_price"] = compare

        return product

    def _select_text(self, soup: BeautifulSoup, selectors: list[str]) -> str:
        for sel in selectors:
            try:
                el = soup.select_one(sel)
            except Exception:
                continue
            if not el:
                continue
            # prefer content attr for itemprop price
            content = el.get("content")
            if content:
                return str(content).strip()
            text = el.get_text(" ", strip=True)
            if text:
                return text
        return ""

    def _select_html(self, soup: BeautifulSoup, selectors: list[str]) -> str:
        for sel in selectors:
            try:
                el = soup.select_one(sel)
            except Exception:
                continue
            if el:
                return str(el.decode_contents() if hasattr(el, "decode_contents") else el)
        return ""

    def _extract_images(
        self, soup: BeautifulSoup, base_url: str, selectors: list[str] | None
    ) -> list[dict[str, Any]]:
        sels = selectors or [
            ".product-image img",
            '[class*="product"] img',
            'img[itemprop="image"]',
            "main img",
        ]
        images: list[dict[str, Any]] = []
        seen: set[str] = set()
        for sel in sels:
            try:
                nodes = soup.select(sel)
            except Exception:
                continue
            for img in nodes:
                src = (
                    img.get("src")
                    or img.get("data-src")
                    or img.get("data-lazy-src")
                    or ""
                )
                if not src and img.get("srcset"):
                    src = (img.get("srcset") or "").split(",")[0].strip().split(" ")[0]
                src = absolute_url(base_url, src)
                if not src or src.startswith("data:") or src in seen:
                    continue
                seen.add(src)
                images.append(
                    {
                        "src": src,
                        "alt": img.get("alt") or "",
                        "position": len(images) + 1,
                    }
                )
            if images:
                break
        return images[:20]

    def _extract_options(
        self, soup: BeautifulSoup, selectors: list[str] | None
    ) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = []
        for sel in soup.select(
            "form select, .variations select, select[name*='attribute'], select[name*='option']"
        ):
            if len(options) >= 3:
                break
            name = sel.get("name") or sel.get("aria-label") or "Option"
            name = re.sub(r"attribute_pa_|attribute_|option_?", "", name, flags=re.I)
            name = canonicalize_option_name(name.replace("-", " ").replace("_", " "))
            values: list[str] = []
            for opt in sel.select("option"):
                val = (opt.get_text(" ", strip=True) or opt.get("value") or "").strip()
                val = re.sub(r"[£$€]\s*\d[\d,]*(?:\.\d+)?", "", val).strip()
                if not val or val.lower().startswith("choose") or val.lower() == "select":
                    continue
                if val not in values:
                    values.append(val)
            if values:
                options.append({"name": name or "Option", "values": values})
        return options
