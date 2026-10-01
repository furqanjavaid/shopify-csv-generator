"""Robust Product Detail Page (PDP) extraction with field-level source tracking."""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from bs4 import BeautifulSoup

from sentivo_extractor.core.platform_detector import detect_platform
from sentivo_extractor.core.schema import empty_product, ensure_product
from sentivo_extractor.core.utils import (
    absolute_url,
    is_blank_price,
    normalize_price,
    resolve_price_from_html,
    sanitize_sku,
    write_json,
)
from sentivo_extractor.extractors.base import ExtractorRegistry
from sentivo_extractor.extractors.html_extractor import HtmlExtractor
from sentivo_extractor.extractors.jsonld_extractor import JsonLdExtractor
from sentivo_extractor.extractors.magento_extractor import MagentoExtractor
from sentivo_extractor.extractors.nextjs_extractor import NextJsExtractor
from sentivo_extractor.extractors.playwright_extractor import PlaywrightExtractor
from sentivo_extractor.extractors.shopify_extractor import ShopifyExtractor
from sentivo_extractor.extractors.woocommerce_extractor import WooCommerceExtractor

SOURCE_SHOPIFY = "Shopify JS"
SOURCE_EMBEDDED = "Embedded JSON"
SOURCE_JSONLD = "JSON-LD"
SOURCE_WOO = "WooCommerce API"
SOURCE_MAGENTO = "Magento Config"
SOURCE_OPENGRAPH = "OpenGraph"
SOURCE_NEXTJS = "Next.js / Initial State"
SOURCE_DOM = "DOM"
SOURCE_PLAYWRIGHT = "Playwright"

# HTTP-only cascade. Playwright is never in this list.
HTTP_METHOD_ORDER = (
    SOURCE_SHOPIFY,
    SOURCE_EMBEDDED,
    SOURCE_JSONLD,
    SOURCE_WOO,
    SOURCE_MAGENTO,
    SOURCE_OPENGRAPH,
    SOURCE_NEXTJS,
    SOURCE_DOM,
)

SOURCE_CONFIDENCE = {
    SOURCE_SHOPIFY: 0.95,
    SOURCE_EMBEDDED: 0.88,
    SOURCE_JSONLD: 0.9,
    SOURCE_WOO: 0.9,
    SOURCE_MAGENTO: 0.85,
    SOURCE_OPENGRAPH: 0.55,
    SOURCE_NEXTJS: 0.86,
    SOURCE_DOM: 0.62,
    SOURCE_PLAYWRIGHT: 0.78,
}

# Back-compat aliases used by older reports / tests
SOURCE_NETWORK = "Network"
METHOD_ORDER = HTTP_METHOD_ORDER + (SOURCE_PLAYWRIGHT,)

REQUIRED_SHOPIFY_FIELDS = ("title", "price", "images")

LOG_FIELD_LABELS = (
    ("title", "Title"),
    ("brand", "Brand"),
    ("sku", "SKU"),
    ("price", "Price"),
    ("compare_at_price", "Compare-at price"),
    ("availability", "Availability"),
    ("description_html", "Description"),
    ("specifications", "Specifications"),
    ("product_type", "Product type"),
    ("breadcrumb", "Breadcrumb/category"),
    ("images", "Images"),
    ("variant_names", "Variants"),
    ("variant_values", "Variant values"),
    ("variant_sku", "Variant SKU"),
    ("variant_price", "Variant price"),
    ("variant_image", "Variant image"),
    ("downloads", "Downloads"),
)


def write_pdp_extraction_report(
    entries: list[dict[str, Any]], path: Path
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "product_count": len(entries),
        "products": entries,
    }
    write_json(path, payload)
    return path


def write_retry_queue(items: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"retry_queue": items})
    return path


def _confidence_for_source(source: str) -> float:
    return float(SOURCE_CONFIDENCE.get(source) or 0.5)


def _field_confidence(source: str, filled: bool) -> float:
    if not filled:
        return 0.0
    return _confidence_for_source(source)


