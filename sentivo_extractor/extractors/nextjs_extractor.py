"""Next.js / Nuxt / React hydration JSON extractor."""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from sentivo_extractor.core.schema import empty_product
from sentivo_extractor.core.utils import absolute_url, normalize_price
from sentivo_extractor.extractors.base import BaseExtractor


class NextJsExtractor(BaseExtractor):
    name = "nextjs"
    platforms = ("Next.js", "Nuxt", "React", "Custom")

    def extract(
        self,
        url: str,
        html: str = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not html:
            return None
        data = self._load_next_data(html) or self._load_nuxt_data(html)
        if not data:
            return None
        product_node = self._find_product_like(data)
        if not product_node:
            return None
        return self._map(product_node, url)

    def _load_next_data(self, html: str) -> Any | None:
        soup = BeautifulSoup(html, "lxml")
        tag = soup.select_one("script#__NEXT_DATA__")
        if not tag:
            return None
        try:
            return json.loads(tag.string or tag.get_text() or "")
        except Exception:
            return None

    def _load_nuxt_data(self, html: str) -> Any | None:
        m = re.search(
            r"window\.__NUXT__\s*=\s*(\{.*?\});\s*</script>",
            html,
            re.S,
        )
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except Exception:
            return None

    def _find_product_like(self, data: Any) -> dict[str, Any] | None:
        """Heuristically find a product object in hydration payload."""
        candidates: list[dict[str, Any]] = []

        def walk(node: Any, depth: int = 0) -> None:
            if depth > 12:
                return
            if isinstance(node, dict):
                keys = {k.lower() for k in node}
                if ("title" in keys or "name" in keys) and (
                    "price" in keys
                    or "variants" in keys
                    or "images" in keys
                    or "image" in keys
                ):
                    candidates.append(node)
                for v in node.values():
                    walk(v, depth + 1)
            elif isinstance(node, list):
                for item in node[:50]:
                    walk(item, depth + 1)

        walk(data)
        if not candidates:
            return None
        # Prefer objects with variants
        candidates.sort(
            key=lambda c: (
                1 if c.get("variants") else 0,
                1 if c.get("images") or c.get("image") else 0,
            ),
            reverse=True,
        )
        return candidates[0]

    def _map(self, node: dict[str, Any], url: str) -> dict[str, Any]:
        product = empty_product()
        product["source_url"] = url
        product["title"] = str(node.get("title") or node.get("name") or "")
        product["description_html"] = str(
            node.get("description") or node.get("body_html") or node.get("body") or ""
        )
        product["vendor"] = str(
            (node.get("vendor") or "")
            if not isinstance(node.get("vendor"), dict)
            else (node.get("vendor") or {}).get("name") or ""
        )
        brand = node.get("brand")
        if isinstance(brand, dict):
            product["brand"] = str(brand.get("name") or brand.get("title") or "")
        elif brand:
            product["brand"] = str(brand)
        else:
            product["brand"] = product["vendor"]
        if not product["vendor"]:
            product["vendor"] = product["brand"]
        product["handle"] = str(node.get("handle") or node.get("slug") or "")
        product["extraction_method"] = "nextjs_hydration"
        product["confidence_score"] = 0.8

        images = []
        raw_images = node.get("images") or []
        if not raw_images and node.get("image"):
            raw_images = [node.get("image")]
        for i, img in enumerate(raw_images, start=1):
            if isinstance(img, dict):
                src = img.get("src") or img.get("url") or img.get("path") or ""
            else:
                src = str(img)
            if src:
                images.append({"src": absolute_url(url, src), "alt": "", "position": i})
        product["images"] = images

        options = []
        for opt in (node.get("options") or [])[:3]:
            if isinstance(opt, dict):
                options.append(
                    {
                        "name": opt.get("name") or "Option",
                        "values": list(opt.get("values") or []),
                    }
                )
        product["options"] = options

        variants = []
        for v in node.get("variants") or []:
            if not isinstance(v, dict):
                continue
            variants.append(
                {
                    "sku": str(v.get("sku") or ""),
                    "barcode": str(v.get("barcode") or ""),
                    "option1": str(v.get("option1") or v.get("title") or "Default Title"),
                    "option2": str(v.get("option2") or ""),
                    "option3": str(v.get("option3") or ""),
                    "price": normalize_price(v.get("price")),
                    "compare_at_price": normalize_price(v.get("compare_at_price")),
                    "inventory_qty": str(v.get("inventory_quantity") or ""),
                    "available": bool(v.get("available", True)),
                    "weight_grams": str(v.get("grams") or ""),
                    "variant_image": "",
                }
            )
        if not variants:
            product["price"] = normalize_price(node.get("price"))
            product["compare_at_price"] = normalize_price(node.get("compare_at_price"))
        product["variants"] = variants
        if not product["title"]:
            return None
        return product
