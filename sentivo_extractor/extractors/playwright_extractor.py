"""Playwright fallback with network JSON capture and variant probing."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sentivo_extractor.core.schema import empty_product, empty_variant
from sentivo_extractor.core.utils import absolute_url, canonicalize_option_name, normalize_price
from sentivo_extractor.extractors.base import BaseExtractor
from sentivo_extractor.extractors.html_extractor import HtmlExtractor
from sentivo_extractor.extractors.jsonld_extractor import JsonLdExtractor
from sentivo_extractor.extractors.nextjs_extractor import NextJsExtractor


class PlaywrightExtractor(BaseExtractor):
    name = "playwright"
    platforms = ("Custom", "Next.js", "Nuxt", "React", "WooCommerce", "Magento")

    # WooCommerce price selectors (ordered) when API/JSON leave price empty.
    WOO_PRICE_SELECTORS = (
        ".woocommerce-Price-amount bdi",
        ".price ins .woocommerce-Price-amount",
        ".price .woocommerce-Price-amount",
        "[data-product_id] .price",
    )

    def supports(self, platform: str, html: str = "", url: str = "") -> bool:
        return True

    def extract(
        self,
        url: str,
        html: str = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        ctx = context or {}
        if not ctx.get("use_playwright"):
            return None

        # Offline / pre-rendered path (tests)
        if ctx.get("rendered_html") is not None and not ctx.get("force_browser"):
            return self._extract_from_rendered(
                url, ctx["rendered_html"], ctx.get("network_json") or [], ctx
            )

        bundle = self._render_with_capture(url, ctx)
        if not bundle:
            if html:
                return self._extract_from_rendered(url, html, [], ctx)
            return None
        return self._extract_from_rendered(
            url,
            bundle["html"],
            bundle.get("network_json") or [],
            ctx,
            variant_probe=bundle.get("variant_probe"),
        )

    def _extract_from_rendered(
        self,
        url: str,
        rendered: str,
        network_json: list[Any],
        ctx: dict[str, Any],
        variant_probe: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        logger = logging.getLogger(__name__)
        # Prefer structured network JSON
        net_product = self._product_from_network(network_json, url)
        if isinstance(net_product, str):
            net_product = None
        if net_product and isinstance(net_product, dict) and net_product.get("title"):
            net_product["extraction_method"] = "playwright+network_json"
            net_product["confidence_score"] = max(
                float(net_product.get("confidence_score") or 0), 0.88
            )
            if variant_probe:
                net_product = self._merge_variant_probe(net_product, variant_probe)
            return self._ensure_woocommerce_price(
                net_product if isinstance(net_product, dict) else None,
                rendered,
                logger=logger,
            )

        for extractor in (NextJsExtractor(), JsonLdExtractor(), HtmlExtractor()):
            result = extractor.extract(url, rendered, context=ctx)
            if isinstance(result, str):
                continue
            if result and isinstance(result, dict) and result.get("title"):
                result["extraction_method"] = f"playwright+{extractor.name}"
                result["confidence_score"] = max(
                    float(result.get("confidence_score") or 0), 0.7
                )
                if variant_probe:
                    result = self._merge_variant_probe(result, variant_probe)
                elif not result.get("options"):
                    # DOM option scrape from HTML
                    options = self._options_from_html(rendered)
                    if options:
                        result["options"] = options
                        result["variants"] = []
                return self._ensure_woocommerce_price(
                    result if isinstance(result, dict) else None,
                    rendered,
                    logger=logger,
                )

        product = empty_product()
        product["source_url"] = url
        product["extraction_method"] = "playwright"
        product["confidence_score"] = 0.45
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(rendered, "lxml")
            h1 = soup.select_one("h1")
            product["title"] = h1.get_text(" ", strip=True) if h1 else ""
            img = soup.select_one("img")
            if img and img.get("src"):
                product["images"] = [
                    {"src": absolute_url(url, img.get("src")), "alt": "", "position": 1}
                ]
            price_el = soup.select_one('[class*="price"], .price, [itemprop="price"]')
            if price_el:
                product["price"] = normalize_price(
                    price_el.get("content") or price_el.get_text(" ", strip=True)
                )
            options = self._options_from_html(rendered)
            if options:
                product["options"] = options
            if variant_probe:
                product = self._merge_variant_probe(product, variant_probe)
        except Exception:
            return None
        if not product.get("title"):
            return None
        return self._ensure_woocommerce_price(product, rendered, logger=logger)

    @staticmethod
    def _product_has_price(product: dict[str, Any] | None) -> bool:
        if not isinstance(product, dict):
            return False
        if str(product.get("price") or "").strip():
            return True
        for v in product.get("variants") or []:
            if isinstance(v, dict) and str(v.get("price") or "").strip():
                return True
        return False

    def _ensure_woocommerce_price(
        self,
        product: dict[str, Any] | None,
        rendered: str,
        *,
        logger: logging.Logger | None = None,
    ) -> dict[str, Any] | None:
        """Fill/override price from WooCommerce CSS selectors on rendered HTML."""
        if not isinstance(product, dict):
            return product
        log = logger or logging.getLogger(__name__)
        price = self._woocommerce_css_price(rendered)
        if price:
            log.info("Price extracted via Playwright CSS selector: %s", price)
            product["price"] = price
            variants = product.get("variants") or []
            if isinstance(variants, list):
                for v in variants:
                    if isinstance(v, dict) and not str(v.get("price") or "").strip():
                        v["price"] = price
                    elif isinstance(v, dict) and self._has_woocommerce_price_markup(rendered):
                        # Prefer Woo sale/current price over crude HTML scrape.
                        v["price"] = price
            return product
        if not self._product_has_price(product):
            log.info("Price not found on page")
        return product

    @staticmethod
    def _has_woocommerce_price_markup(html: str) -> bool:
        low = (html or "").lower()
        return "woocommerce-price-amount" in low or "woocommerce-Price-amount".lower() in low

    def _woocommerce_css_price(self, html: str) -> str:
        """Try WooCommerce price selectors in order; return normalized float string."""
        if not html:
            return ""
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "lxml")
        except Exception:
            return ""

        selectors = list(self.WOO_PRICE_SELECTORS)
        # #product-[id] .price — resolve concrete product id when present
        product_id = ""
        for el in soup.select("[data-product_id], [data-product-id], input[name='product']"):
            product_id = str(
                el.get("data-product_id")
                or el.get("data-product-id")
                or el.get("value")
                or ""
            ).strip()
            if product_id.isdigit():
                break
        if not product_id:
            body = soup.body
            classes = " ".join(body.get("class") or []) if body else ""
            m = re.search(r"(?:postid-|product-|productid-)(\d+)", classes, re.I)
            if m:
                product_id = m.group(1)
        if product_id.isdigit():
            selectors.append(f"#product-{product_id} .price")
        else:
            selectors.append("[id^=product-] .price")

        for sel in selectors:
            try:
                candidates = soup.select(sel)
            except Exception:
                continue
            for el in candidates:
                # Prefer current/sale price — skip struck-through compare-at amounts.
                if el.find_parent("del") is not None:
                    continue
                text = el.get("content") if el.has_attr("content") else None
                text = (text or el.get_text(" ", strip=True) or "").strip()
                if not text:
                    continue
                # Strip currency symbols / labels then parse as float via normalize_price.
                cleaned = re.sub(r"[^\d.,\-]", "", text.replace(",", ""))
                price = normalize_price(cleaned or text)
                if price:
                    return price
        return ""

    def _render_with_capture(self, url: str, ctx: dict[str, Any]) -> dict[str, Any] | None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return None

        timeout = int(ctx.get("timeout_ms") or 30000)
        live = ctx.get("playwright_session")
        if live is not None and getattr(live, "alive", False):
            try:
                return live.render_url(url, timeout_ms=timeout)
            except Exception:
                return None
        network_json: list[Any] = []
        try:
            with sync_playwright() as p:
                headless = True if ctx.get("playwright_headless") is None else bool(
                    ctx.get("playwright_headless")
                )
                if live is not None:
                    headless = False
                browser = p.chromium.launch(headless=headless)
                browser_ctx = None
                from sentivo_extractor.core.utils import BROWSER_HEADERS, BROWSER_USER_AGENT

                use_browser_headers = bool(
                    ctx.get("browser_headers")
                    or str(ctx.get("platform") or "").lower() == "magento"
                )
                if use_browser_headers:
                    context_opts = {
                        "viewport": {"width": 1440, "height": 900},
                        "user_agent": BROWSER_USER_AGENT,
                        "extra_http_headers": {
                            k: v
                            for k, v in BROWSER_HEADERS.items()
                            if k.lower() != "user-agent"
                        },
                    }
                    browser_ctx = browser.new_context(**context_opts)
                    page = browser_ctx.new_page()
                else:
                    page = browser.new_page(viewport={"width": 1440, "height": 900})

                def on_response(response) -> None:
                    try:
                        ct = (response.headers.get("content-type") or "").lower()
                        rurl = response.url.lower()
                        if "json" not in ct and not any(
                            x in rurl for x in ("/products", "variant", "graphql", "api/")
                        ):
                            return
                        if response.status >= 400:
                            return
                        data = response.json()
                        network_json.append({"url": response.url, "data": data})
                    except Exception:
                        return

                if ctx.get("capture_network", True):
                    page.on("response", on_response)

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                    page.wait_for_timeout(1500)
                    variant_probe = None
                    # Render-only: never click variant controls (probe_variants defaults False).
                    if ctx.get("probe_variants") is True:
                        variant_probe = self._probe_variants(page)
                    html = page.content()
                    screenshot_png = None
                    try:
                        screenshot_png = page.screenshot(type="png", full_page=False)
                    except Exception:
                        screenshot_png = None
                    return {
                        "html": html,
                        "network_json": network_json,
                        "variant_probe": variant_probe,
                        "screenshot_png": screenshot_png,
                    }
                finally:
                    try:
                        if browser_ctx is not None:
                            browser_ctx.close()
                    except Exception:
                        pass
                    browser.close()
        except Exception:
            return None

    def _probe_variants(self, page) -> dict[str, Any] | None:
        """Disabled: Playwright must not click variant selectors."""
        return None

    def _select_combo(self, page, options: list[dict[str, Any]], combo: tuple) -> None:
        for idx, value in enumerate(combo):
            kind = options[idx].get("kind") or "select"
            # Try select by label/text
            try:
                if kind == "select":
                    selects = page.query_selector_all(
                        "form select, .variations select, select[name*='attribute'], select[name*='option']"
                    )
                    if idx < len(selects):
                        selects[idx].select_option(label=value)
                        continue
            except Exception:
                pass
            # Click matching button/radio/swatch
            try:
                page.evaluate(
                    """([value]) => {
                    const nodes = [
                      ...document.querySelectorAll(
                        "button, [role=radio], a[class*=swatch], span[class*=swatch], label, input[type=radio]"
                      )
                    ];
                    for (const n of nodes) {
                      const t = (n.innerText || n.value || n.getAttribute('data-value')
                        || n.getAttribute('title') || '').trim();
                      if (t === value) {
                        if (n.tagName === 'INPUT') {
                          n.click();
                        } else {
                          n.click();
                        }
                        return true;
                      }
                    }
                    return false;
                }""",
                    [value],
                )
            except Exception:
                continue

    def _merge_variant_probe(
        self, product: dict[str, Any], probe: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(product, dict):
            return product
        if not isinstance(probe, dict):
            return product
        if probe.get("options"):
            product["options"] = [
                o for o in (probe.get("options") or [])[:3] if isinstance(o, dict)
            ]
        if probe.get("variants"):
            # Deduplicate
            seen: set[tuple[str, str, str]] = set()
            clean = []
            for v in probe["variants"]:
                if not isinstance(v, dict):
                    continue
                key = (
                    str(v.get("option1") or ""),
                    str(v.get("option2") or ""),
                    str(v.get("option3") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                clean.append(v)
            product["variants"] = clean
            product["confidence_score"] = max(
                float(product.get("confidence_score") or 0), 0.8
            )
        return product

    def _options_from_html(self, html: str) -> list[dict[str, Any]]:
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "lxml")
        except Exception:
            return []
        options: list[dict[str, Any]] = []
        for sel in soup.select(
            "form select, .variations select, select[name*='attribute'], select[name*='option']"
        ):
            if len(options) >= 3:
                break
            name = canonicalize_option_name(
                (sel.get("name") or sel.get("aria-label") or "Option").replace("_", " ")
            )
            values = []
            for opt in sel.select("option"):
                if opt.has_attr("disabled"):
                    continue
                val = (opt.get_text(" ", strip=True) or "").strip()
                val = re.sub(r"[£$€]\s*\d[\d,]*(?:\.\d+)?", "", val).strip()
                if not val or val.lower().startswith("choose"):
                    continue
                if val not in values:
                    values.append(val)
            if values:
                options.append({"name": name, "values": values})
        return options

    def _product_from_network(self, payloads: list[Any], url: str) -> dict[str, Any] | None:
        for item in payloads:
            data = item.get("data") if isinstance(item, dict) else item
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    continue
            node = self._find_product_node(data)
            if not isinstance(node, dict):
                continue
            product = empty_product()
            product["source_url"] = url
            product["title"] = str(node.get("title") or node.get("name") or "")
            product["handle"] = str(node.get("handle") or node.get("slug") or "")
            product["description_html"] = str(
                node.get("body_html") or node.get("description") or ""
            )
            brand = node.get("vendor") or node.get("brand") or ""
            if isinstance(brand, dict):
                brand = brand.get("name") or brand.get("title") or ""
            product["vendor"] = str(brand or "")
            images = []
            for i, img in enumerate(node.get("images") or [], start=1):
                src = img.get("src") if isinstance(img, dict) else str(img)
                if src:
                    images.append({"src": absolute_url(url, src), "alt": "", "position": i})
            if not images and node.get("image"):
                images.append(
                    {
                        "src": absolute_url(url, str(node.get("image"))),
                        "alt": "",
                        "position": 1,
                    }
                )
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
                elif isinstance(opt, str) and opt.strip():
                    options.append({"name": opt.strip(), "values": []})
            product["options"] = options
            variants = []
            seen_keys: set[tuple[str, str, str]] = set()
            for v in node.get("variants") or []:
                if not isinstance(v, dict):
                    continue
                key = (
                    str(v.get("option1") or v.get("title") or "Default Title"),
                    str(v.get("option2") or ""),
                    str(v.get("option3") or ""),
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                variants.append(
                    {
                        "sku": str(v.get("sku") or ""),
                        "barcode": str(v.get("barcode") or ""),
                        "option1": key[0],
                        "option2": key[1],
                        "option3": key[2],
                        "price": normalize_price(v.get("price")),
                        "compare_at_price": normalize_price(v.get("compare_at_price")),
                        "inventory_qty": str(v.get("inventory_quantity") or ""),
                        "available": bool(v.get("available", True)),
                        "weight_grams": str(v.get("grams") or ""),
                        "variant_image": "",
                    }
                )
            product["variants"] = variants
            if product["title"]:
                return product
        return None

    def _find_product_node(self, data: Any, depth: int = 0) -> dict[str, Any] | None:
        if depth > 8 or data is None:
            return None
        if isinstance(data, dict):
            keys = {k.lower() for k in data}
            if ("title" in keys or "name" in keys) and (
                "variants" in keys or "price" in keys or "images" in keys
            ):
                return data
            # Shopify product.js shape
            if "product" in data and isinstance(data["product"], dict):
                found = self._find_product_node(data["product"], depth + 1)
                if found:
                    return found
            for v in data.values():
                found = self._find_product_node(v, depth + 1)
                if found:
                    return found
        elif isinstance(data, list):
            for item in data[:30]:
                found = self._find_product_node(item, depth + 1)
                if found:
                    return found
        return None