class PDPExtractionPipeline:
    """
    Extract PDP data HTTP-first; Playwright only as last resort:
      Shopify .js → Embedded JSON → JSON-LD → Woo Store API → Magento →
      OpenGraph → Next.js / __INITIAL_STATE__ → DOM →
      Playwright (render + network JSON only, if all above empty/blocked)
    """

    def __init__(
        self,
        *,
        http: Any,
        options: dict[str, Any],
        registry: ExtractorRegistry,
        site_rules: dict[str, Any] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.http = http
        self.options = options
        self.registry = registry
        self.site_rules = site_rules or {}
        self.logger = logger or logging.getLogger(__name__)
        self._playwright_bundle: dict[str, Any] | None = None

    def extract(self, url: str) -> dict[str, Any]:
        html = ""
        fetch_blocked = False
        try:
            html = self.http.get_text(url)
        except Exception as exc:
            self.logger.warning("GET failed %s: %s", url, exc)
            fetch_blocked = True

        detected = detect_platform(
            url, html=html, session=getattr(self.http, "session", None)
        )
        platform = detected.get("platform") or "Custom"
        context: dict[str, Any] = {
            "http": self.http,
            "platform": platform,
            "site_rules": self.site_rules.get("selectors") or self.site_rules,
            # Never default to Playwright. Enabled only for last-resort fallback below.
            "use_playwright": False,
            "timeout_ms": int(float(self.options.get("timeout") or 25) * 1000),
            "capture_network": True,
            # Render-only — no variant clicking / interactions.
            "probe_variants": False,
            "logger": self.logger,
        }

        values: dict[str, Any] = {}
        sources: dict[str, str] = {}
        confidences: dict[str, float] = {}
        methods_attempted: list[str] = []

        for method in HTTP_METHOD_ORDER:
            partial = self._run_method(method, url, html, context)
            methods_attempted.append(method)
            if not partial:
                continue
            self._merge_partial(values, sources, confidences, partial, method)

        missing = self._missing_required(values)
        if missing:
            for method in HTTP_METHOD_ORDER:
                if not missing:
                    break
                partial = self._run_method(
                    method, url, html, context, retry_missing=missing
                )
                if partial:
                    self._merge_partial(
                        values,
                        sources,
                        confidences,
                        partial,
                        method,
                        only_fields=set(missing),
                    )
                missing = self._missing_required(values)

        if self._needs_playwright_fallback(
            values, html=html, fetch_blocked=fetch_blocked
        ):
            self.logger.info(
                "HTTP extractors empty/blocked — activating Playwright render-only fallback"
            )
            context["use_playwright"] = True
            methods_attempted.append(SOURCE_PLAYWRIGHT)
            partial = self._run_method(SOURCE_PLAYWRIGHT, url, html, context)
            if partial:
                self._merge_partial(
                    values, sources, confidences, partial, SOURCE_PLAYWRIGHT
                )
            missing = self._missing_required(values)
            if missing:
                partial = self._run_method(
                    SOURCE_PLAYWRIGHT, url, html, context, retry_missing=missing
                )
                if partial:
                    self._merge_partial(
                        values,
                        sources,
                        confidences,
                        partial,
                        SOURCE_PLAYWRIGHT,
                        only_fields=set(missing),
                    )

        engine_html = html
        if self._playwright_bundle and self._playwright_bundle.get("html"):
            engine_html = self._playwright_bundle["html"]

        self._ensure_price(values, sources, confidences, engine_html or html)
        self._ensure_description(
            values,
            sources,
            confidences,
            url,
            html=html,
            engine_html=engine_html or html,
            context=context,
        )

        product = self._build_product(values, url)
        product["field_sources"] = dict(sources)
        product["extraction_method"] = self._summarize_method(sources)
        product["platform"] = platform
        if "Shopify JS" in (product.get("extraction_method") or ""):
            self.logger.info(
                "Shopify JS variants: %s extracted",
                len(product.get("variants") or []),
            )

        product, variant_report = self._apply_variant_engine(
            product, url, engine_html, context
        )
        self._scrub_zero_prices(product)
        product, image_report = self._apply_image_engine(
            product, url, engine_html, context
        )

        report_entry = self._build_report_entry(
            url=url,
            product=product,
            values=values,
            sources=sources,
            confidences=confidences,
            missing=self._missing_required(values),
            methods_attempted=methods_attempted,
        )

        self._log_field_sources(sources)

        still_missing = self._missing_required(values)
        # After fallbacks, blank price is allowed — never emit 0.00.
        still_missing = [m for m in still_missing if m != "price"]
        debug = self._debug_artifacts(engine_html)
        if still_missing:
            reason = f"missing_required: {', '.join(still_missing)}"
            failed = ensure_product(product)
            failed["_fail_reason"] = reason
            failed["missing_fields"] = still_missing
            return {
                "success": False,
                "product": None,
                "failed_record": failed,
                "reason": reason,
                "report": report_entry,
                "image_report": image_report,
                "debug_artifacts": debug,
            }

        if not product.get("images"):
            reason = "missing_valid_images"
            failed = ensure_product(product)
            failed["_fail_reason"] = reason
            failed["missing_fields"] = ["images"]
            failed["image_engine_report"] = image_report
            return {
                "success": False,
                "product": None,
                "failed_record": failed,
                "reason": reason,
                "report": report_entry,
                "image_report": image_report,
                "debug_artifacts": debug,
            }

        return {
            "success": True,
            "product": product,
            "failed_record": None,
            "reason": "",
            "report": report_entry,
            "variant_report": variant_report,
            "image_report": image_report,
            "debug_artifacts": debug,
        }

    def _needs_playwright_fallback(
        self,
        values: dict[str, Any],
        *,
        html: str,
        fetch_blocked: bool,
    ) -> bool:
        """Playwright only when HTTP cascade is empty or the fetch was blocked."""
        if fetch_blocked and not (html or "").strip():
            return True
        # All HTTP methods returned empty — no product title from any source.
        if not self._is_filled(values.get("title")):
            return True
        return False

    def _debug_artifacts(self, html: str) -> dict[str, Any]:
        bundle = self._playwright_bundle or {}
        return {
            "html": bundle.get("html") or html or "",
            "network_json": bundle.get("network_json") or [],
            "screenshot_png": bundle.get("screenshot_png"),
        }

    def _apply_variant_engine(
        self,
        product: dict[str, Any],
        url: str,
        html: str,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        from sentivo_extractor.core.variant_engine import ProductionVariantEngine

        bundle = self._playwright_bundle or {}
        engine = ProductionVariantEngine(logger=self.logger)
        result = engine.process(
            url=url,
            product=product,
            html=html,
            network_json=bundle.get("network_json") or [],
            probe=bundle.get("variant_probe"),
        )
        product = engine.apply_to_product(product, result)
        return product, result.to_report(url=url)

    def _apply_image_engine(
        self,
        product: dict[str, Any],
        url: str,
        html: str,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        from sentivo_extractor.core.image_engine import ProductionImageEngine

        bundle = self._playwright_bundle or {}
        verify = bool(self.options.get("verify_product_images", True))
        engine = ProductionImageEngine(
            logger=self.logger,
            verify=verify,
            min_width=int(self.options.get("min_image_width") or 50),
            min_height=int(self.options.get("min_image_height") or 50),
            session=getattr(self.http, "session", None),
            timeout=float(self.options.get("timeout") or 15),
        )
        result = engine.process(
            url=url,
            product=product,
            html=html,
            network_json=bundle.get("network_json") or [],
            rendered_html=bundle.get("html") or html,
        )
        product = engine.apply_to_product(product, result)
        return product, result.to_report(url=url)

    def _failure_outcome(
        self, url: str, reason: str, partial_values: dict[str, Any]
    ) -> dict[str, Any]:
        product = self._build_product(partial_values, url)
        failed = ensure_product(product)
        failed["_fail_reason"] = reason
        failed["missing_fields"] = list(REQUIRED_SHOPIFY_FIELDS)
        report = self._build_report_entry(
            url=url,
            product=product,
            values=partial_values,
            sources={},
            confidences={},
            missing=list(REQUIRED_SHOPIFY_FIELDS),
            methods_attempted=[],
        )
        return {
            "success": False,
            "product": None,
            "failed_record": failed,
            "reason": reason,
            "report": report,
            "debug_artifacts": self._debug_artifacts(""),
        }

    def _run_method(
        self,
        method: str,
        url: str,
        html: str,
        context: dict[str, Any],
        retry_missing: list[str] | None = None,
    ) -> dict[str, Any] | None:
        del retry_missing  # reserved for future field-targeted retries
        try:
            return self._run_method_impl(method, url, html, context)
        except AttributeError as exc:
            self.logger.error(
                "Extractor %s non-dict value for %s: %s — skipping",
                method,
                url,
                exc,
            )
            return None

    def _run_method_impl(
        self,
        method: str,
        url: str,
        html: str,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        if method == SOURCE_SHOPIFY:
            raw = ShopifyExtractor().extract(url, html, context=context)
            return self._from_extractor_product(raw, method)
        if method == SOURCE_EMBEDDED:
            return self._from_extractor_product(
                self._try_embedded_json(url, html, context), method
            )
        if method == SOURCE_JSONLD:
            raw = JsonLdExtractor().extract(url, html, context=context)
            return self._from_extractor_product(raw, method)
        if method == SOURCE_WOO:
            client = context.get("http")
            if client is None:
                return None
            raw = WooCommerceExtractor()._try_store_api(url, client)  # noqa: SLF001
            return self._from_extractor_product(raw, method)
        if method == SOURCE_MAGENTO:
            raw = MagentoExtractor().extract(url, html, context=context)
            return self._from_extractor_product(raw, method)
        if method == SOURCE_OPENGRAPH:
            return self._try_opengraph(html, url)
        if method == SOURCE_NEXTJS:
            raw = NextJsExtractor().extract(url, html, context=context)
            if not raw:
                raw = self._try_initial_state(html, url)
            return self._from_extractor_product(raw, method)
        if method == SOURCE_DOM:
            raw = HtmlExtractor().extract(url, html, context=context)
            if not raw:
                return None
            extras = self._dom_extras(html, url)
            if isinstance(raw, dict) and isinstance(extras, dict):
                raw.update(extras)
            return self._from_extractor_product(raw, method)
        if method == SOURCE_PLAYWRIGHT:
            bundle = self._ensure_playwright_bundle(url, context)
            if not bundle:
                return None
            rendered = bundle.get("html") or html
            pw = PlaywrightExtractor()
            raw = pw._extract_from_rendered(  # noqa: SLF001
                url,
                rendered,
                bundle.get("network_json") or [],
                context,
                variant_probe=bundle.get("variant_probe"),
            )
            if isinstance(raw, str):
                self.logger.error(
                    "Playwright returned string instead of dict for %s — treating as failed",
                    url,
                )
                return None
            return self._from_extractor_product(raw, method)
        return None

    def _try_embedded_json(
        self, url: str, html: str, context: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Inline product JSON blobs only (Shopify/Next/JSON-LD handled separately)."""
        del url, context
        try:
            soup = BeautifulSoup(html or "", "lxml")
            for script in soup.find_all("script"):
                # Leave JSON-LD and Next hydration to their dedicated steps.
                stype = (script.get("type") or "").lower()
                sid = (script.get("id") or "").lower()
                if "ld+json" in stype or sid in {"__next_data__", "__nuxt_data__"}:
                    continue
                text = (script.string or script.get_text() or "").strip()
                if not text or len(text) < 40:
                    continue
                if "product" not in text.lower():
                    continue
                for chunk in re.findall(r"\{[^{}]{40,8000}\}", text):
                    try:
                        data = json.loads(chunk)
                    except Exception:
                        continue
                    if isinstance(data, dict) and (
                        data.get("title") or data.get("name")
                    ):
                        return data
        except Exception:
            pass
        return None

    def _try_opengraph(self, html: str, url: str) -> dict[str, Any] | None:
        try:
            soup = BeautifulSoup(html or "", "lxml")
        except Exception:
            return None

        def _meta(*keys: str) -> str:
            for key in keys:
                tag = soup.find("meta", attrs={"property": key}) or soup.find(
                    "meta", attrs={"name": key}
                )
                if tag and tag.get("content"):
                    return str(tag["content"]).strip()
            return ""

        title = _meta("og:title", "twitter:title")
        description = _meta("og:description", "twitter:description", "description")
        image = _meta("og:image", "og:image:url", "twitter:image")
        price = _meta(
            "product:price:amount",
            "og:price:amount",
            "twitter:data1",
        )
        currency = _meta("product:price:currency", "og:price:currency")
        availability = _meta("product:availability", "og:availability")
        brand = _meta("product:brand", "og:brand")

        if not any((title, image, price, description)):
            return None

        out: dict[str, Any] = {}
        if title:
            out["title"] = title
        if brand:
            out["brand"] = brand
        if description:
            out["description_html"] = description
        if price:
            parsed = normalize_price(price)
            if not is_blank_price(parsed):
                out["price"] = parsed
        if currency and out.get("price"):
            out["currency"] = currency
        if availability:
            low = availability.lower()
            out["availability"] = (
                "in_stock"
                if "instock" in low.replace(" ", "") or "in stock" in low
                else availability
            )
        if image:
            out["images"] = [
                {"src": absolute_url(url, image), "alt": "", "position": 1}
            ]
        return out

    def _try_initial_state(
        self, html: str, url: str
    ) -> dict[str, Any] | None:
        """Parse window.__INITIAL_STATE__ / similar bootstrap JSON."""
        del url
        if not html:
            return None
        patterns = (
            r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;",
            r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;",
            r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\})\s*;",
        )
        for pat in patterns:
            m = re.search(pat, html, re.S)
            if not m:
                continue
            try:
                data = json.loads(m.group(1))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            # Prefer nested product-like nodes
            candidates = [data]
            for key in ("product", "productDetail", "pdp", "pageProps"):
                node = data.get(key) if isinstance(data.get(key), dict) else None
                if node:
                    candidates.insert(0, node)
            for node in candidates:
                title = node.get("title") or node.get("name")
                if title:
                    return node
        return None

    def _dom_extras(self, html: str, url: str) -> dict[str, Any]:
        extras: dict[str, Any] = {
            "specifications": {},
            "downloads": [],
            "breadcrumb": "",
        }
        try:
            soup = BeautifulSoup(html or "", "lxml")
            crumbs = [
                c.get_text(" ", strip=True)
                for c in soup.select(
                    ".breadcrumb a, nav[aria-label*='breadcrumb'] a, "
                    "[class*='breadcrumb'] a"
                )
                if c.get_text(strip=True)
            ]
            if crumbs:
                extras["breadcrumb"] = " > ".join(crumbs[-6:])

            specs: dict[str, str] = {}
            for table in soup.select(
                "table.specifications, .woocommerce-product-attributes, "
                ".product-specs table, table.shop_attributes"
            ):
                for row in table.select("tr"):
                    cells = row.find_all(["th", "td"])
                    if len(cells) >= 2:
                        k = cells[0].get_text(" ", strip=True)
                        v = cells[1].get_text(" ", strip=True)
                        if k and v:
                            specs[k] = v
            extras["specifications"] = specs

            downloads: list[str] = []
            for a in soup.select('a[href$=".pdf"], a[href*=".pdf?"]'):
                href = a.get("href") or ""
                if href:
                    downloads.append(absolute_url(url, href))
            extras["downloads"] = list(dict.fromkeys(downloads))
        except Exception:
            pass
        return extras

    def _ensure_playwright_bundle(
        self, url: str, context: dict[str, Any]
    ) -> dict[str, Any] | None:
        if self._playwright_bundle is not None:
            return self._playwright_bundle
        # Only called from SOURCE_PLAYWRIGHT last-resort path (use_playwright already True).
        if not context.get("use_playwright"):
            return None
        pw = PlaywrightExtractor()
        self._playwright_bundle = pw._render_with_capture(url, context)  # noqa: SLF001
        return self._playwright_bundle

    def _from_extractor_product(
        self, raw: dict[str, Any] | None, method: str
    ) -> dict[str, Any] | None:
        if isinstance(raw, str):
            self.logger.error(
                "Extractor %s returned string instead of dict — treating as failed",
                method,
            )
            return None
        if not isinstance(raw, dict):
            return None
        return self._flatten_product_fields(raw, method)

    def _flatten_product_fields(
        self, raw: dict[str, Any], method: str
    ) -> dict[str, Any]:
        """Map extractor product dict → pipeline field keys."""
        out: dict[str, Any] = {}
        title = raw.get("title") or raw.get("name")
        if title:
            out["title"] = str(title)
        brand = raw.get("brand") or raw.get("vendor") or ""
        if isinstance(brand, dict):
            brand = brand.get("name") or brand.get("title") or ""
        if brand:
            out["brand"] = str(brand)
        if raw.get("description_html"):
            out["description_html"] = str(raw["description_html"])
        if raw.get("product_type"):
            out["product_type"] = str(raw["product_type"])
        if raw.get("department"):
            out["breadcrumb"] = str(raw["department"])
        if raw.get("breadcrumb"):
            out["breadcrumb"] = str(raw["breadcrumb"])
        if raw.get("specifications"):
            out["specifications"] = raw["specifications"]
        if raw.get("downloads"):
            out["downloads"] = raw["downloads"]

        images = raw.get("images") or []
        if images:
            out["images"] = self._best_resolution_images(images, raw.get("source_url"))

        options = raw.get("options") or []
        variants = raw.get("variants") or []
        # Prefer keeping structured Shopify/API option+variant payloads intact.
        structured_options: list[dict[str, Any]] = []
        for o in options[:3]:
            if isinstance(o, dict):
                structured_options.append(
                    {
                        "name": str(o.get("name") or "Option"),
                        "values": list(o.get("values") or []),
                    }
                )
            elif isinstance(o, str) and o.strip():
                structured_options.append({"name": o.strip(), "values": []})
        if structured_options:
            out["options"] = structured_options
            out["variant_names"] = [str(o.get("name") or "") for o in structured_options]
            out["variant_values"] = [list(o.get("values") or []) for o in structured_options]

        structured_variants = [v for v in variants if isinstance(v, dict)]
        if structured_variants:
            cleaned_variants: list[dict[str, Any]] = []
            for v in structured_variants:
                vv = dict(v)
                vv["sku"] = sanitize_sku(vv.get("sku"))
                cleaned_variants.append(vv)
            structured_variants = cleaned_variants
            out["variants"] = structured_variants
            out["variant_sku"] = [str(v.get("sku") or "") for v in structured_variants]
            out["variant_price"] = [
                ""
                if is_blank_price(v.get("price"))
                else normalize_price(v.get("price"))
                for v in structured_variants
            ]
            out["variant_image"] = [
                str(v.get("variant_image") or "") for v in structured_variants
            ]
            for v in structured_variants:
                if is_blank_price(v.get("price")):
                    v["price"] = ""
                else:
                    v["price"] = normalize_price(v.get("price"))
                if is_blank_price(v.get("compare_at_price")):
                    v["compare_at_price"] = ""
            avail = structured_variants[0].get("available")
            out["availability"] = (
                "in_stock" if avail is not False else "out_of_stock"
            )
            if structured_variants[0].get("price") and not is_blank_price(
                structured_variants[0].get("price")
            ):
                out["price"] = normalize_price(str(structured_variants[0].get("price")))
            if structured_variants[0].get("compare_at_price") and not is_blank_price(
                structured_variants[0].get("compare_at_price")
            ):
                out["compare_at_price"] = normalize_price(
                    str(structured_variants[0].get("compare_at_price"))
                )
            if structured_variants[0].get("sku"):
                out["sku"] = str(structured_variants[0].get("sku"))
        else:
            if raw.get("price") and not is_blank_price(raw.get("price")):
                out["price"] = normalize_price(str(raw.get("price")))
            if raw.get("compare_at_price") and not is_blank_price(
                raw.get("compare_at_price")
            ):
                out["compare_at_price"] = normalize_price(
                    str(raw.get("compare_at_price"))
                )
            if raw.get("sku"):
                out["sku"] = sanitize_sku(raw.get("sku"))

        return out

    def _best_resolution_images(
        self, images: list[Any], base_url: str = ""
    ) -> list[dict[str, Any]]:
        """Prefer largest src / srcset candidate."""
        cleaned: list[dict[str, Any]] = []
        for i, img in enumerate(images, start=1):
            if isinstance(img, dict):
                src = str(img.get("src") or "")
                alt = str(img.get("alt") or "")
            else:
                src = str(img)
                alt = ""
            if not src:
                continue
            src = absolute_url(base_url or "https://example.com/", src)
            src = re.sub(r"[?&]width=\d+", "", src)
            cleaned.append({"src": src, "alt": alt, "position": i})
        return cleaned

    def _merge_partial(
        self,
        values: dict[str, Any],
        sources: dict[str, str],
        confidences: dict[str, float],
        partial: dict[str, Any],
        method: str,
        only_fields: set[str] | None = None,
    ) -> None:
        for key, val in partial.items():
            if only_fields is not None and key not in only_fields:
                continue
            if not self._is_filled(val, key=key):
                continue
            if self._is_filled(values.get(key), key=key) and only_fields is None:
                continue
            values[key] = deepcopy(val)
            sources[key] = method
            confidences[key] = _field_confidence(method, True)

    @staticmethod
    def _is_filled(val: Any, key: str = "") -> bool:
        if val is None:
            return False
        if key in {"price", "compare_at_price"}:
            return not is_blank_price(val)
        if key == "variant_price":
            if isinstance(val, list):
                return any(not is_blank_price(p) for p in val)
            return not is_blank_price(val)
        if isinstance(val, str):
            return bool(val.strip())
        if isinstance(val, (list, dict)):
            return bool(val)
        return True

    def _missing_required(self, values: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        if not self._is_filled(values.get("title")):
            missing.append("title")
        if not self._is_filled(values.get("images")):
            missing.append("images")
        has_price = self._is_filled(values.get("price"), key="price")
        if not has_price:
            for p in values.get("variant_price") or []:
                if self._is_filled(p, key="price"):
                    has_price = True
                    break
        if not has_price:
            missing.append("price")
        return missing

    def _ensure_price(
        self,
        values: dict[str, Any],
        sources: dict[str, str],
        confidences: dict[str, float],
        html: str,
    ) -> None:
        """Normalize price; if 0/empty run fallbacks; never keep 0.00."""
        current = values.get("price")
        if is_blank_price(current):
            for p in values.get("variant_price") or []:
                if not is_blank_price(p):
                    current = p
                    break
            if is_blank_price(current):
                for v in values.get("variants") or []:
                    if isinstance(v, dict) and not is_blank_price(v.get("price")):
                        current = v.get("price")
                        break

        if not is_blank_price(current):
            parsed = normalize_price(current)
            values["price"] = parsed
            src = sources.get("price") or sources.get("variant_price") or "cascade"
            self.logger.info("Price parsed: %s from source %s", parsed, src)
            self._propagate_price(values, parsed)
            return

        resolved, src = resolve_price_from_html(html or "")
        if not is_blank_price(resolved):
            values["price"] = resolved
            sources["price"] = src
            confidences["price"] = _field_confidence(src, True)
            self.logger.info("Price parsed: %s from source %s", resolved, src)
            self._propagate_price(values, resolved)
            return

        self.logger.info("Price not found")
        values["price"] = ""
        sources.pop("price", None)
        self._propagate_price(values, "")

    def _ensure_description(
        self,
        values: dict[str, Any],
        sources: dict[str, str],
        confidences: dict[str, float],
        url: str,
        *,
        html: str,
        engine_html: str,
        context: dict[str, Any],
    ) -> None:
        """Fill description via dedicated priority fallback chain when missing/short."""
        from sentivo_extractor.core.description_engine import (
            extract_description,
            is_usable_description,
        )

        current = values.get("description_html") or ""
        if is_usable_description(current):
            return

        playwright_html = None
        if self._playwright_bundle and self._playwright_bundle.get("html"):
            playwright_html = str(self._playwright_bundle.get("html") or "")
        elif engine_html and engine_html != html:
            playwright_html = engine_html

        desc, source = extract_description(
            url=url,
            html=html or engine_html or "",
            http=context.get("http") or self.http,
            playwright_html=playwright_html,
            logger=self.logger,
        )
        if not desc:
            return
        source_map = {
            "Shopify JS": SOURCE_SHOPIFY,
            "JSON-LD": SOURCE_JSONLD,
            "Embedded JSON": SOURCE_EMBEDDED,
            "OpenGraph": SOURCE_OPENGRAPH,
            "CSS selectors": SOURCE_DOM,
            "meta description": SOURCE_OPENGRAPH,
        }
        mapped = source_map.get(source) or source or SOURCE_DOM
        values["description_html"] = desc
        sources["description_html"] = mapped
        confidences["description_html"] = _field_confidence(mapped, True)

    def _propagate_price(self, values: dict[str, Any], price: str) -> None:
        """Keep variant price lists / variant dicts aligned; blank zeros."""
        if values.get("variant_price") and isinstance(values["variant_price"], list):
            values["variant_price"] = [
                price
                if is_blank_price(p) and price
                else ("" if is_blank_price(p) else normalize_price(p))
                for p in values["variant_price"]
            ]
        for v in values.get("variants") or []:
            if not isinstance(v, dict):
                continue
            if is_blank_price(v.get("price")):
                v["price"] = price
            else:
                v["price"] = normalize_price(v.get("price"))
            if is_blank_price(v.get("compare_at_price")):
                v["compare_at_price"] = ""

    @staticmethod
    def _scrub_zero_prices(product: dict[str, Any]) -> None:
        for v in product.get("variants") or []:
            if not isinstance(v, dict):
                continue
            if is_blank_price(v.get("price")):
                v["price"] = ""
            else:
                v["price"] = normalize_price(v.get("price"))
            if is_blank_price(v.get("compare_at_price")):
                v["compare_at_price"] = ""
            else:
                v["compare_at_price"] = normalize_price(v.get("compare_at_price"))

    def _build_product(self, values: dict[str, Any], url: str) -> dict[str, Any]:
        product = empty_product()
        product["source_url"] = url
        product["title"] = str(values.get("title") or "")
        product["brand"] = str(values.get("brand") or "")
        product["vendor"] = product["brand"]
        product["description_html"] = str(values.get("description_html") or "")
        product["product_type"] = str(values.get("product_type") or "")
        product["department"] = str(values.get("breadcrumb") or "")
        product["images"] = list(values.get("images") or [])
        product["downloads"] = list(values.get("downloads") or [])
        specs = values.get("specifications") or {}
        product["specifications"] = specs if isinstance(specs, dict) else {}
        # Normalize images that may still be bare URL strings from OpenGraph/etc.
        images_out: list[dict[str, Any]] = []
        for i, img in enumerate(product["images"], start=1):
            if isinstance(img, dict):
                images_out.append(img)
            elif isinstance(img, str) and img.strip():
                images_out.append(
                    {"src": img.strip(), "alt": "", "position": i}
                )
        product["images"] = images_out

        option_names = values.get("variant_names") or []
        option_values = values.get("variant_values") or []
        options = []
        raw_options = values.get("options") or []
        if isinstance(raw_options, list) and raw_options:
            for o in raw_options[:3]:
                if isinstance(o, dict) and (o.get("name") or o.get("values")):
                    options.append(
                        {
                            "name": str(o.get("name") or "Option"),
                            "values": list(o.get("values") or []),
                        }
                    )
        if not options:
            for idx, name in enumerate(option_names[:3]):
                vals = option_values[idx] if idx < len(option_values) else []
                if name and vals:
                    options.append({"name": str(name), "values": list(vals)})
        product["options"] = options

        v_skus = values.get("variant_sku") or []
        v_prices = values.get("variant_price") or []
        v_images = values.get("variant_image") or []
        variants: list[dict[str, Any]] = []
        raw_variants = values.get("variants") or []
        if isinstance(raw_variants, list) and raw_variants:
            variants = [v for v in raw_variants if isinstance(v, dict)]
        elif options and v_prices:
            # Align by index when possible (fallback when structured variants absent)
            max_len = max(len(v_prices), len(v_skus), len(v_images), 1)
            for i in range(max_len):
                variants.append(
                    {
                        "sku": sanitize_sku(v_skus[i] if i < len(v_skus) else ""),
                        "barcode": "",
                        "option1": str(
                            (option_values[0][i] if option_values and i < len(option_values[0]) else "")
                            if options
                            else "Default Title"
                        ),
                        "option2": str(
                            option_values[1][i]
                            if len(option_values) > 1 and i < len(option_values[1])
                            else ""
                        ),
                        "option3": str(
                            option_values[2][i]
                            if len(option_values) > 2 and i < len(option_values[2])
                            else ""
                        ),
                        "price": (
                            ""
                            if is_blank_price(
                                str(v_prices[i] if i < len(v_prices) else "")
                            )
                            else normalize_price(
                                str(v_prices[i] if i < len(v_prices) else "")
                            )
                        ),
                        "compare_at_price": (
                            ""
                            if is_blank_price(str(values.get("compare_at_price") or ""))
                            else normalize_price(
                                str(values.get("compare_at_price") or "")
                            )
                        ),
                        "inventory_qty": "",
                        "available": values.get("availability") != "out_of_stock",
                        "weight_grams": "",
                        "variant_image": str(
                            v_images[i] if i < len(v_images) else ""
                        ),
                    }
                )
        elif values.get("title") or values.get("price") or values.get("images"):
            # Single default row; blank/zero price stays empty (never write 0.00).
            product["options"] = [{"name": "Title", "values": ["Default Title"]}]
            price_val = (
                normalize_price(str(values.get("price")))
                if not is_blank_price(values.get("price"))
                else ""
            )
            compare_val = (
                normalize_price(str(values.get("compare_at_price") or ""))
                if not is_blank_price(values.get("compare_at_price"))
                else ""
            )
            variants = [
                {
                    "sku": sanitize_sku(values.get("sku")),
                    "barcode": "",
                    "option1": "Default Title",
                    "option2": "",
                    "option3": "",
                    "price": price_val,
                    "compare_at_price": compare_val,
                    "inventory_qty": "",
                    "available": values.get("availability") != "out_of_stock",
                    "weight_grams": "",
                    "variant_image": "",
                }
            ]
        product["variants"] = variants
        # Prefer product-level price onto blank variant prices.
        base_price = (
            normalize_price(str(values.get("price")))
            if not is_blank_price(values.get("price"))
            else ""
        )
        for v in product["variants"]:
            if not isinstance(v, dict):
                continue
            if is_blank_price(v.get("price")):
                v["price"] = base_price
            else:
                v["price"] = normalize_price(v.get("price"))
            if is_blank_price(v.get("compare_at_price")):
                v["compare_at_price"] = ""
        if values.get("availability"):
            product["status"] = (
                "active" if values.get("availability") != "out_of_stock" else "draft"
            )
        return product

    def _summarize_method(self, sources: dict[str, str]) -> str:
        uniq = []
        for s in sources.values():
            if s not in uniq:
                uniq.append(s)
        return "+".join(uniq) if uniq else "pdp_pipeline"

    def _build_report_entry(
        self,
        *,
        url: str,
        product: dict[str, Any],
        values: dict[str, Any],
        sources: dict[str, str],
        confidences: dict[str, float],
        missing: list[str],
        methods_attempted: list[str],
    ) -> dict[str, Any]:
        fields_report: dict[str, Any] = {}
        summary: dict[str, str] = {}
        for key, label in LOG_FIELD_LABELS:
            val = values.get(key)
            if key == "images" and not val:
                val = product.get("images")
            src = sources.get(key, "missing")
            if self._is_filled(val):
                summary[label] = src
            fields_report[key] = {
                "value": val,
                "source": src,
                "confidence": confidences.get(key, 0.0),
            }
        return {
            "url": url,
            "success": not missing,
            "fields": fields_report,
            "field_sources_summary": summary,
            "missing_fields": missing,
            "methods_attempted": methods_attempted,
            "recommended_confidence": max(confidences.values()) if confidences else 0.0,
        }

    def _log_field_sources(self, sources: dict[str, str]) -> None:
        for key, label in LOG_FIELD_LABELS:
            src = sources.get(key, "missing")
            if src != "missing":
                self.logger.info("%s: %s", label, src)
