"""Platform-specific product URL discoverers."""

from __future__ import annotations

from sentivo_extractor.crawlers.magento_crawler import MagentoCategoryCrawler, discover_magento_products

__all__ = [
    "MagentoCategoryCrawler",
    "discover_magento_products",
]
