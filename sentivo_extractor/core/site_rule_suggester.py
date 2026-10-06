"""Generate draft YAML site rules from HTML samples (suggestions only)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from bs4 import BeautifulSoup

SUGGESTION_HEADER = (
    "# AUTO-GENERATED SUGGESTIONS — do not trust blindly.\n"
    "# Review and promote to configs/site_rules/ after manual verification.\n"
    "suggestions_only: true\n"
)


def _class_selector(el) -> str | None:
    classes = el.get("class") or []
    if not classes:
        return None
    # Prefer shorter meaningful class
    cls = sorted(classes, key=len)[0]
    tag = el.name or "*"
    return f"{tag}.{cls}"


def _score_candidates(samples: list[str], pickers) -> list[str]:
    counter: Counter[str] = Counter()
    for html in samples:
        soup = BeautifulSoup(html or "", "lxml")
        for sel in pickers(soup):
            if sel:
                counter[sel] += 1
    return [s for s, _ in counter.most_common(5)]


def suggest_selectors(html_samples: list[str]) -> dict[str, list[str]]:
    def title_pickers(soup):
        for el in soup.select("h1"):
            yield "h1"
            sel = _class_selector(el)
            if sel:
                yield sel

    def price_pickers(soup):
        for el in soup.select('[itemprop="price"], .price, [class*="price"]'):
            if el.get("itemprop") == "price":
                yield '[itemprop="price"]'
            sel = _class_selector(el)
            if sel:
                yield sel
            for c in el.get("class") or []:
                if "price" in c.lower():
                    yield f".{c}"

    def desc_pickers(soup):
        for el in soup.select(
            '[itemprop="description"], [class*="description"], #description, .product-description'
        ):
            if el.get("id"):
                yield f"#{el.get('id')}"
            sel = _class_selector(el)
            if sel:
                yield sel
            yield '[itemprop="description"]'

    def image_pickers(soup):
        for el in soup.select(
            '.product-image img, [class*="gallery"] img, [itemprop="image"], main img'
        ):
            parent = el.parent
            if parent and parent.get("class"):
                yield _class_selector(parent) + " img" if _class_selector(parent) else "img"
            yield "img[itemprop='image']"
            yield ".product-image img"

    def card_pickers(soup):
        for el in soup.select(
            ".product-card, .product-item, li.product, [class*='product-card'], .grid-product"
        ):
            sel = _class_selector(el)
            if sel:
                yield sel

    def option_pickers(soup):
        for el in soup.select(
            "form select, .variations select, select[name*='attribute'], "
            "[class*='swatch'], [role='radiogroup']"
        ):
            name = el.get("name")
            if name:
                yield f"select[name='{name}']"
            sel = _class_selector(el)
            if sel:
                yield sel

    def product_url_pickers(soup):
        for a in soup.select(
            'a[href*="/product"], a[href*="/products/"], a[href*="/shop/"]'
        )[:20]:
            href = a.get("href") or ""
            if "/products/" in href:
                yield 'a[href*="/products/"]'
            elif "/product" in href:
                yield 'a[href*="/product"]'
            elif "/shop/" in href:
                yield 'a[href*="/shop/"]'

    return {
        "product_card": _score_candidates(html_samples, card_pickers),
        "product_url": _score_candidates(html_samples, product_url_pickers),
        "title": _score_candidates(html_samples, title_pickers),
        "price": _score_candidates(html_samples, price_pickers),
        "description": _score_candidates(html_samples, desc_pickers),
        "images": _score_candidates(html_samples, image_pickers),
        "variant_options": _score_candidates(html_samples, option_pickers),
    }


def write_site_rule_suggestion(
    domain: str,
    html_samples: list[str],
    output_path: Path,
    *,
    platform: str = "Custom",
    notes: list[str] | None = None,
) -> Path:
    domain = domain.lower().removeprefix("www.")
    selectors = suggest_selectors(html_samples)
    payload: dict[str, Any] = {
        "hosts": [domain, f"www.{domain}"],
        "platform_hint": platform,
        "suggestions_only": True,
        "selectors": selectors,
        "notes": notes
        or [
            "These selectors are heuristics from sample pages.",
            "Validate manually before using in production.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    output_path.write_text(SUGGESTION_HEADER + body, encoding="utf-8-sig")
    return output_path


def domain_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host
