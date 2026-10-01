"""Magento extractor — embedded JSON config + HTML option fallbacks."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from sentivo_extractor.core.schema import empty_product, empty_variant
from sentivo_extractor.core.utils import (
    absolute_url,
    canonicalize_option_name,
    is_blank_price,
    normalize_price,
    sanitize_sku,
)
from sentivo_extractor.extractors.base import BaseExtractor
from sentivo_extractor.extractors.html_extractor import HtmlExtractor
from sentivo_extractor.extractors.jsonld_extractor import JsonLdExtractor

logger = logging.getLogger(__name__)

_SIZE_CELL_RE = re.compile(
    r"\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?(?:\s*[x×]\s*\d+(?:\.\d+)?)?\s*(?:mm|cm|m)?",
    re.I,
)

_SELECT_NAME_HINT = re.compile(
    r"(size|dimension|diameter|length|width|thickness|option|super_attribute)",
    re.I,
)


def _balanced_json_object(text: str, start: int) -> str | None:
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


class MagentoExtractor(BaseExtractor):
    name = "magento"
    platforms = ("Magento",)

    def extract(
        self,
        url: str,
        html: str = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Variants only from the current page's configurable/grouped data.
        Never invent variants by merging sibling simple PDPs / title sizes.
        """
        log = (context or {}).get("logger") or logger
        if html:
            for label, fn in (
                ("Magento Config", self._from_embedded_config),
                ("Magento selects", self._from_selects),
                ("Magento table", self._from_dimension_tables),
            ):
                result = fn(html, url)
                if not result or not result.get("title"):
                    continue
                n = len(
                    [v for v in (result.get("variants") or []) if isinstance(v, dict)]
                )
                if n:
                    log.info("Variants found via %s: %s variants", label, n)
                    result["variant_engine_source"] = label
                    return result
                if label == "Magento Config" and result.get("options"):
                    expanded = self._expand_options_to_variants(result)
                    n2 = len(expanded.get("variants") or [])
                    if n2:
                        log.info("Variants found via %s: %s variants", label, n2)
                        expanded["variant_engine_source"] = label
                        return expanded

        for extractor in (JsonLdExtractor(), HtmlExtractor()):
            result = extractor.extract(url, html, context=context)
            if result and result.get("title"):
                result["extraction_method"] = f"magento+{extractor.name}"
                result["platform"] = result.get("platform") or "Magento"
                return result
        return None

    def _from_embedded_config(self, html: str, url: str) -> dict[str, Any] | None:
        data, source_key = self._find_config_object(html)
        if not data:
            return None

        product = empty_product()
        product["source_url"] = url
        product["extraction_method"] = f"magento_{source_key or 'jsonconfig'}"
        product["confidence_score"] = 0.75
        product["platform"] = "Magento"

        title = str(data.get("product_name") or data.get("name") or "")
        if not title:
            try:
                soup = BeautifulSoup(html or "", "lxml")
                h1 = soup.select_one("h1 span, h1 .base, h1")
                title = h1.get_text(" ", strip=True) if h1 else ""
            except Exception:
                title = ""
        product["title"] = title

        attributes = data.get("attributes") or {}
        if not isinstance(attributes, dict):
            attributes = {}
        attr_items = [
            a for a in attributes.values() if isinstance(a, dict)
        ]
        attr_items.sort(key=lambda a: int(a.get("position") or 0))
        attr_items = attr_items[:3]

        options: list[dict[str, Any]] = []
        attr_maps: list[tuple[str, dict[str, str]]] = []
        for attr in attr_items:
            name = canonicalize_option_name(
                str(attr.get("label") or attr.get("code") or "Option")
            )
            id_to_label: dict[str, str] = {}
            values: list[str] = []
            for opt in attr.get("options") or []:
                if not isinstance(opt, dict):
                    continue
                oid = str(opt.get("id") or "")
                label = str(opt.get("label") or "").strip()
                if not label:
                    continue
                id_to_label[oid] = label
                if label not in values:
                    values.append(label)
            if values:
                options.append({"name": name or "Option", "values": values})
                attr_maps.append((str(attr.get("id") or ""), id_to_label))
        product["options"] = options

        images = self._images_from_config(data, url)
        product["images"] = images

        index = data.get("index") or {}
        if not isinstance(index, dict):
            index = {}
        skus = data.get("sku") if isinstance(data.get("sku"), dict) else {}
        prices = data.get("optionPrices") if isinstance(data.get("optionPrices"), dict) else {}

        variants: list[dict[str, Any]] = []
        for pid, opt_map in index.items():
            if not isinstance(opt_map, dict):
                continue
            option_vals: list[str] = []
            for attr_id, id_to_label in attr_maps:
                raw_oid = opt_map.get(attr_id)
                if raw_oid is None:
                    raw_oid = opt_map.get(str(attr_id))
                label = id_to_label.get(str(raw_oid), "") if raw_oid is not None else ""
                option_vals.append(label)
            while len(option_vals) < 3:
                option_vals.append("")
            price = self._price_from_option_node(
                prices.get(str(pid))
                or prices.get(pid)
                or (prices.get(int(pid)) if str(pid).isdigit() else None)
            )
            sku = ""
            if isinstance(skus, dict):
                sku = sanitize_sku(
                    skus.get(str(pid)) or skus.get(pid) or ""
                )
            v = empty_variant()
            v["option1"] = option_vals[0] or "Default Title"
            v["option2"] = option_vals[1]
            v["option3"] = option_vals[2]
            v["price"] = price
            v["sku"] = sku
            v["available"] = True
            variants.append(v)

        product["variants"] = variants
        if variants and not is_blank_price(variants[0].get("price")):
            product["price"] = variants[0]["price"]
        else:
            base_price = self._price_from_option_node(data.get("prices"))
            product["price"] = base_price

        if not product["title"]:
            return None
        return product

    def _expand_options_to_variants(self, product: dict[str, Any]) -> dict[str, Any]:
        options = [o for o in (product.get("options") or []) if isinstance(o, dict)]
        if not options:
            return product
        values = list(options[0].get("values") or [])
        if not values:
            return product
        base_price = "" if is_blank_price(product.get("price")) else normalize_price(
            product.get("price")
        )
        variants = []
        for val in values:
            v = empty_variant()
            v["option1"] = str(val)
            v["price"] = base_price
            variants.append(v)
        product = dict(product)
        product["variants"] = variants
        return product

    def _from_selects(self, html: str, url: str) -> dict[str, Any] | None:
        try:
            soup = BeautifulSoup(html or "", "lxml")
        except Exception:
            return None
        title = self._page_title(soup)
        if not title:
            return None

        options: list[dict[str, Any]] = []
        for sel in soup.select(
            "select[name*='super_attribute'], select[id*='attribute'], "
            "select[name*='options'], form#product_addtocart_form select, "
            ".product-options-wrapper select, #product-options-wrapper select"
        ):
            if len(options) >= 3:
                break
            name_raw = (
                sel.get("name")
                or sel.get("id")
                or sel.get("aria-label")
                or ""
            )
            label_el = sel.find_previous("label")
            label = label_el.get_text(" ", strip=True) if label_el else ""
            name = canonicalize_option_name(
                label
                or re.sub(
                    r"super_attribute\[\d+\]|attribute_?",
                    "",
                    name_raw,
                    flags=re.I,
                ).replace("_", " ")
                or "Option"
            )
            if name_raw and not _SELECT_NAME_HINT.search(name_raw + " " + name):
                # Still allow plainly labeled size/dimension selects
                if not _SELECT_NAME_HINT.search(name):
                    continue
            values: list[str] = []
            for opt in sel.select("option"):
                val = (opt.get_text(" ", strip=True) or "").strip()
                val = re.sub(r"[£$€]\s*\d[\d,]*(?:\.\d+)?", "", val).strip()
                if not val or val.lower().startswith(("choose", "select", "--")):
                    continue
                if val not in values:
                    values.append(val)
            if values:
                options.append({"name": name or "Size", "values": values})

        if not options:
            return None

        product = empty_product()
        product["source_url"] = url
        product["title"] = title
        product["platform"] = "Magento"
        product["extraction_method"] = "magento_selects"
        product["options"] = options
        product["images"] = self._page_images(soup, url)
        product = self._expand_options_to_variants(product)
        return product

    def _from_dimension_tables(self, html: str, url: str) -> dict[str, Any] | None:
        try:
            soup = BeautifulSoup(html or "", "lxml")
        except Exception:
            return None
        title = self._page_title(soup)
        if not title:
            return None

        sizes: list[str] = []
        prices: dict[str, str] = {}
        skus: dict[str, str] = {}

        for table in soup.select(
            ".product-info-main table, #product-options-wrapper table, "
            ".product.data.items table, table.table"
        ):
            headers = [
                c.get_text(" ", strip=True).lower()
                for c in table.select("thead th, tr th")
            ]
            header_blob = " ".join(headers)
            for tr in table.select("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.select("td,th")]
                if len(cells) < 1:
                    continue
                size = ""
                for cell in cells:
                    m = _SIZE_CELL_RE.search(cell)
                    if m:
                        size = re.sub(r"\s+", " ", m.group(0).strip())
                        break
                if not size:
                    continue
                if size not in sizes:
                    sizes.append(size)
                # price in row
                for cell in cells:
                    if re.search(r"[£$€]\s*\d", cell) or re.search(
                        r"\d+\.\d{2}", cell
                    ):
                        p = normalize_price(cell)
                        if not is_blank_price(p):
                            prices[size] = p
                            break
                if "sku" in header_blob:
                    for i, h in enumerate(headers):
                        if "sku" in h and i < len(cells):
                            skus[size] = sanitize_sku(cells[i])

        if len(sizes) < 2:
            return None

        product = empty_product()
        product["source_url"] = url
        product["title"] = title
        product["platform"] = "Magento"
        product["extraction_method"] = "magento_table"
        product["options"] = [{"name": "Size", "values": sizes}]
        product["images"] = self._page_images(soup, url)
        variants = []
        for size in sizes:
            v = empty_variant()
            v["option1"] = size
            v["price"] = prices.get(size, "")
            v["sku"] = skus.get(size, "")
            variants.append(v)
        product["variants"] = variants
        return product

    def _find_config_object(self, html: str) -> tuple[dict[str, Any] | None, str]:
        if not html:
            return None, ""
        for key in ("spConfig", "jsonConfig"):
            for m in re.finditer(rf'["\']?{key}["\']?\s*:\s*\{{', html):
                raw = _balanced_json_object(html, m.end() - 1)
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                if isinstance(data, dict) and (
                    data.get("attributes") or data.get("optionPrices") or data.get("index")
                ):
                    return data, key
        # initConfigurableOptions(productId, {…})
        m = re.search(r"initConfigurableOptions\s*\(\s*[^,]+,\s*\{", html)
        if m:
            raw = _balanced_json_object(html, m.end() - 1)
            if raw:
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        return data, "initConfigurableOptions"
                except Exception:
                    pass
        return None, ""

    @staticmethod
    def _price_from_option_node(node: Any) -> str:
        if node is None:
            return ""
        if isinstance(node, (int, float)):
            return "" if is_blank_price(node) else normalize_price(node)
        if isinstance(node, str):
            return "" if is_blank_price(node) else normalize_price(node)
        if isinstance(node, dict):
            for key in ("finalPrice", "basePrice", "oldPrice", "amount"):
                val = node.get(key)
                if isinstance(val, dict) and "amount" in val:
                    p = normalize_price(val.get("amount"))
                    if not is_blank_price(p):
                        return p
                else:
                    p = normalize_price(val)
                    if not is_blank_price(p):
                        return p
        return ""

    @staticmethod
    def _images_from_config(data: dict[str, Any], url: str) -> list[dict[str, Any]]:
        images: list[dict[str, Any]] = []
        raw_images = data.get("images")
        items: list[Any] = []
        if isinstance(raw_images, list):
            items = raw_images
        elif isinstance(raw_images, dict):
            # Magento often maps productId -> [images]
            for val in raw_images.values():
                if isinstance(val, list):
                    items.extend(val)
                elif isinstance(val, dict):
                    items.append(val)
        for i, img in enumerate(items, start=1):
            if isinstance(img, dict):
                src = img.get("full") or img.get("img") or img.get("src") or ""
            else:
                src = str(img or "")
            if src:
                images.append(
                    {"src": absolute_url(url, str(src)), "alt": "", "position": i}
                )
            if len(images) >= 12:
                break
        return images

    @staticmethod
    def _page_title(soup: BeautifulSoup) -> str:
        h1 = soup.select_one("h1 span.base, h1 .page-title span, h1")
        return h1.get_text(" ", strip=True) if h1 else ""

    @staticmethod
    def _page_images(soup: BeautifulSoup, url: str) -> list[dict[str, Any]]:
        images: list[dict[str, Any]] = []
        seen: set[str] = set()
        for img in soup.select(
            ".gallery-placeholder img, .product.media img, "
            ".fotorama__img, img.gallery-placeholder__image, "
            '[class*="product"] img[src*="catalog/product"]'
        ):
            src = img.get("src") or img.get("data-src") or ""
            src = absolute_url(url, src)
            if not src or src in seen or "/theme/" in src:
                continue
            seen.add(src)
            images.append({"src": src, "alt": img.get("alt") or "", "position": len(images) + 1})
            if len(images) >= 8:
                break
        return images
