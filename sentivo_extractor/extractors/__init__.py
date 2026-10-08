"""Platform-specific product extractors."""

from __future__ import annotations

from sentivo_extractor.extractors.base import BaseExtractor, ExtractorRegistry
from sentivo_extractor.extractors.html_extractor import HtmlExtractor
from sentivo_extractor.extractors.jsonld_extractor import JsonLdExtractor
from sentivo_extractor.extractors.magento_extractor import MagentoExtractor
from sentivo_extractor.extractors.magento_product_extractor import MagentoProductExtractor
from sentivo_extractor.extractors.nextjs_extractor import NextJsExtractor
from sentivo_extractor.extractors.playwright_extractor import PlaywrightExtractor
from sentivo_extractor.extractors.shopify_extractor import ShopifyExtractor
from sentivo_extractor.extractors.woocommerce_extractor import WooCommerceExtractor

__all__ = [
    "BaseExtractor",
    "ExtractorRegistry",
    "ShopifyExtractor",
    "WooCommerceExtractor",
    "MagentoExtractor",
    "MagentoProductExtractor",
    "JsonLdExtractor",
    "NextJsExtractor",
    "HtmlExtractor",
    "PlaywrightExtractor",
]


def build_default_registry() -> ExtractorRegistry:
    """Register extractors in priority order (first match wins per attempt)."""
    registry = ExtractorRegistry()
    for cls in (
        ShopifyExtractor,
        WooCommerceExtractor,
        MagentoProductExtractor,
        MagentoExtractor,
        JsonLdExtractor,
        NextJsExtractor,
        HtmlExtractor,
        PlaywrightExtractor,
    ):
        registry.register(cls())
    return registry
