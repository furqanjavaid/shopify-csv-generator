"""Normalize raw extractor output into the internal product schema."""

from __future__ import annotations

import itertools
import re
from typing import Any

from sentivo_extractor.core.schema import empty_product, empty_variant, ensure_product
from sentivo_extractor.core.utils import (
    absolute_url,
    canonicalize_option_name,
    detect_currency,
    normalize_price,
    sanitize_sku,
    stable_handle,
)


def _clean_html_description(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if text.startswith("<"):
        return text
    # wrap plain text
    escaped = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return f"<p>{escaped}</p>"


def _dedupe_images(images: list[dict[str, Any]], base_url: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for idx, img in enumerate(images or [], start=1):
        if isinstance(img, dict):
            src = absolute_url(base_url, str(img.get("src") or ""))
            alt = str(img.get("alt") or "")
            position = int(img.get("position") or len(out) + 1)
        elif isinstance(img, str) and img.strip():
            src = absolute_url(base_url, img.strip())
            alt = ""
            position = len(out) + 1
        else:
            continue
        if not src or src in seen:
            continue
        seen.add(src)
        out.append(
            {
                "src": src,
                "alt": alt,
                "position": position,
            }
        )
    for i, img in enumerate(out, start=1):
        img["position"] = i
    return out


def _normalize_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for opt in (options or [])[:3]:
        if isinstance(opt, str) and opt.strip():
            cleaned.append({"name": canonicalize_option_name(opt.strip()), "values": []})
            continue
        if not isinstance(opt, dict):
            continue
        name = canonicalize_option_name(str(opt.get("name") or ""))
        values: list[str] = []
        for v in opt.get("values") or []:
            s = re.sub(r"\s+", " ", str(v)).strip()
            s = re.sub(r"[£$€]\s*\d[\d,]*(?:\.\d+)?", "", s).strip()
            if s and s not in values:
                values.append(s)
        if name and values:
            cleaned.append({"name": name, "values": values})
    return cleaned


def _variant_key(v: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(v.get("option1") or ""),
        str(v.get("option2") or ""),
        str(v.get("option3") or ""),
    )


def _expand_variants_from_options(
    options: list[dict[str, Any]],
    base_price: str,
    base_compare: str,
) -> list[dict[str, Any]]:
    if not options:
        v = empty_variant()
        v["option1"] = "Default Title"
        v["price"] = base_price
        v["compare_at_price"] = base_compare
        return [v]

    axes = [opt.get("values") or ["Default Title"] for opt in options[:3]]
    combos = list(itertools.product(*axes))
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for combo in combos[:200]:
        vals = list(combo) + ["", "", ""]
        key = (vals[0], vals[1], vals[2])
        if key in seen:
            continue
        seen.add(key)
        v = empty_variant()
        v["option1"] = vals[0]
        v["option2"] = vals[1]
        v["option3"] = vals[2]
        v["price"] = base_price
        v["compare_at_price"] = base_compare
        out.append(v)
    return out


def normalize_product(raw: dict[str, Any], base_url: str = "") -> dict[str, Any]:
    """Produce a clean internal schema product from extractor output."""
    product = ensure_product(raw)
    source = product.get("source_url") or base_url or ""
    base = base_url or source

    product["title"] = re.sub(r"\s+", " ", str(product.get("title") or "")).strip()
    product["handle"] = product.get("handle") or stable_handle(product["title"], source)
    product["description_html"] = _clean_html_description(
        str(product.get("description_html") or "")
    )
    product["vendor"] = str(product.get("vendor") or product.get("brand") or "").strip()
    product["brand"] = str(product.get("brand") or product.get("vendor") or "").strip()
    product["product_type"] = str(product.get("product_type") or "").strip()
    product["department"] = str(product.get("department") or "").strip()
    product["status"] = (product.get("status") or "active").strip().lower() or "active"

    tags = product.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    product["tags"] = [str(t).strip() for t in tags if str(t).strip()]

    options = _normalize_options(product.get("options") or [])
    product["options"] = options

    images = _dedupe_images(product.get("images") or [], base)
    product["images"] = images

    currency = str(product.get("currency") or "")
    variants_in = list(product.get("variants") or [])
    variants: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    if variants_in:
        for raw_v in variants_in:
            if not isinstance(raw_v, dict):
                continue
            v = empty_variant()
            v.update({k: raw_v.get(k, v.get(k)) for k in v})
            v["price"] = normalize_price(v.get("price"))
            v["compare_at_price"] = normalize_price(v.get("compare_at_price"))
            v["sku"] = sanitize_sku(v.get("sku"))
            v["barcode"] = str(v.get("barcode") or "").strip()
            v["option1"] = str(v.get("option1") or "").strip()
            v["option2"] = str(v.get("option2") or "").strip()
            v["option3"] = str(v.get("option3") or "").strip()
            if not any((v["option1"], v["option2"], v["option3"])):
                v["option1"] = "Default Title"
            if v.get("variant_image"):
                v["variant_image"] = absolute_url(base, str(v["variant_image"]))
            if not currency and (raw_v.get("price") or ""):
                currency = detect_currency(str(raw_v.get("price")))
            key = _variant_key(v)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            variants.append(v)
    else:
        # Infer a base price from first image alt / leftover fields
        base_price = normalize_price(raw.get("price") or "")
        base_compare = normalize_price(raw.get("compare_at_price") or "")
        if not currency:
            currency = detect_currency(str(raw.get("price") or ""))
        variants = _expand_variants_from_options(options, base_price, base_compare)

    # Ensure option names exist when variants have values
    if variants and not options:
        if variants[0].get("option1") and variants[0]["option1"] != "Default Title":
            product["options"] = [{"name": "Title", "values": [variants[0]["option1"]]}]
        else:
            product["options"] = [{"name": "Title", "values": ["Default Title"]}]
            for v in variants:
                if not v.get("option1"):
                    v["option1"] = "Default Title"

    product["variants"] = variants
    product["currency"] = currency
    product["source_url"] = source
    product["source_name"] = str(product.get("source_name") or "").strip()
    product["priority"] = str(product.get("priority") or "").strip()

    from sentivo_extractor.core.confidence import apply_confidence

    return apply_confidence(product)
