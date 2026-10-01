"""Production Variant Engine — detect options, enumerate combos, validate, dedupe."""

from __future__ import annotations

import itertools
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sentivo_extractor.core.schema import empty_variant
from sentivo_extractor.core.utils import (
    absolute_url,
    canonicalize_option_name,
    normalize_price,
    write_json,
)
from sentivo_extractor.extractors.playwright_extractor import PlaywrightExtractor

SOURCE_NETWORK = "Network"
SOURCE_DOM = "DOM"
SOURCE_PLAYWRIGHT = "Playwright"

MAX_COMBINATIONS = 200


@dataclass
class VariantEngineResult:
    options: list[dict[str, Any]] = field(default_factory=list)
    variants: list[dict[str, Any]] = field(default_factory=list)
    extraction_source: str = ""
    total_combinations: int = 0
    extracted_combinations: int = 0
    skipped_duplicates: int = 0
    unavailable_combinations: int = 0
    failed_combinations: int = 0
    page_variant_count: int | None = None
    count_mismatch: bool = False
    mismatch_note: str = ""

    def to_report(self, *, url: str = "") -> dict[str, Any]:
        return {
            "url": url,
            "extraction_source": self.extraction_source,
            "total_combinations": self.total_combinations,
            "extracted_combinations": self.extracted_combinations,
            "skipped_duplicates": self.skipped_duplicates,
            "unavailable_combinations": self.unavailable_combinations,
            "failed_combinations": self.failed_combinations,
            "page_variant_count": self.page_variant_count,
            "count_mismatch": self.count_mismatch,
            "mismatch_note": self.mismatch_note,
            "options": self.options,
            "variants": self.variants,
        }


def write_variant_report(entries: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        path,
        {
            "schema_version": 1,
            "products": entries,
        },
    )
    return path


def _variant_signature(state: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        re.sub(r"\s+", " ", str(state.get("sku") or "")).strip().lower(),
        str(state.get("price") or "").strip(),
        str(state.get("image") or "").strip().split("?")[0].lower(),
        "1" if state.get("available", True) else "0",
    )


def _combo_key(vals: tuple[str, ...]) -> tuple[str, str, str]:
    v = list(vals) + ["", "", ""]
    return (v[0], v[1], v[2])


