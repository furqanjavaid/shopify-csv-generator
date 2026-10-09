"""
SENTIVO-PRICING-V2 — Sheet Plastics CutConfiguratorWidget pricing.

Confirmed JS path:
  CutConfiguratorWidget.vm.$store.state.Configurator.confData.entities

Extracts (material > colour > thickness):
  - pricePerSqm
  - minWidth / maxWidth / minLength / maxLength
  - polishing (flat £/sqm options)
  - holes (flat per-hole + diameter tiers)
  - accessories (customOptions with allowQty=true)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sentivo_extractor.core.utils import (
    normalize_price,
    slugify,
    stable_handle,
)

logger = logging.getLogger(__name__)

# Official hole diameter tier table (sheetplastics tooltip / product FAQ).
HOLE_DIAMETER_PRICES: dict[str, float] = {
    "base": 1.0,
    "15mm": 5.0,
    "20mm": 8.0,
    "25mm": 11.0,
    "30mm": 13.0,
    "35mm": 16.0,
    "40mm": 19.0,
    "45mm": 22.0,
    "50mm": 25.0,
}

# Runs in browser after networkidle + 3–5s settle.
CUT_CONFIGURATOR_EVAL_JS = """() => {
  try {
    if (typeof CutConfiguratorWidget === 'undefined') {
      return {
        error: 'CutConfiguratorWidget_undefined',
        keys: Object.keys(window).filter(k => /Cut|Config|Widget/i.test(k)),
      };
    }
    const w = CutConfiguratorWidget;
    if (!w.vm || !w.vm.$store) {
      return { error: 'CutConfiguratorWidget_no_vm' };
    }
    const conf = w.vm.$store.state.Configurator;
    if (!conf || !conf.confData) {
      return { error: 'Configurator_confData_missing' };
    }
    const entities = conf.confData.entities || [];
    const path = conf.selectedPathWay || [];
    const sizes = conf.sizes || {};

    function typeCode(n) {
      return (n && n.entityTypeMetadata && n.entityTypeMetadata.code) || '';
    }
    function walkNodes(nodes, out) {
      if (!Array.isArray(nodes)) return;
      for (const n of nodes) {
        out.push(n);
        if (n && n.nodes) walkNodes(n.nodes, out);
      }
    }

    // Prefer selected pathway (material > colour > thickness); else walk entities.
    const flat = [];
    walkNodes(path, flat);
    if (!flat.length) {
      for (const root of entities) walkNodes([root], flat);
    }

    let material = null, colour = null, thickness = null;
    const polishingOptions = [];
    let holesNode = null;
    const accessoryMap = {};

    for (const n of flat) {
      if (!n) continue;
      const tc = typeCode(n);
      const code = String(n.code || '').toUpperCase();
      if (tc === 'MATERIAL') material = n;
      if (tc === 'MATERIAL_TYPE') colour = n;
      if (tc === 'MATERIAL_THICKNESS') thickness = n;

      // Polishing: PROCESSING nodes under material (flat £ addition).
      if (n.processingMod === 'PROCESSING' || code === 'POLISHED'
          || code === 'PERFECTLY_POLISHED_BUFFED' || code === 'UNPOLISHED_SAW_CUT'
          || /polish/i.test(String(n.title || ''))) {
        polishingOptions.push({
          title: n.title || code,
          code: code || null,
          price: n.price != null ? Number(n.price) : 0,
          description: (n.processingProps && n.processingProps.description) || null,
        });
      }
      if (code === 'HOLES' || /^holes$/i.test(String(n.title || ''))) {
        holesNode = n;
      }

      // Accessories: customOptions with allowQty=true (flat priced add-ons).
      for (const opt of (n.customOptions || [])) {
        if (!opt || opt.allowQty !== true) continue;
        const ocode = String(opt.code || opt.name || '').toUpperCase();
        if (!ocode || accessoryMap[ocode]) continue;
        accessoryMap[ocode] = {
          name: opt.name || ocode,
          code: ocode,
          allowQty: true,
          required: !!opt.required,
          values: (opt.values || [])
            .filter(v => v && Number(v.price) > 0)
            .map(v => ({
              label: v.label || String(v.value),
              price: Number(v.price),
            })),
        };
      }
    }

    // Last MATERIAL_THICKNESS on path wins (deepest leaf).
    for (let i = flat.length - 1; i >= 0; i--) {
      if (typeCode(flat[i]) === 'MATERIAL_THICKNESS') {
        thickness = flat[i];
        break;
      }
    }

    // Dedupe polishing by code.
    const polishSeen = {};
    const polishing = [];
    for (const p of polishingOptions) {
      const key = p.code || p.title;
      if (polishSeen[key]) continue;
      polishSeen[key] = true;
      polishing.push(p);
    }

    const pricePerSqm = thickness != null && thickness.price != null
      ? Number(thickness.price)
      : null;

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

    // Default polishing price = standard "Polished" option when present.
    let polishingPrice = null;
    for (const p of polishing) {
      if (p.code === 'POLISHED') { polishingPrice = p.price; break; }
    }
    if (polishingPrice == null && polishing.length) {
      polishingPrice = polishing[0].price;
    }

    const holesBase = holesNode && holesNode.price != null
      ? Number(holesNode.price)
      : 1;
    const holesTooltip = holesNode && holesNode.processingProps
      ? (holesNode.processingProps.tooltip || holesNode.processingProps.description || '')
      : '';

    return {
      pricePerSqm: Number.isFinite(pricePerSqm) ? pricePerSqm : null,
      minWidth,
      maxWidth,
      minLength,
      maxLength,
      polishingPrice: polishingPrice != null ? Number(polishingPrice) : null,
      polishingOptions: polishing,
      holesPrice: holesBase,
      holesDiameterPrices: {
        base: holesBase,
        '15mm': 5, '20mm': 8, '25mm': 11, '30mm': 13,
        '35mm': 16, '40mm': 19, '45mm': 22, '50mm': 25,
      },
      holesTooltip: holesTooltip || null,
      accessories: Object.values(accessoryMap),
      sku: (thickness && thickness.sku) || (material && material.sku) || null,
      thicknessTitle: thickness ? thickness.title : null,
      materialTitle: material ? material.title : null,
      colourTitle: colour ? colour.title : null,
      priceUnits: thickness ? thickness.priceUnitsOfMeasurement : null,
      currency: (conf.currency && conf.currency.code) || 'GBP',
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
    if not isinstance(data, dict):
        return None
    if data.get("error"):
        logger.warning("CutConfigurator evaluate error: %s", data.get("error"))
        if data.get("keys"):
            logger.warning("Window Cut/Config keys: %s", data.get("keys"))
        return None

    def _num(key: str) -> float | None:
        val = data.get(key)
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    polishing_options: list[dict[str, Any]] = []
    for item in data.get("polishingOptions") or []:
        if not isinstance(item, dict):
            continue
        polishing_options.append(
            {
                "title": str(item.get("title") or "").strip() or None,
                "code": str(item.get("code") or "").strip() or None,
                "price": float(item["price"])
                if item.get("price") is not None
                else 0.0,
                "description": str(item.get("description") or "").strip() or None,
            }
        )

    accessories: list[dict[str, Any]] = []
    for item in data.get("accessories") or []:
        if not isinstance(item, dict):
            continue
        values = []
        for v in item.get("values") or []:
            if not isinstance(v, dict):
                continue
            try:
                price = float(v.get("price"))
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            values.append(
                {
                    "label": str(v.get("label") or "").strip(),
                    "price": price,
                }
            )
        if not values:
            continue
        accessories.append(
            {
                "name": str(item.get("name") or "").strip(),
                "code": str(item.get("code") or "").strip(),
                "allowQty": True,
                "required": bool(item.get("required")),
                "values": values,
            }
        )

    holes_tiers = dict(HOLE_DIAMETER_PRICES)
    raw_tiers = data.get("holesDiameterPrices")
    if isinstance(raw_tiers, dict):
        for k, v in raw_tiers.items():
            try:
                holes_tiers[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    base_hole = _num("holesPrice")
    if base_hole is not None:
        holes_tiers["base"] = base_hole

    out: dict[str, Any] = {
        "pricePerSqm": _num("pricePerSqm"),
        "minWidth": _num("minWidth"),
        "maxWidth": _num("maxWidth"),
        "minLength": _num("minLength"),
        "maxLength": _num("maxLength"),
        "polishingPrice": _num("polishingPrice"),
        "polishingOptions": polishing_options,
        "holesPrice": base_hole if base_hole is not None else holes_tiers.get("base"),
        "holesDiameterPrices": holes_tiers,
        "holesTooltip": str(data.get("holesTooltip") or "").strip() or None,
        "accessories": accessories,
        "sku": str(data.get("sku") or "").strip() or None,
        "thicknessTitle": str(data.get("thicknessTitle") or "").strip() or None,
        "materialTitle": str(data.get("materialTitle") or "").strip() or None,
        "colourTitle": str(data.get("colourTitle") or "").strip() or None,
        "priceUnits": str(data.get("priceUnits") or "").strip() or None,
        "currency": str(data.get("currency") or "GBP").strip() or "GBP",
    }
    if out["pricePerSqm"] is None and out["sku"] is None and out["minWidth"] is None:
        return None
    return out


def unique_configurator_handle(
    title: str,
    source_url: str = "",
    *,
    thickness: str | None = None,
    sku: str | None = None,
) -> str:
    """Build a unique Shopify handle: title + thickness + SKU."""
    base = stable_handle(title, source_url)
    parts = [base]
    thick_slug = slugify(str(thickness or ""))
    if thick_slug and thick_slug not in base:
        parts.append(thick_slug)
    sku_slug = slugify(str(sku or ""))
    if sku_slug and sku_slug not in "-".join(parts):
        parts.append(sku_slug)
    return "-".join(p for p in parts if p) or "product"


def apply_configurator_pricing(
    product: dict[str, Any],
    pricing: dict[str, Any] | None,
    *,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Attach configurator_pricing, SKU/price, and unique thickness/SKU handle."""
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
    if pricing.get("polishingOptions"):
        specs["polishingOptions"] = pricing["polishingOptions"]
    if pricing.get("holesPrice") is not None:
        specs["holesPrice"] = pricing["holesPrice"]
    if pricing.get("holesDiameterPrices"):
        specs["holesDiameterPrices"] = pricing["holesDiameterPrices"]
    if pricing.get("accessories"):
        specs["accessories"] = pricing["accessories"]
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

    # Unique handle per thickness/SKU (avoids collisions across cut-to-size PDPs).
    product["handle"] = unique_configurator_handle(
        str(product.get("title") or ""),
        str(product.get("source_url") or ""),
        thickness=str(pricing.get("thicknessTitle") or "") or None,
        sku=sku or None,
    )

    price_sqm = pricing.get("pricePerSqm")
    if price_sqm is not None:
        amount = normalize_price(price_sqm)
        if amount:
            # Shopify Variant Price = £/m² rate (not £/cm² which rounds to 0.00).
            display = f"£{amount} per m²"
            product["price"] = amount
            product["price_per_sqm"] = display
            variants = list(product.get("variants") or [])
            if variants and isinstance(variants[0], dict):
                variants[0]["price"] = amount
                product["variants"] = variants
            tags = list(product.get("tags") or [])
            if "cut-to-size-pricing" not in tags:
                tags.append("cut-to-size-pricing")
            product["tags"] = tags
            product["currency"] = (
                pricing.get("currency") or product.get("currency") or "GBP"
            )
            log.info(
                "SENTIVO-PRICING-V2: pricePerSqm=%s sku=%s polish=%s holes=%s accessories=%s handle=%s",
                amount,
                sku or "-",
                pricing.get("polishingPrice"),
                pricing.get("holesPrice"),
                len(pricing.get("accessories") or []),
                product.get("handle"),
            )
    return product


def thickness_mm_from_url_or_title(url: str = "", title: str = "") -> str | None:
    """Optional helper — e.g. '5mm' from slug/title."""
    blob = f"{url} {title}"
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm", blob, re.I)
    return m.group(0) if m else None
