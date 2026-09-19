"""Scrape product listing pages into tabular data."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup

from app.utils.helpers import clean_value


class ScraperError(Exception):
    """Raised when scraping fails."""


class ProductScraper:
    """Extract product data from a listing URL using progressive strategies."""

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    TIMEOUT = 15

    def scrape(self, url: str) -> dict[str, Any]:
        url = (url or "").strip()
        if not url:
            raise ScraperError("URL is required.")

        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            response = requests.get(url, headers=headers, timeout=self.TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ScraperError(f"Unable to reach URL: {exc}") from exc

        soup = BeautifulSoup(response.text, "lxml")

        for strategy_name, strategy_fn in (
            ("JSON-LD", self._strategy_json_ld),
            ("Open Graph", self._strategy_open_graph),
            ("Generic HTML", self._strategy_generic_html),
        ):
            product = strategy_fn(soup)
            if product and product.get("Title"):
                headers_list = list(product.keys())
                row = {k: clean_value(v) for k, v in product.items()}
                return {
                    "headers": headers_list,
                    "rows": [row],
                    "row_count": 1,
                    "strategy_used": strategy_name,
                }

        raise ScraperError(
            "Could not extract product data. The page may not be a product listing "
            "or may block scrapers."
        )

    def _strategy_json_ld(self, soup: BeautifulSoup) -> Optional[dict[str, str]]:
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            raw = script.string or script.get_text() or ""
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            product = self._find_product_in_ld(data)
            if not product:
                continue

            name = product.get("name") or ""
            description = product.get("description") or ""
            sku = product.get("sku") or product.get("mpn") or ""
            brand = product.get("brand")
            if isinstance(brand, dict):
                brand = brand.get("name", "")
            brand = brand or ""

            image = product.get("image") or ""
            if isinstance(image, list) and image:
                image = image[0]
            if isinstance(image, dict):
                image = image.get("url", "")

            price = ""
            offers = product.get("offers")
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                price = str(offers.get("price") or offers.get("lowPrice") or "")

            if not name:
                continue

            return {
                "Title": str(name),
                "Description": str(description),
                "Price": str(price),
                "SKU": str(sku),
                "Product image URL": str(image),
                "Vendor": str(brand),
            }
        return None

    def _find_product_in_ld(self, data: Any) -> Optional[dict]:
        if isinstance(data, list):
            for item in data:
                found = self._find_product_in_ld(item)
                if found:
                    return found
            return None

        if not isinstance(data, dict):
            return None

        type_val = data.get("@type", "")
        types = type_val if isinstance(type_val, list) else [type_val]
        types_lower = [str(t).lower() for t in types]

        if "product" in types_lower:
            return data

        if "itemlist" in types_lower:
            elements = data.get("itemListElement") or []
            for el in elements:
                if isinstance(el, dict):
                    item = el.get("item") or el
                    found = self._find_product_in_ld(item)
                    if found:
                        return found

        for key in ("@graph", "mainEntity", "mainEntityOfPage"):
            if key in data:
                found = self._find_product_in_ld(data[key])
                if found:
                    return found

        return None

    def _strategy_open_graph(self, soup: BeautifulSoup) -> Optional[dict[str, str]]:
        def og(prop: str) -> str:
            tag = soup.find("meta", property=prop) or soup.find(
                "meta", attrs={"name": prop}
            )
            if tag and tag.get("content"):
                return str(tag["content"]).strip()
            return ""

        title = og("og:title")
        if not title:
            return None

        return {
            "Title": title,
            "Description": og("og:description"),
            "Product image URL": og("og:image"),
            "Price": og("product:price:amount"),
            "SKU": og("product:retailer_item_id"),
        }

    def _strategy_generic_html(self, soup: BeautifulSoup) -> Optional[dict[str, str]]:
        title = ""
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(" ", strip=True)
        if not title:
            return None

        price = ""
        for el in soup.find_all(True, class_=re.compile(r"price", re.I)):
            text = el.get_text(" ", strip=True)
            if re.search(r"\d", text):
                # Prefer a numeric-looking price fragment
                match = re.search(r"[\d,.]+", text.replace(",", ""))
                price = match.group(0) if match else text
                break

        description = ""
        containers = soup.select("main, article, [class*=product]")
        search_roots = containers or [soup]
        for root in search_roots:
            for p in root.find_all("p"):
                text = p.get_text(" ", strip=True)
                if len(text) > 50:
                    description = text
                    break
            if description:
                break

        image = ""
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if not src or src.startswith("data:"):
                continue
            lower = src.lower()
            if any(x in lower for x in ("icon", "logo", "sprite", "1x1", "pixel")):
                continue
            image = src
            break

        return {
            "Title": title,
            "Description": description,
            "Price": price,
            "Product image URL": image,
        }
