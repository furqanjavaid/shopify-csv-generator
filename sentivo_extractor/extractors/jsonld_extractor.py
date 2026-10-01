"""JSON-LD Product schema extractor."""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from sentivo_extractor.core.schema import empty_product
from sentivo_extractor.core.utils import absolute_url, detect_currency, normalize_price
from sentivo_extractor.extractors.base import BaseExtractor


class JsonLdExtractor(BaseExtractor):
    name = "jsonld"
    platforms = ("Custom", "WooCommerce", "Magento", "Next.js", "Nuxt", "React", "Shopify")

    def extract(
        self,
        url: str,
        html: str = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not html:
            return None
        blocks = self._parse_jsonld(html)
        product_node = self._find_product(blocks)
        if not product_node:
            return None
        return self._map(product_node, url)

    def _parse_jsonld(self, html: str) -> list[Any]:
        soup = BeautifulSoup(html, "lxml")
        out: list[Any] = []
        for tag in soup.select('script[type="application/ld+json"]'):
            text = tag.string or tag.get_text() or ""
            text = text.strip()
            if not text:
                continue
            try:
                out.append(json.loads(text))
            except json.JSONDecodeError:
                # Sometimes trailing commas
                try:
                    cleaned = re.sub(r",\s*}", "}", text)
                    cleaned = re.sub(r",\s*]", "]", cleaned)
                    out.append(json.loads(cleaned))
                except Exception:
                    continue
        return out

    def _find_product(self, blocks: list[Any]) -> dict[str, Any] | None:
        def walk(node: Any) -> dict[str, Any] | None:
            if isinstance(node, dict):
                t = node.get("@type")
                types = t if isinstance(t, list) else [t]
                types = [str(x).lower() for x in types if x]
                if "product" in types:
                    return node
                if node.get("@graph"):
                    found = walk(node["@graph"])
                    if found:
                        return found
                for v in node.values():
                    found = walk(v)
                    if found:
                        return found
            elif isinstance(node, list):
                for item in node:
                    found = walk(item)
                    if found:
                        return found
            return None

        for block in blocks:
            found = walk(block)
            if found:
                return found
        return None

    def _map(self, node: dict[str, Any], url: str) -> dict[str, Any]:
        product = empty_product()
        product["source_url"] = url
        product["title"] = str(node.get("name") or "")
        product["description_html"] = str(node.get("description") or "")
        brand = node.get("brand")
        if isinstance(brand, dict):
            product["brand"] = str(brand.get("name") or "")
        else:
            product["brand"] = str(brand or "")
        product["vendor"] = product["brand"]
        product["extraction_method"] = "jsonld"
        product["confidence_score"] = 0.85

        images: list[dict[str, Any]] = []
        img = node.get("image")
        img_list = img if isinstance(img, list) else [img] if img else []
        for i, item in enumerate(img_list, start=1):
            src = item.get("url") if isinstance(item, dict) else str(item)
            if src:
                images.append({"src": absolute_url(url, src), "alt": "", "position": i})
        product["images"] = images

        offers = node.get("offers")
        offer_list: list[dict[str, Any]] = []
        if isinstance(offers, dict):
            if offers.get("@type") == "AggregateOffer" or "lowPrice" in offers:
                offer_list = [offers]
            else:
                offer_list = [offers]
        elif isinstance(offers, list):
            offer_list = [o for o in offers if isinstance(o, dict)]

        variants = []
        currency = ""
        for off in offer_list:
            price = normalize_price(off.get("price") or off.get("lowPrice"))
            currency = currency or str(off.get("priceCurrency") or "") or detect_currency(
                str(off.get("price") or "")
            )
            sku = str(off.get("sku") or node.get("sku") or "")
            variants.append(
                {
                    "sku": sku,
                    "barcode": str(node.get("gtin13") or node.get("gtin") or ""),
                    "option1": "Default Title",
                    "option2": "",
                    "option3": "",
                    "price": price,
                    "compare_at_price": "",
                    "inventory_qty": "",
                    "available": "InStock" in str(off.get("availability") or "InStock"),
                    "weight_grams": "",
                    "variant_image": "",
                }
            )
        if not variants:
            variants.append(
                {
                    "sku": str(node.get("sku") or ""),
                    "barcode": "",
                    "option1": "Default Title",
                    "option2": "",
                    "option3": "",
                    "price": "",
                    "compare_at_price": "",
                    "inventory_qty": "",
                    "available": True,
                    "weight_grams": "",
                    "variant_image": "",
                }
            )
        product["variants"] = variants
        product["options"] = [{"name": "Title", "values": ["Default Title"]}]
        product["currency"] = currency
        return product