class ProductionVariantEngine:
    """Detect variant controls, enumerate valid combinations, dedupe for Shopify."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self._pw = PlaywrightExtractor()

    def process(
        self,
        *,
        url: str,
        product: dict[str, Any] | None = None,
        html: str = "",
        network_json: list[Any] | None = None,
        probe: dict[str, Any] | None = None,
        page: Any | None = None,
    ) -> VariantEngineResult:
        """
        Build production-ready variants. Retries Network when DOM/Playwright incomplete.
        """
        # Shopify JS / product.json already has the complete variant array — trust it.
        if product and self._is_shopify_js_source(product):
            shopify_variants = [
                v for v in (product.get("variants") or []) if isinstance(v, dict)
            ]
            if shopify_variants:
                shopify_options = [
                    o for o in (product.get("options") or [])[:3] if isinstance(o, dict)
                ]
                self.logger.info(
                    "Shopify JS variants: %s extracted", len(shopify_variants)
                )
                return VariantEngineResult(
                    options=shopify_options,
                    variants=list(shopify_variants),
                    extraction_source="Shopify JS",
                    total_combinations=len(shopify_variants),
                    extracted_combinations=len(shopify_variants),
                    page_variant_count=len(shopify_variants),
                    count_mismatch=False,
                    mismatch_note="",
                )

        network_json = network_json or []
        page_count = self._expected_count_from_network(network_json, url)
        if page_count is None and product:
            page_count = self._expected_count_from_product(product)

        dom_result = VariantEngineResult()
        if probe:
            dom_result = self._from_probe(probe, url, SOURCE_PLAYWRIGHT)
        elif page is not None:
            dom_result = self.probe_page(page, url)

        net_result = self._from_network(network_json, url)
        chosen = dom_result
        if net_result.variants:
            if not dom_result.variants or (
                page_count
                and len(dom_result.variants) < page_count
                and len(net_result.variants) >= len(dom_result.variants)
            ):
                chosen = net_result
                self.logger.info(
                    "Variant engine: using Network/XHR (%s variants) — DOM had %s",
                    len(net_result.variants),
                    len(dom_result.variants),
                )

        if not chosen.variants and net_result.variants:
            chosen = net_result

        # Prefer existing product variants (Embedded JSON etc.) when engines empty.
        if (
            not chosen.variants
            and product
            and isinstance(product, dict)
            and product.get("variants")
        ):
            kept_variants = [
                v for v in (product.get("variants") or []) if isinstance(v, dict)
            ]
            if kept_variants:
                chosen = VariantEngineResult(
                    options=[
                        o
                        for o in (product.get("options") or [])[:3]
                        if isinstance(o, dict)
                    ],
                    variants=kept_variants,
                    extraction_source=str(
                        product.get("variant_engine_source")
                        or product.get("extraction_method")
                        or "Embedded JSON"
                    ),
                    total_combinations=len(kept_variants),
                    extracted_combinations=len(kept_variants),
                )

        chosen.page_variant_count = page_count
        # Never run HTML/page variant-count mismatch against Shopify JS sources.
        if (
            product
            and self._is_shopify_js_source(product)
            and chosen.variants
        ):
            chosen.count_mismatch = False
            chosen.mismatch_note = ""
            chosen.page_variant_count = len(chosen.variants)
            self.logger.info(
                "Shopify JS variants: %s extracted", len(chosen.variants)
            )
        elif page_count is not None and len(chosen.variants) != page_count:
            chosen.count_mismatch = True
            chosen.mismatch_note = (
                f"extracted {len(chosen.variants)} vs page variant count {page_count}"
            )
            self.logger.warning(
                "Variant count mismatch for %s: %s",
                url,
                chosen.mismatch_note,
            )
            self.logger.info(
                "Variant mismatch — skipping Playwright variant interaction."
            )
            # Prefer Embedded JSON / existing product variants over incomplete probe.
            if product and isinstance(product, dict) and product.get("variants"):
                kept_options = [
                    o
                    for o in (product.get("options") or chosen.options)
                    if isinstance(o, dict)
                ]
                kept_variants = [
                    v for v in (product.get("variants") or []) if isinstance(v, dict)
                ]
                kept = VariantEngineResult(
                    options=kept_options or list(chosen.options),
                    variants=kept_variants,
                    extraction_source=str(
                        product.get("variant_engine_source")
                        or product.get("extraction_method")
                        or "Embedded JSON"
                    ),
                    total_combinations=chosen.total_combinations,
                    extracted_combinations=len(kept_variants),
                    skipped_duplicates=chosen.skipped_duplicates,
                    page_variant_count=page_count,
                    count_mismatch=True,
                    mismatch_note=chosen.mismatch_note,
                )
                chosen = kept

        chosen.variants = self._dedupe_shopify_variants(chosen.variants)
        chosen.extracted_combinations = len(chosen.variants)
        return chosen

    @staticmethod
    def _is_shopify_js_source(product: dict[str, Any]) -> bool:
        method = str(product.get("extraction_method") or "").lower()
        if "shopify js" in method or method.strip() in {"shopify", "shopify+js"}:
            return True
        if "shopify" in method and "js" in method:
            return True
        sources = product.get("field_sources") or {}
        if isinstance(sources, dict) and sources:
            vals = [str(v) for v in sources.values() if v]
            if vals and all(v == "Shopify JS" for v in vals):
                return True
            if any(v == "Shopify JS" for v in vals):
                # Dominant Shopify JS signal for core commerce fields
                core = ("title", "price", "images", "variant_sku", "variant_price", "variants")
                core_hits = [
                    sources.get(k)
                    for k in core
                    if sources.get(k)
                ]
                if core_hits and all(v == "Shopify JS" for v in core_hits):
                    return True
        return False

    def apply_to_product(
        self, product: dict[str, Any], result: VariantEngineResult
    ) -> dict[str, Any]:
        if result.count_mismatch:
            product["variant_mismatch"] = True
            product["confidence_partial"] = True
            # Keep Embedded JSON variants when mismatch; only fill if product empty.
            if result.options and not product.get("options"):
                product["options"] = result.options[:3]
            if result.variants and not product.get("variants"):
                product["variants"] = result.variants
                product["variant_engine_source"] = result.extraction_source
            product["variant_engine_report"] = result.to_report(
                url=str(product.get("source_url") or "")
            )
            return product
        if result.options:
            product["options"] = result.options[:3]
        if result.variants:
            product["variants"] = result.variants
            product["variant_engine_source"] = result.extraction_source
        product["variant_engine_report"] = result.to_report(
            url=str(product.get("source_url") or "")
        )
        return product

    def probe_page(self, page, url: str = "") -> VariantEngineResult:
        """Render-only: never click variant controls during production crawls."""
        self.logger.info("Skipping Playwright variant interaction (render-only mode).")
        return VariantEngineResult(extraction_source=SOURCE_PLAYWRIGHT)

    def _from_probe(
        self, probe: dict[str, Any], url: str, source: str
    ) -> VariantEngineResult:
        """Normalize an existing playwright probe dict through validation rules."""
        result = VariantEngineResult(extraction_source=source)
        if not isinstance(probe, dict):
            self.logger.error(
                "Variant probe is %s, expected dict — skipping",
                type(probe).__name__,
            )
            return result
        options = probe.get("options") or []
        raw_variants = probe.get("variants") or []
        dict_options = [o for o in options[:3] if isinstance(o, dict)]
        if dict_options:
            result.options = [
                {"name": o.get("name"), "values": o.get("values")} for o in dict_options
            ]
            lists = [o.get("values") or [] for o in dict_options]
            if lists:
                result.total_combinations = len(list(itertools.product(*lists)))

        accepted_sigs: set[tuple[str, str, str, str]] = set()
        accepted_keys: set[tuple[str, str, str]] = set()
        for raw in raw_variants:
            if not isinstance(raw, dict):
                continue
            if raw.get("available") is False:
                result.unavailable_combinations += 1
                continue
            key = _combo_key(
                (
                    str(raw.get("option1") or ""),
                    str(raw.get("option2") or ""),
                    str(raw.get("option3") or ""),
                )
            )
            if key in accepted_keys:
                result.skipped_duplicates += 1
                continue
            state = {
                "sku": raw.get("sku"),
                "price": raw.get("price"),
                "image": raw.get("variant_image"),
                "available": raw.get("available", True),
            }
            sig = _variant_signature(state)
            if sig in accepted_sigs:
                result.skipped_duplicates += 1
                continue
            accepted_sigs.add(sig)
            accepted_keys.add(key)
            v = empty_variant()
            v.update({k: raw.get(k, v.get(k)) for k in v})
            v["price"] = normalize_price(v.get("price"))
            v["compare_at_price"] = normalize_price(v.get("compare_at_price"))
            if v.get("variant_image"):
                v["variant_image"] = absolute_url(url, str(v["variant_image"]))
            result.variants.append(v)

        result.extracted_combinations = len(result.variants)
        return result

    def _from_network(
        self, network_json: list[Any], url: str
    ) -> VariantEngineResult:
        result = VariantEngineResult(extraction_source=SOURCE_NETWORK)
        raw = self._pw._product_from_network(network_json, url)  # noqa: SLF001
        if isinstance(raw, str) or not isinstance(raw, dict):
            if raw is not None:
                self.logger.error(
                    "Network product is %s, expected dict — skipping",
                    type(raw).__name__,
                )
            return result
        result.page_variant_count = len(raw.get("variants") or [])
        result.options = [
            o for o in (raw.get("options") or [])[:3] if isinstance(o, dict)
        ]
        if result.options:
            lists = [o.get("values") or [] for o in result.options]
            if lists:
                result.total_combinations = len(list(itertools.product(*lists)))

        seen_keys: set[tuple[str, str, str]] = set()
        seen_sigs: set[tuple[str, str, str, str]] = set()
        for rv in raw.get("variants") or []:
            if not isinstance(rv, dict):
                continue
            if rv.get("available") is False:
                result.unavailable_combinations += 1
                continue
            key = _combo_key(
                (
                    str(rv.get("option1") or ""),
                    str(rv.get("option2") or ""),
                    str(rv.get("option3") or ""),
                )
            )
            if key in seen_keys:
                result.skipped_duplicates += 1
                continue
            sig = _variant_signature(
                {
                    "sku": rv.get("sku"),
                    "price": rv.get("price"),
                    "image": rv.get("variant_image"),
                    "available": rv.get("available", True),
                }
            )
            if sig in seen_sigs:
                result.skipped_duplicates += 1
                continue
            seen_keys.add(key)
            seen_sigs.add(sig)
            v = empty_variant()
            v.update({k: rv.get(k, v.get(k)) for k in v})
            v["price"] = normalize_price(v.get("price"))
            v["compare_at_price"] = normalize_price(v.get("compare_at_price"))
            result.variants.append(v)

        result.extracted_combinations = len(result.variants)
        return result

    def _expected_count_from_network(
        self, network_json: list[Any], url: str
    ) -> int | None:
        raw = self._pw._product_from_network(network_json, url)  # noqa: SLF001
        if not isinstance(raw, dict):
            return None
        variants = raw.get("variants") or []
        return len(variants) if variants else None

    @staticmethod
    def _expected_count_from_product(product: dict[str, Any]) -> int | None:
        meta = product.get("page_variant_count")
        if isinstance(meta, int):
            return meta
        variants = product.get("variants") or []
        if len(variants) > 1:
            return len(variants)
        return None

    @staticmethod
    def _dedupe_shopify_variants(
        variants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        seen_keys: set[tuple[str, str, str]] = set()
        out: list[dict[str, Any]] = []
        for v in variants:
            key = _combo_key(
                (
                    str(v.get("option1") or ""),
                    str(v.get("option2") or ""),
                    str(v.get("option3") or ""),
                )
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append(v)
        return out

    def _detect_axes_playwright(self, page) -> list[dict[str, Any]]:
        try:
            axes = page.evaluate(
                """() => {
                const axes = [];
                const pushAxis = (name, values, kind) => {
                  const clean = values.map(v => (v||'').trim()).filter(Boolean);
                  const uniq = [...new Set(clean)].slice(0, 30);
                  if (uniq.length) axes.push({name, values: uniq, kind});
                };
                document.querySelectorAll(
                  "form select, .variations select, select[name*='attribute'], select[name*='option']"
                ).forEach(sel => {
                  let name = sel.getAttribute('name') || sel.getAttribute('aria-label') || 'Option';
                  name = name.replace(/attribute_pa_|attribute_|option_?/ig,'').replace(/[_-]/g,' ').trim();
                  const vals = [];
                  sel.querySelectorAll('option').forEach(o => {
                    const t = (o.innerText||o.value||'').trim();
                    if (!t || /^choose/i.test(t) || t.toLowerCase()==='select') return;
                    if (o.disabled) return;
                    vals.push(t);
                  });
                  pushAxis(name || 'Option', vals, 'select');
                });
                const radios = {};
                document.querySelectorAll("input[type=radio]").forEach(r => {
                  if (r.disabled) return;
                  const name = r.getAttribute('name') || 'Option';
                  const lab = document.querySelector('label[for="'+r.id+'"]');
                  const val = (lab ? lab.innerText : (r.value||'')).trim();
                  if (!val) return;
                  radios[name] = radios[name] || [];
                  radios[name].push(val);
                });
                Object.entries(radios).forEach(([n,v]) => pushAxis(n, v, 'radio'));
                document.querySelectorAll(
                  "[class*='swatch'], [role='radiogroup'], .product-form__input, fieldset, "
                  + "[class*='size'], [class*='color'], [class*='material'], [class*='thickness']"
                ).forEach(group => {
                  if (axes.length >= 3) return;
                  const legend = group.querySelector('legend, label, .form__label, .label');
                  let name = legend ? (legend.innerText||'').trim() : 'Option';
                  const vals = [];
                  group.querySelectorAll(
                    "button, [role='radio'], a[class*='swatch'], span[class*='swatch'], label"
                  ).forEach(btn => {
                    const disabled = btn.disabled || btn.getAttribute('aria-disabled')==='true'
                      || /disabled|sold-?out|unavailable/i.test(btn.className||'');
                    if (disabled) return;
                    const t = (btn.innerText||btn.getAttribute('data-value')||btn.getAttribute('title')||'').trim();
                    if (t && t.length < 60) vals.push(t);
                  });
                  pushAxis(name || 'Option', vals, 'swatch');
                });
                return axes.slice(0, 3);
            }"""
            )
            return axes if isinstance(axes, list) else []
        except Exception:
            return []

    def _select_combo(
        self, page, options: list[dict[str, Any]], combo: tuple
    ) -> None:
        self._pw._select_combo(page, options, combo)  # noqa: SLF001

    def _read_variant_state(self, page, base_url: str) -> dict[str, Any]:
        state = page.evaluate(
            """() => {
            const priceEl = document.querySelector(
              '[itemprop=price], .price, [class*=price], [class*=Price]'
            );
            const compareEl = document.querySelector(
              'del, s, [class*=compare], [class*=was-price]'
            );
            const skuEl = document.querySelector(
              '[itemprop=sku], .sku, [class*=sku]'
            );
            const imgEl = document.querySelector(
              '.product-image img, [class*=gallery] img, main img'
            );
            const weightEl = document.querySelector(
              '[itemprop=weight], [class*=weight]'
            );
            const unavailable = !!(
              document.querySelector('.sold-out, .unavailable, [class*=sold-out]')
              || document.querySelector('button[disabled].add-to-cart, button[disabled][name*=add]')
              || document.querySelector('[data-available=false]')
            );
            return {
              price: priceEl ? (priceEl.getAttribute('content') || priceEl.innerText || '') : '',
              compare_at_price: compareEl ? (compareEl.innerText || '') : '',
              sku: skuEl ? (skuEl.innerText || '').replace(/^sku\\s*[:#]?\\s*/i,'') : '',
              barcode: '',
              image: imgEl ? (imgEl.getAttribute('src') || imgEl.getAttribute('data-src') || '') : '',
              weight_grams: weightEl ? (weightEl.innerText || weightEl.getAttribute('content') || '') : '',
              available: !unavailable
            };
        }"""
        )
        if isinstance(state, dict) and state.get("image"):
            state["image"] = absolute_url(base_url, str(state["image"]))
        return state if isinstance(state, dict) else {}
