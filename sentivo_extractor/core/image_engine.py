"""Production Image Engine — multi-source collection, normalize, verify, variant map."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from bs4 import BeautifulSoup

from sentivo_extractor.core.image_checker import verify_image_url
from sentivo_extractor.core.image_pipeline import prefer_largest_srcset, unwrap_next_image
from sentivo_extractor.core.utils import absolute_url, write_json
from sentivo_extractor.core.woocommerce_images import (
    WOO_MAX_IMAGES,
    apply_woocommerce_image_cap,
    extract_woocommerce_gallery_images,
    filter_woocommerce_jsonld_images,
    resolve_woocommerce_product_images,
)
from sentivo_extractor.core.woocommerce_title import looks_like_woocommerce_url
from sentivo_extractor.extractors.jsonld_extractor import JsonLdExtractor

SOURCE_DOM = "DOM"
SOURCE_JSON = "JSON"
SOURCE_JSONLD = "JSON-LD"
SOURCE_NETWORK = "Network"
SOURCE_PLAYWRIGHT = "Playwright"

RETRY_SOURCE_ORDER = (SOURCE_DOM, SOURCE_JSON, SOURCE_NETWORK, SOURCE_PLAYWRIGHT)

# Hard chrome / tracker assets only — do NOT match bare "logo"/"icon" substrings
# (WooCommerce CDN paths often contain those words in product filenames).
_HARD_JUNK_PATH = re.compile(
    r"(favicon|sprite|placeholder|blank\.|1x1|pixel\.|spacer|"
    r"trustpilot|klarna|paypal[-_]?mark|visa[-_]?card|mastercard|"
    r"loading\.gif|spinner\.|/avatar/|profile-pic)",
    re.I,
)
# Path-segment chrome (filename or directory), not arbitrary substrings.
_CHROME_ASSET_PATH = re.compile(
    r"/(?:logos?|icons?|badges?)/(?:payment|pay|cart|search|menu|social)?|"
    r"/(?:logo|icon|badge)(?:[-_.][^/]*)?\.(?:png|jpe?g|gif|webp|svg)(?:$|\?)|"
    r"[-_](?:site[-_]?logo|header[-_]?logo|footer[-_]?logo)\.|"
    r"/payment[-_](?:icon|logo|badge)",
    re.I,
)
# Soft-reject reasons that should not drop JSON-LD product images.
_SOFT_VERIFY_ERRORS = frozenset(
    {
        "empty response",
        "empty_body",
        "not_image_data",
        "too_small",
    }
)
_SIZE_IN_PATH = re.compile(
    r"(_|-)(\d{2,4})x(\d{2,4})(?=\.|/|$)|"
    r"/(\d{2,4})x(\d{2,4})/|"
    r"[_-](small|medium|large|thumb|mini|tiny)(?=\.|/|$)",
    re.I,
)
_STRIP_QUERY_KEYS = frozenset(
    {
        "w",
        "h",
        "width",
        "height",
        "resize",
        "fit",
        "crop",
        "quality",
        "q",
        "auto",
        "format",
        "dpr",
    }
)


@dataclass
class ImageCandidate:
    url: str
    source: str
    alt: str = ""
    width_hint: int = 0
    variant_key: str = ""


@dataclass
class ImageEngineResult:
    images: list[dict[str, Any]] = field(default_factory=list)
    variant_mappings: list[dict[str, Any]] = field(default_factory=list)
    total_found: int = 0
    unique_count: int = 0
    duplicates_removed: int = 0
    broken_images: list[dict[str, Any]] = field(default_factory=list)
    sources_used: dict[str, int] = field(default_factory=dict)
    primary_source: str = ""
    verified: bool = True

    def to_report(self, *, url: str = "") -> dict[str, Any]:
        return {
            "url": url,
            "total_images_found": self.total_found,
            "unique_images": self.unique_count,
            "duplicates_removed": self.duplicates_removed,
            "broken_images": self.broken_images,
            "variant_image_mappings": self.variant_mappings,
            "extraction_sources": self.sources_used,
            "primary_extraction_source": self.primary_source,
            "verified": self.verified,
            "images": self.images,
        }


def write_image_report(entries: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "total_images_found": sum(int(e.get("total_images_found") or 0) for e in entries),
        "unique_images": sum(int(e.get("unique_images") or 0) for e in entries),
        "duplicates_removed": sum(int(e.get("duplicates_removed") or 0) for e in entries),
        "broken_images": sum(len(e.get("broken_images") or []) for e in entries),
    }
    write_json(
        path,
        {
            "schema_version": 1,
            "summary": summary,
            "products": entries,
        },
    )
    return path


def normalize_image_url(raw: str, base_url: str) -> str:
    if not raw or raw.startswith("data:"):
        return ""
    url = unwrap_next_image(str(raw).strip(), base_url)
    url = absolute_url(base_url, url)
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {k: v for k, v in qs.items() if k.lower() not in _STRIP_QUERY_KEYS}
        flat_qs = []
        for k in sorted(filtered):
            for val in filtered[k]:
                flat_qs.append(f"{k}={val}")
        query = "&".join(flat_qs)
        path = _SIZE_IN_PATH.sub("", parsed.path)
        url = urlunparse(
            (parsed.scheme, parsed.netloc, path, parsed.params, query, parsed.fragment)
        )
    except Exception:
        pass
    return url.rstrip("?&")


def dedupe_key(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.lower())
    path = _SIZE_IN_PATH.sub("", parsed.path)
    path = re.sub(r"[-_](\d{2,4})w(?=\.|$)", "", path)
    return f"{parsed.netloc}{path}"


def is_navigation_image_url(url: str) -> bool:
    """True for Magento theme/menu (and other non-catalog /media/) assets."""
    if not url:
        return False
    low = url.lower()
    if "/theme/menu/" in low:
        return True
    # Magento: product images live under /media/catalog/product/
    if "/media/" in low and "/media/catalog/product/" not in low:
        # Allow common Woo/WP media upload trees mistaken for Magento /media/
        if "/wp-content/" in low or "/uploads/" in low:
            return False
        return True
    return False


def image_reject_reason(
    url: str,
    alt: str = "",
    *,
    source: str = "",
    soft: bool = False,
) -> str | None:
    """
    Return a rejection reason, or None if the URL should be kept.

    JSON-LD / structured product sources use soft=True so CDN paths containing
    'logo'/'icon' substrings are not false-rejected.
    """
    if not url:
        return "empty_url"
    if url.startswith("data:"):
        return "data_uri"
    low = url.lower()
    if is_navigation_image_url(url):
        return "navigation_or_theme_media"
    if _HARD_JUNK_PATH.search(low):
        return f"hard_junk_path:{_HARD_JUNK_PATH.search(low).group(0)}"
    alt_low = (alt or "").lower().strip()
    if alt_low in {"logo", "icon", "site logo", "company logo"}:
        return f"chrome_alt:{alt_low}"
    if soft:
        # Structured product images: only hard chrome filenames.
        if _CHROME_ASSET_PATH.search(low) and SOURCE_JSONLD not in (source or ""):
            # Still allow if path also looks like a product upload (wp-content/uploads)
            if "/uploads/" not in low and "/catalog/product/" not in low:
                return f"chrome_asset_path:{_CHROME_ASSET_PATH.search(low).group(0)}"
        return None
    if _CHROME_ASSET_PATH.search(low):
        # WooCommerce uploads often include brand words — keep /uploads/ and catalog.
        if "/uploads/" in low or "/catalog/product/" in low or "/product" in low:
            return None
        return f"chrome_asset_path:{_CHROME_ASSET_PATH.search(low).group(0)}"
    parsed = urlparse(low)
    if parsed.path.endswith(".svg"):
        stem = parsed.path.rsplit("/", 1)[-1]
        if stem.startswith(("logo", "icon", "badge")) or stem in {
            "logo.svg",
            "icon.svg",
        }:
            return "chrome_svg"
    return None


def is_junk_image_url(url: str, alt: str = "", *, source: str = "") -> bool:
    soft = (source or "") in {SOURCE_JSONLD, SOURCE_JSON, "JSON-LD", "JSON"}
    return image_reject_reason(url, alt, source=source, soft=soft) is not None


def _reject_at_collect(url: str, alt: str = "", source: str = "") -> bool:
    """Skip hard junk early; keep nav URLs for finalize count+drop."""
    if not url:
        return True
    if is_navigation_image_url(url):
        return False
    soft = (source or "") in {SOURCE_JSONLD, SOURCE_JSON, "JSON-LD", "JSON"}
    reason = image_reject_reason(url, alt, source=source, soft=soft)
    return reason is not None


def _log_image_rejected(
    logger: logging.Logger | None, url: str, reason: str
) -> None:
    log = logger or logging.getLogger(__name__)
    log.info("Image rejected: %s — reason: %s", url, reason)


def _width_hint_from_url(url: str) -> int:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("width", "w"):
        for val in qs.get(key) or []:
            try:
                return int(val)
            except ValueError:
                continue
    m = re.search(r"(\d{2,4})x(\d{2,4})", parsed.path)
    if m:
        try:
            return max(int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    m = re.search(r"[-_](\d{3,4})w(?=\.|$)", parsed.path, re.I)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return len(parsed.path)


class ProductionImageEngine:
    """Collect, normalize, dedupe, verify, and map product + variant images."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        *,
        verify: bool = True,
        min_width: int = 50,
        min_height: int = 50,
        session: Any | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.verify = verify
        self.min_width = min_width
        self.min_height = min_height
        self.session = session
        self.timeout = timeout

    def process(
        self,
        *,
        url: str,
        product: dict[str, Any],
        html: str = "",
        network_json: list[Any] | None = None,
        rendered_html: str = "",
    ) -> ImageEngineResult:
        base = url
        network_json = network_json or []
        buckets = self._collect_buckets(
            url=url,
            html=html,
            rendered_html=rendered_html or html,
            network_json=network_json,
            product=product,
        )

        all_candidates: list[ImageCandidate] = []
        for src, items in buckets.items():
            all_candidates.extend(items)

        result = self._finalize(all_candidates, product, base)
        if result.images:
            return result

        for source in RETRY_SOURCE_ORDER:
            retry_items = buckets.get(source) or []
            if not retry_items:
                continue
            retry_result = self._finalize(retry_items, product, base)
            if retry_result.images:
                retry_result.primary_source = source
                return retry_result

        return result

    @staticmethod
    def _is_woocommerce_product(product: dict[str, Any], url: str = "") -> bool:
        platform = str(product.get("platform") or "")
        method = str(product.get("extraction_method") or "").lower()
        src = str(product.get("source_url") or url or "")
        return (
            platform == "WooCommerce"
            or "woocommerce" in method
            or looks_like_woocommerce_url(src)
        )

    def apply_to_product(
        self, product: dict[str, Any], result: ImageEngineResult
    ) -> dict[str, Any]:
        if not isinstance(product, dict):
            return product
        prior = list(product.get("images") or [])
        is_woo = self._is_woocommerce_product(product)
        if result.images:
            product["images"] = list(result.images)
        elif prior:
            # Never wipe JSON-LD / extractor images when the filter over-rejects.
            # WooCommerce: prior must already be gallery-filtered (never restore 25+ junk).
            self.logger.warning(
                "Image engine returned 0 verified images — keeping %s prior image(s)",
                len(prior),
            )
            product["images"] = prior
        else:
            product["images"] = []
        if is_woo:
            apply_woocommerce_image_cap(product, max_images=WOO_MAX_IMAGES)
        product["image_engine_report"] = result.to_report(
            url=product.get("source_url") or ""
        )
        by_src = {m["normalized_src"]: m for m in result.variant_mappings}
        for idx, variant in enumerate(product.get("variants") or []):
            if not isinstance(variant, dict):
                continue
            raw = str(variant.get("variant_image") or "").strip()
            if not raw:
                continue
            norm = normalize_image_url(raw, product.get("source_url") or "")
            mapping = by_src.get(norm) or by_src.get(dedupe_key(norm))
            if mapping and mapping.get("verified_src"):
                variant["variant_image"] = mapping["verified_src"]
            elif norm and any(
                isinstance(i, dict) and i.get("src") == norm for i in result.images
            ):
                variant["variant_image"] = norm
        return product

    def _collect_buckets(
        self,
        *,
        url: str,
        html: str,
        rendered_html: str,
        network_json: list[Any],
        product: dict[str, Any],
    ) -> dict[str, list[ImageCandidate]]:
        is_woo = self._is_woocommerce_product(product, url)
        page_html = rendered_html or html

        if is_woo:
            # Gallery-first only — never vacuum related/banner/blog images.
            gallery = extract_woocommerce_gallery_images(
                page_html or html, url, max_images=WOO_MAX_IMAGES
            )
            dom = [
                ImageCandidate(
                    url=str(img.get("src") or ""),
                    source=SOURCE_DOM,
                    alt=str(img.get("alt") or ""),
                    width_hint=_width_hint_from_url(str(img.get("src") or "")),
                )
                for img in gallery
                if img.get("src")
            ]
            pw_dom = [
                ImageCandidate(
                    url=c.url,
                    source=SOURCE_PLAYWRIGHT,
                    alt=c.alt,
                    width_hint=c.width_hint,
                )
                for c in dom
            ]
            # Embedded product images already filtered upstream; still re-filter.
            resolve_woocommerce_product_images(
                product, page_html or html, logger=self.logger, force=True
            )
            embedded = self._collect_embedded(product, html, url)[:WOO_MAX_IMAGES]
            json_ld = self._collect_jsonld(html, url, woo=True, product=product)
            # Prefer gallery; if empty keep at most first JSON-LD image.
            if dom:
                json_bucket = []
                network = []
            else:
                json_bucket = (json_ld[:1] + embedded[:1])[:1]
                network = []
            variant_cands = self._collect_variant_images(product, url)
            return {
                SOURCE_DOM: dom + variant_cands,
                SOURCE_JSON: json_bucket,
                SOURCE_NETWORK: network,
                SOURCE_PLAYWRIGHT: pw_dom,
            }

        dom = self._collect_dom(html, url)
        json_ld = self._collect_jsonld(html, url)
        embedded = self._collect_embedded(product, html, url)
        json_bucket = json_ld + embedded
        network = self._collect_network(network_json, url, product)
        pw_dom = self._collect_dom(rendered_html, url) if rendered_html != html else []
        for c in pw_dom:
            c.source = SOURCE_PLAYWRIGHT

        variant_cands = self._collect_variant_images(product, url)

        return {
            SOURCE_DOM: dom + variant_cands,
            SOURCE_JSON: json_bucket,
            SOURCE_NETWORK: network,
            SOURCE_PLAYWRIGHT: pw_dom,
        }

    def _collect_dom(self, html: str, base_url: str) -> list[ImageCandidate]:
        out: list[ImageCandidate] = []
        if not html:
            return out
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            return out

        selectors = [
            ".product-gallery img",
            ".product__media img",
            "[data-product-gallery] img",
            ".woocommerce-product-gallery__image img",
            ".woocommerce-product-gallery img",
            'img[itemprop="image"]',
            ".product-image img",
            "picture source",
            "picture img",
        ]
        seen: set[str] = set()
        for sel in selectors:
            for node in soup.select(sel):
                src = ""
                width_hint = 0
                if node.name == "source":
                    srcset = node.get("srcset") or ""
                    src = prefer_largest_srcset(srcset, base_url)
                else:
                    srcset = node.get("srcset") or node.get("data-srcset") or ""
                    if srcset:
                        src = prefer_largest_srcset(srcset, base_url)
                        width_hint = _width_hint_from_url(src)
                    if not src:
                        src = (
                            node.get("data-src")
                            or node.get("data-lazy-src")
                            or node.get("src")
                            or ""
                        )
                        src = unwrap_next_image(src, base_url)
                src = normalize_image_url(src, base_url)
                if not src or src in seen:
                    continue
                alt = str(node.get("alt") or "")
                reason = image_reject_reason(src, alt, source=SOURCE_DOM, soft=False)
                if reason:
                    _log_image_rejected(self.logger, src, reason)
                    continue
                seen.add(src)
                out.append(
                    ImageCandidate(
                        url=src,
                        source=SOURCE_DOM,
                        alt=alt,
                        width_hint=width_hint or _width_hint_from_url(src),
                    )
                )
        return out

    def _collect_jsonld(
        self,
        html: str,
        base_url: str,
        *,
        woo: bool = False,
        product: dict[str, Any] | None = None,
    ) -> list[ImageCandidate]:
        out: list[ImageCandidate] = []
        if not html:
            return out
        ctx = {"platform": "WooCommerce"} if woo else None
        raw = JsonLdExtractor().extract(base_url, html, context=ctx)
        if not isinstance(raw, dict):
            return out
        images = list(raw.get("images") or [])
        if woo:
            images = filter_woocommerce_jsonld_images(
                images,
                url=base_url,
                title=str((product or {}).get("title") or raw.get("title") or ""),
            )
        for img in images:
            if isinstance(img, dict):
                src = normalize_image_url(str(img.get("src") or ""), base_url)
                alt = str(img.get("alt") or "")
            elif isinstance(img, str):
                src = normalize_image_url(img, base_url)
                alt = ""
            else:
                continue
            if not src:
                continue
            # JSON-LD product images: never drop for logo/icon substring false-positives.
            reason = image_reject_reason(
                src, alt, source=SOURCE_JSONLD, soft=True
            )
            if reason:
                _log_image_rejected(self.logger, src, reason)
                continue
            out.append(
                ImageCandidate(
                    url=src,
                    source=SOURCE_JSONLD,
                    alt=alt,
                    width_hint=_width_hint_from_url(src),
                )
            )
        return out

    def _collect_embedded(
        self, product: dict[str, Any], html: str, base_url: str
    ) -> list[ImageCandidate]:
        out: list[ImageCandidate] = []
        if not isinstance(product, dict):
            product = {}
        for img in product.get("images") or []:
            if isinstance(img, dict):
                src = normalize_image_url(str(img.get("src") or ""), base_url)
                alt = str(img.get("alt") or "")
            elif isinstance(img, str):
                src = normalize_image_url(img, base_url)
                alt = ""
            else:
                continue
            if not src:
                continue
            reason = image_reject_reason(
                src, alt, source=SOURCE_JSON, soft=True
            )
            if reason:
                _log_image_rejected(self.logger, src, reason)
                continue
            out.append(
                ImageCandidate(
                    url=src,
                    source=SOURCE_JSON,
                    alt=alt,
                    width_hint=_width_hint_from_url(src),
                )
            )

        blobs: list[Any] = []
        try:
            soup = BeautifulSoup(html or "", "lxml")
            for script in soup.find_all("script"):
                text = (script.string or script.get_text() or "").strip()
                if not text or "image" not in text.lower():
                    continue
                if script.get("type") == "application/ld+json":
                    continue
                try:
                    data = json.loads(text)
                    blobs.append(data)
                except Exception:
                    for m in re.finditer(r'"images?"\s*:\s*(\[[^\]]+\])', text):
                        try:
                            blobs.append({"images": json.loads(m.group(1))})
                        except Exception:
                            pass
        except Exception:
            pass

        for blob in blobs:
            out.extend(self._images_from_json_node(blob, base_url, SOURCE_JSON))
        return out

    def _collect_network(
        self,
        network_json: list[Any],
        base_url: str,
        product: dict[str, Any],
    ) -> list[ImageCandidate]:
        out: list[ImageCandidate] = []
        for payload in network_json:
            if isinstance(payload, dict):
                out.extend(self._images_from_json_node(payload, base_url, SOURCE_NETWORK))
            elif isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        out.extend(
                            self._images_from_json_node(item, base_url, SOURCE_NETWORK)
                        )
        return out

    def _images_from_json_node(
        self, node: Any, base_url: str, source: str, depth: int = 0
    ) -> list[ImageCandidate]:
        if depth > 8 or node is None:
            return []
        out: list[ImageCandidate] = []
        if isinstance(node, dict):
            if "images" in node:
                imgs = node.get("images")
                if isinstance(imgs, list):
                    for item in imgs:
                        if isinstance(item, str):
                            src = normalize_image_url(item, base_url)
                            if not src:
                                continue
                            reason = image_reject_reason(
                                src, source=source, soft=True
                            )
                            if reason:
                                _log_image_rejected(None, src, reason)
                                continue
                            out.append(
                                ImageCandidate(
                                    url=src,
                                    source=source,
                                    width_hint=_width_hint_from_url(src),
                                )
                            )
                        elif isinstance(item, dict):
                            src = normalize_image_url(
                                str(item.get("src") or item.get("url") or ""),
                                base_url,
                            )
                            alt = str(item.get("alt") or "")
                            if not src:
                                continue
                            reason = image_reject_reason(
                                src, alt, source=source, soft=True
                            )
                            if reason:
                                _log_image_rejected(None, src, reason)
                                continue
                            out.append(
                                ImageCandidate(
                                    url=src,
                                    source=source,
                                    alt=alt,
                                    width_hint=_width_hint_from_url(src),
                                )
                            )
            if "image" in node:
                img = node.get("image")
                urls: list[str] = []
                if isinstance(img, str):
                    urls = [img]
                elif isinstance(img, list):
                    urls = [str(u) for u in img if u]
                elif isinstance(img, dict):
                    u = img.get("url") or img.get("contentUrl")
                    if u:
                        urls = [str(u)]
                for u in urls:
                    src = normalize_image_url(u, base_url)
                    if not src:
                        continue
                    reason = image_reject_reason(src, source=source, soft=True)
                    if reason:
                        _log_image_rejected(None, src, reason)
                        continue
                    out.append(
                        ImageCandidate(
                            url=src,
                            source=source,
                            width_hint=_width_hint_from_url(src),
                        )
                    )
            for v in node.get("variants") or []:
                if isinstance(v, dict):
                    vi = v.get("image") or v.get("featured_image") or v.get("variant_image")
                    if isinstance(vi, dict):
                        vi = vi.get("src") or vi.get("url")
                    src = normalize_image_url(str(vi or ""), base_url)
                    if not src:
                        continue
                    reason = image_reject_reason(src, source=source, soft=True)
                    if reason:
                        _log_image_rejected(None, src, reason)
                        continue
                    key = str(v.get("sku") or v.get("id") or "")
                    out.append(
                        ImageCandidate(
                            url=src,
                            source=source,
                            variant_key=key,
                            width_hint=_width_hint_from_url(src),
                        )
                    )
            for val in node.values():
                if isinstance(val, (dict, list)):
                    out.extend(
                        self._images_from_json_node(val, base_url, source, depth + 1)
                    )
        elif isinstance(node, list):
            for item in node:
                out.extend(self._images_from_json_node(item, base_url, source, depth + 1))
        return out

    def _collect_variant_images(
        self, product: dict[str, Any], base_url: str
    ) -> list[ImageCandidate]:
        out: list[ImageCandidate] = []
        for v in product.get("variants") or []:
            if not isinstance(v, dict):
                continue
            src = normalize_image_url(str(v.get("variant_image") or ""), base_url)
            if not src:
                continue
            reason = image_reject_reason(src, source=SOURCE_DOM, soft=False)
            if reason:
                _log_image_rejected(self.logger, src, reason)
                continue
            out.append(
                ImageCandidate(
                    url=src,
                    source=SOURCE_DOM,
                    variant_key=str(v.get("sku") or ""),
                    width_hint=_width_hint_from_url(src),
                )
            )
        return out

    def _finalize(
        self,
        candidates: list[ImageCandidate],
        product: dict[str, Any],
        base_url: str,
    ) -> ImageEngineResult:
        total = len(candidates)
        sources_used: dict[str, int] = {}
        for c in candidates:
            sources_used[c.source] = sources_used.get(c.source, 0) + 1

        nav_filtered = sum(
            1 for c in candidates if c.url and is_navigation_image_url(c.url)
        )
        if nav_filtered:
            self.logger.info("Navigation image filtered: %s", nav_filtered)

        filtered: list[ImageCandidate] = []
        for c in candidates:
            if not c.url:
                continue
            reason = image_reject_reason(
                c.url,
                c.alt,
                source=c.source,
                soft=c.source in {SOURCE_JSONLD, SOURCE_JSON},
            )
            if reason:
                _log_image_rejected(self.logger, c.url, reason)
                continue
            filtered.append(c)

        groups: dict[str, list[ImageCandidate]] = {}
        for c in filtered:
            key = dedupe_key(c.url)
            groups.setdefault(key, []).append(c)

        duplicates_removed = max(0, len(filtered) - len(groups))
        picked: list[ImageCandidate] = []
        for group in groups.values():
            best = max(
                group,
                key=lambda x: (x.width_hint, len(x.url)),
            )
            picked.append(best)

        picked.sort(key=lambda x: (-x.width_hint, x.url))
        verified_images: list[dict[str, Any]] = []
        broken: list[dict[str, Any]] = []
        verified_urls: dict[str, str] = {}

        for i, cand in enumerate(picked, start=1):
            check_url = cand.url
            ok = True
            soft_keep = False
            verify_meta: dict[str, Any] = {}
            if self.verify:
                verify_meta = verify_image_url(
                    check_url,
                    session=self.session,
                    timeout=self.timeout,
                    min_width=self.min_width,
                    min_height=self.min_height,
                )
                ok = bool(verify_meta.get("ok"))
                if verify_meta.get("final_url"):
                    check_url = str(verify_meta["final_url"])
                if not ok:
                    err = str(verify_meta.get("error") or "verify_failed")
                    err_l = err.lower()
                    # Structured product images + proxy/dimension failures: keep URL.
                    soft_keep = cand.source in {
                        SOURCE_JSONLD,
                        SOURCE_JSON,
                    } or any(
                        token in err_l
                        for token in (
                            "proxy",
                            "timeout",
                            "timed out",
                            "connection",
                            "empty response",
                            "empty_body",
                            "too_small",
                            "not_image_data",
                        )
                    )
                    if soft_keep:
                        self.logger.warning(
                            "Image verify soft-keep (proxy/dimensions): %s — reason: %s",
                            cand.url,
                            err,
                        )
                        ok = True
                    else:
                        _log_image_rejected(
                            self.logger, cand.url, f"verify_failed:{err}"
                        )
            if ok:
                verified_urls[cand.url] = check_url
                verified_images.append(
                    {
                        "src": check_url,
                        "alt": cand.alt,
                        "position": len(verified_images) + 1,
                        "source": cand.source,
                        "verify_soft_kept": soft_keep,
                    }
                )
            else:
                broken.append(
                    {
                        "url": cand.url,
                        "source": cand.source,
                        "error": verify_meta.get("error") or "verify_failed",
                    }
                )

        variant_mappings = self._map_variants(product, base_url, verified_urls, picked)

        primary = ""
        if verified_images:
            primary = str(verified_images[0].get("source") or "")

        return ImageEngineResult(
            images=verified_images,
            variant_mappings=variant_mappings,
            total_found=total,
            unique_count=len(verified_images),
            duplicates_removed=duplicates_removed,
            broken_images=broken,
            sources_used=sources_used,
            primary_source=primary,
            verified=self.verify,
        )

    def _map_variants(
        self,
        product: dict[str, Any],
        base_url: str,
        verified_urls: dict[str, str],
        picked: list[ImageCandidate],
    ) -> list[dict[str, Any]]:
        mappings: list[dict[str, Any]] = []
        url_to_source = {c.url: c.source for c in picked}
        for idx, variant in enumerate(product.get("variants") or []):
            if not isinstance(variant, dict):
                continue
            raw = str(variant.get("variant_image") or "").strip()
            norm = normalize_image_url(raw, base_url) if raw else ""
            verified_src = verified_urls.get(norm) or verified_urls.get(dedupe_key(norm), "")
            source = url_to_source.get(norm, SOURCE_DOM)
            for c in picked:
                if dedupe_key(c.url) == dedupe_key(norm):
                    source = c.source
                    break
            mappings.append(
                {
                    "variant_index": idx,
                    "sku": str(variant.get("sku") or ""),
                    "option1": str(variant.get("option1") or ""),
                    "raw_src": raw,
                    "normalized_src": norm,
                    "verified_src": verified_src,
                    "source": source,
                }
            )
            if norm and not verified_src:
                for c in picked:
                    if dedupe_key(c.url) == dedupe_key(norm) and c.url in verified_urls:
                        mappings[-1]["verified_src"] = verified_urls[c.url]
                        break
        return mappings
