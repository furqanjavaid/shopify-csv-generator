"""WooCommerce extractor (Store API + HTML fallbacks)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sentivo_extractor.core.schema import empty_product
from sentivo_extractor.core.utils import absolute_url, normalize_price
from sentivo_extractor.extractors.base import BaseExtractor
from sentivo_extractor.extractors.html_extractor import HtmlExtractor
from sentivo_extractor.extractors.jsonld_extractor import JsonLdExtractor


class WooCommerceExtractor(BaseExtractor):
    name = "woocommerce"
    platforms = ("WooCommerce",)

    def extract(
        self,
        url: str,
        html: str = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        ctx = context or {}
        client = ctx.get("http")
        if client is not None:
            api_product = self._try_store_api(url, client)
            if api_product:
                return api_product

        # Prefer JSON-LD then HTML
        for extractor in (JsonLdExtractor(), HtmlExtractor()):
            result = extractor.extract(url, html, context=context)
            if result and result.get("title"):
                result["extraction_method"] = f"woocommerce+{extractor.name}"
                result["confidence_score"] = max(
                    float(result.get("confidence_score") or 0), 0.7
                )
                return result
        return None

    def _try_store_api(self, url: str, client) -> dict[str, Any] | None:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        slug = parsed.path.rstrip("/").split("/")[-1]
        if not slug:
            return None
        endpoints = [
            f"{base}/wp-json/wc/store/v1/products?slug={slug}",
            f"{base}/wp-json/wc/store/products?slug={slug}",
        ]
        for endpoint in endpoints:
            try:
                data = client.get_json(endpoint)
                if isinstance(data, list) and data:
                    return self._map_store_product(data[0], url)
                if isinstance(data, dict) and data.get("name"):
                    return self._map_store_product(data, url)
            except Exception:
                continue
        return None

    def _map_store_product(self, data: dict[str, Any], url: str) -> dict[str, Any]:
        product = empty_product()
        product["source_url"] = url
        product["title"] = str(data.get("name") or "")
        product["description_html"] = str(
            data.get("description") or data.get("short_description") or ""
        )
        product["extraction_method"] = "woocommerce_store_api"
        product["confidence_score"] = 0.9
        prices = data.get("prices") or {}
        if not isinstance(prices, dict):
            prices = {}
        price = normalize_price(
            prices.get("price")
            or prices.get("sale_price")
            or data.get("price")
            or ""
        )
        # Store API prices often in minor units
        if price and prices.get("currency_minor_unit") is not None:
            try:
                minor = int(prices.get("currency_minor_unit") or 0)
                if minor and "." not in price and len(price) > minor:
                    price = f"{int(price) / (10 ** minor):.2f}"
            except Exception:
                pass
        compare = normalize_price(prices.get("regular_price") or "")
        if compare == price:
            compare = ""
        product["currency"] = str(prices.get("currency_code") or "")

        images = []
        for i, img in enumerate(data.get("images") or [], start=1):
            src = img.get("src") if isinstance(img, dict) else str(img)
            if src:
                images.append(
                    {
                        "src": absolute_url(url, src),
                        "alt": (img.get("alt") if isinstance(img, dict) else "") or "",
                        "position": i,
                    }
                )
        product["images"] = images
        product["options"] = [{"name": "Title", "values": ["Default Title"]}]
        product["variants"] = [
            {
                "sku": str(data.get("sku") or ""),
                "barcode": "",
                "option1": "Default Title",
                "option2": "",
                "option3": "",
                "price": price,
                "compare_at_price": compare,
                "inventory_qty": "",
                "available": bool(data.get("is_in_stock", True)),
                "weight_grams": "",
                "variant_image": "",
            }
        ]
        return product
