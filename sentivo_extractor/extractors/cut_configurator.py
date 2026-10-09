"""
Parse Sheet Plastics CutConfiguratorWidget (Vue) store into structured pricing.

Source (Playwright page.evaluate):
  CutConfiguratorWidget.vm.$store.state.Configurator.confData.entities
plus selectedPathWay / sizes on the same Configurator state.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sentivo_extractor.core.utils import format_cut_to_size_price, normalize_price

logger = logging.getLogger(__name__)

# Runs in the browser after networkidle — returns compact JSON (not the 3MB tree).
CUT_CONFIGURATOR_EVAL_JS = """() => {
  try {
    const w = window.CutConfiguratorWidget;
    if (!w || !w.vm || !w.vm.$store) return null;
    const conf = w.vm.$store.state.Configurator;
    if (!conf || !conf.confData) return null;
    const entities = conf.confData.entities || [];
    const path = conf.selectedPathWay || [];
    const sizes = conf.sizes || {};

    function typeCode(n) {
      return (n && n.entityTypeMetadata && n.entityTypeMetadata.code) || "";
    }
    function walkNodes(nodes, out) {
      if (!Array.isArray(nodes)) return;
      for (const n of nodes) {
        out.push(n);
        if (n && n.nodes) walkNodes(n.nodes, out);
      }
    }

    const flat = [];
    walkNodes(path, flat);
    // Fallback: walk entity trees via .nodes
    if (!flat.length) {
      for (const root of entities) walkNodes([root], flat);
    }

    let material = null, colour = null, thickness = null;
    let polishingPrice = null, holesPrice = null;
    for (const n of flat) {
      if (!n) continue;
      const tc = typeCode(n);
      const code = String(n.code || "").toUpperCase();
      const title = String(n.title || "");
      if (tc === "MATERIAL" || (!material && n.parentId == null && tc)) {
        if (tc === "MATERIAL") material = n;
      }
      if (tc === "MATERIAL_TYPE") colour = n;
      if (tc === "MATERIAL_THICKNESS") thickness = n;
      if (code === "POLISHED" || /polish/i.test(title)) {
        if (polishingPrice == null && n.price != null) polishingPrice = n.price;
      }
      if (code === "HOLES" || /^holes$/i.test(title)) {
        if (holesPrice == null && n.price != null) holesPrice = n.price;
      }
    }

    // Prefer leaf thickness on selected path (last MATERIAL_THICKNESS).
    for (let i = flat.length - 1; i >= 0; i--) {
      if (typeCode(flat[i]) === "MATERIAL_THICKNESS") {
        thickness = flat[i];
        break;
      }
    }

    const pricePerSqm = thickness && thickness.entityUsePrice
      ? Number(thickness.price)
      : (thickness ? Number(thickness.price) : null);

    const minWidth = Number(
      (thickness && thickness.minWidth) || sizes.minWidth || 0
    ) || null;
    const maxWidth = Number(
      (thickness && thickness.maxWidth) || sizes.maxWidth || 0
    ) || null;
    const minLength = Number(
      (thickness && thickness.minLength) || sizes.minLength || 0
    ) || null;
    const maxLength = Number(
      (thickness && thickness.maxLength) || sizes.maxLength || 0
    ) || null;

    // If polishing/holes missing from path, scan material.nodes
    if ((polishingPrice == null || holesPrice == null) && material && material.nodes) {
      const extras = [];
      walkNodes(material.nodes, extras);
      for (const n of extras) {
        const code = String(n.code || "").toUpperCase();
        if (polishingPrice == null && code === "POLISHED") polishingPrice = n.price;
        if (holesPrice == null && code === "HOLES") holesPrice = n.price;
      }
    }

    return {
      pricePerSqm: Number.isFinite(pricePerSqm) ? pricePerSqm : null,
      minWidth,
      maxWidth,
      minLength,
      maxLength,
      polishingPrice: polishingPrice != null ? Number(polishingPrice) : null,
      holesPrice: holesPrice != null ? Number(holesPrice) : null,
      sku: (thickness && thickness.sku) || (material && material.sku) || null,
      thicknessTitle: thickness ? thickness.title : null,
      materialTitle: material ? material.title : null,
      colourTitle: colour ? colour.title : null,
      priceUnits: thickness ? thickness.priceUnitsOfMeasurement : null,
      currency: (conf.currency && conf.currency.code) || "GBP",
      entityCount: Array.isArray(entities) ? entities.length : 0,
    };
  } catch (e) {
    return { error: String(e) };
  }
}"""


def parse_cut_configurator_payload(raw: Any) -> dict[str, Any] | None:
    """Normalize evaluate() result into structured configurator_pricing fields."""
    if raw is None or raw == "":
        return None
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict) or data.get("error"):
        return None

    def _num(key: str) -> float | None:
        val = data.get(key)
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    out: dict[str, Any] = {
        "pricePerSqm": _num("pricePerSqm"),
        "minWidth": _num("minWidth"),
        "maxWidth": _num("maxWidth"),
        "minLength": _num("minLength"),
        "maxLength": _num("maxLength"),
        "polishingPrice": _num("polishingPrice"),
        "holesPrice": _num("holesPrice"),
        "sku": str(data.get("sku") or "").strip() or None,
        "thicknessTitle": str(data.get("thicknessTitle") or "").strip() or None,
        "materialTitle": str(data.get("materialTitle") or "").strip() or None,
        "colourTitle": str(data.get("colourTitle") or "").strip() or None,
        "priceUnits": str(data.get("priceUnits") or "").strip() or None,
        "currency": str(data.get("currency") or "GBP").strip() or "GBP",
    }
    # Drop empty shell
    if out["pricePerSqm"] is None and out["sku"] is None and out["minWidth"] is None:
        return None
    return out


def apply_configurator_pricing(
    product: dict[str, Any],
    pricing: dict[str, Any] | None,
    *,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Attach configurator_pricing and prefer its SKU / per-area price."""
    if not product or not pricing:
        return product
    log = logger or logging.getLogger(__name__)
    product["configurator_pricing"] = pricing

    specs = dict(product.get("specifications") or {})
    if pricing.get("pricePerSqm") is not None:
        specs["pricePerSqm"] = pricing["pricePerSqm"]
    for key in ("minWidth", "maxWidth", "minLength", "maxLength"):
        if pricing.get(key) is not None:
            specs[key] = pricing[key]
    if pricing.get("polishingPrice") is not None:
        specs["polishingPrice"] = pricing["polishingPrice"]
    if pricing.get("holesPrice") is not None:
        specs["holesPrice"] = pricing["holesPrice"]
    if pricing.get("thicknessTitle"):
        specs["Thickness"] = pricing["thicknessTitle"]
    if pricing.get("materialTitle"):
        specs["Material"] = pricing["materialTitle"]
    if pricing.get("colourTitle"):
        specs["Colour"] = pricing["colourTitle"]
    product["specifications"] = specs

    sku = str(pricing.get("sku") or "").strip()
    if sku:
        product["sku"] = sku
        variants = list(product.get("variants") or [])
        if variants and isinstance(variants[0], dict):
            variants[0]["sku"] = sku
            product["variants"] = variants

    price_sqm = pricing.get("pricePerSqm")
    if price_sqm is not None:
        # PRICE_PER_METER2 → display as £X.XX per m²; also derive per cm² for cut-to-size tag.
        amount = normalize_price(price_sqm)
        if amount:
            try:
                per_cm2 = float(amount) / 10000.0
                cut_price = format_cut_to_size_price(per_cm2)
            except ValueError:
                cut_price = ""
            display = f"£{amount} per m²"
            product["price"] = cut_price or display
            product["price_per_sqm"] = display
            variants = list(product.get("variants") or [])
            if variants and isinstance(variants[0], dict):
                variants[0]["price"] = cut_price or display
                product["variants"] = variants
            tags = list(product.get("tags") or [])
            if "cut-to-size-pricing" not in tags:
                tags.append("cut-to-size-pricing")
            product["tags"] = tags
            product["currency"] = pricing.get("currency") or product.get("currency") or "GBP"
            log.info(
                "CutConfigurator pricing: pricePerSqm=%s sku=%s sizes=%sx%s..%sx%s",
                amount,
                sku or "-",
                pricing.get("minWidth"),
                pricing.get("minLength"),
                pricing.get("maxWidth"),
                pricing.get("maxLength"),
            )
    return product


def thickness_mm_from_url_or_title(url: str = "", title: str = "") -> str | None:
    """Optional helper — e.g. '5mm' from slug/title."""
    blob = f"{url} {title}"
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm", blob, re.I)
    return m.group(0) if m else None
