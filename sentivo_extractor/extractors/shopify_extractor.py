"""Shopify product extractor via /products/{handle}.js and products.json."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from sentivo_extractor.core.schema import empty_product
from sentivo_extractor.core.utils import absolute_url, normalize_price
from sentivo_extractor.extractors.base import BaseExtractor


class ShopifyExtractor(BaseExtractor):
    name = "shopify"
    platforms = ("Shopify",)

    def extract(
        self,
        url: str,
        html: str = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        ctx = context or {}
        client = ctx.get("http")
        data = ctx.get("shopify_json")
        if data is None and client is not None:
            data = self._fetch_product_js(url, client)
        if data is None and html:
            data = self._from_html_bootstrap(html)
        if not isinstance(data, dict):
            return None
        return self._map_product(data, url)

    def _fetch_product_js(self, url: str, client) -> dict[str, Any] | None:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        # /products/handle → /products/handle.js
        m = re.search(r"/products/([^/?#]+)", path, re.I)
        if not m:
            return None
        handle = m.group(1)
        base = f"{parsed.scheme}://{parsed.netloc}"
        try:
            return client.get_json(f"{base}/products/{handle}.js")
        except Exception:
            try:
                payload = client.get_json(f"{base}/products.json?limit=250")
                for p in payload.get("products") or []:
                    if p.get("handle") == handle:
                        return p
            except Exception:
                return None
        return None

    def _from_html_bootstrap(self, html: str) -> dict[str, Any] | None:
        # Minimal fallback — real pages usually need .js endpoint
        return None

    def _map_product(self, data: dict[str, Any], url: str) -> dict[str, Any]:
        product = empty_product()
        product["source_url"] = url
        product["title"] = str(data.get("title") or "")
        product["handle"] = str(data.get("handle") or "")
        product["description_html"] = str(
            data.get("body_html") or data.get("description") or ""
        )
        product["vendor"] = str(data.get("vendor") or "")
        product["brand"] = product["vendor"]
        product["product_type"] = str(data.get("product_type") or data.get("type") or "")
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        product["tags"] = list(tags)
        product["extraction_method"] = "shopify"
        product["confidence_score"] = 0.95

        options = []
        for opt in data.get("options") or []:
            name = opt.get("name") if isinstance(opt, dict) else str(opt)
            values = opt.get("values") if isinstance(opt, dict) else []
            options.append({"name": name, "values": list(values or [])})
        product["options"] = options[:3]

        images = []
        for i, img in enumerate(data.get("images") or [], start=1):
            if isinstance(img, dict):
                src = img.get("src") or img.get("url") or ""
                alt = img.get("alt") or ""
            else:
                src = str(img)
                alt = ""
            if src:
                images.append({"src": absolute_url(url, src), "alt": alt, "position": i})
        # Shopify .js sometimes uses featured_image
        if not images and data.get("featured_image"):
            fi = data["featured_image"]
            src = fi.get("src") if isinstance(fi, dict) else str(fi)
            images.append({"src": absolute_url(url, src), "alt": "", "position": 1})
        product["images"] = images

        variants = []
        for v in data.get("variants") or []:
            if not isinstance(v, dict):
                continue
            variants.append(
                {
                    "sku": str(v.get("sku") or ""),
                    "barcode": str(v.get("barcode") or ""),
                    "option1": str(v.get("option1") or ""),
                    "option2": str(v.get("option2") or ""),
                    "option3": str(v.get("option3") or ""),
                    "price": normalize_price(v.get("price")),
                    "compare_at_price": normalize_price(
                        v.get("compare_at_price") or v.get("compare_at_price")
                    ),
                    "inventory_qty": str(
                        v.get("inventory_quantity")
                        if v.get("inventory_quantity") is not None
                        else ""
                    ),
                    "available": bool(v.get("available", True)),
                    "weight_grams": str(v.get("grams") or v.get("weight") or ""),
                    "variant_image": absolute_url(
                        url,
                        (v.get("featured_image") or {}).get("src")
                        if isinstance(v.get("featured_image"), dict)
                        else str(v.get("image_id") or ""),
                    )
                    if v.get("featured_image")
                    else "",
                }
            )
        product["variants"] = variants
        return product
