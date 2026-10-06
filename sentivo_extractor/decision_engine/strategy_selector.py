"""Platform-aware strategy ordering with domain memory."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sentivo_extractor.core.platform_detector import detect_platform
from sentivo_extractor.core.pdp_pipeline import (
    SOURCE_JSONLD,
    SOURCE_MAGENTO,
    SOURCE_OPENGRAPH,
    SOURCE_PLAYWRIGHT,
    SOURCE_SHOPIFY,
    SOURCE_WOO,
)

# Canonical Decision Engine fallback chain (user-specified order).
DEFAULT_STRATEGY_CHAIN: tuple[str, ...] = (
    SOURCE_SHOPIFY,
    SOURCE_MAGENTO,
    SOURCE_WOO,
    SOURCE_JSONLD,
    SOURCE_OPENGRAPH,
    SOURCE_PLAYWRIGHT,
)

# Platform → preferred first strategies (rest follow DEFAULT order, de-duped).
_PLATFORM_PRIORITY: dict[str, tuple[str, ...]] = {
    "Shopify": (SOURCE_SHOPIFY, SOURCE_JSONLD, SOURCE_OPENGRAPH, SOURCE_PLAYWRIGHT),
    "Magento": (SOURCE_MAGENTO, SOURCE_JSONLD, SOURCE_OPENGRAPH, SOURCE_PLAYWRIGHT),
    "WooCommerce": (SOURCE_WOO, SOURCE_JSONLD, SOURCE_OPENGRAPH, SOURCE_PLAYWRIGHT),
    "Custom": (
        SOURCE_JSONLD,
        SOURCE_OPENGRAPH,
        SOURCE_SHOPIFY,
        SOURCE_WOO,
        SOURCE_MAGENTO,
        SOURCE_PLAYWRIGHT,
    ),
    "Next.js": (SOURCE_JSONLD, SOURCE_OPENGRAPH, SOURCE_PLAYWRIGHT),
    "Unknown": DEFAULT_STRATEGY_CHAIN,
}


def _domain_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().removeprefix("www.")
    except Exception:
        return ""


class StrategySelector:
    """
    Detect platform, pick best extraction strategy order, and remember
    which strategy succeeded for similar (same-domain) URLs.
    """

    def __init__(self) -> None:
        # domain → last successful strategy name
        self._winners: dict[str, str] = {}

    def detect(
        self,
        url: str,
        *,
        html: str = "",
        session: Any = None,
    ) -> dict[str, Any]:
        detected = detect_platform(url, html=html, session=session)
        platform = str(detected.get("platform") or "Custom")
        if platform in ("", "Unknown"):
            platform = "Custom"
        return {
            "platform": platform,
            "signals": list(detected.get("signals") or []),
        }

    def strategies_for(
        self,
        platform: str,
        *,
        url: str = "",
    ) -> list[str]:
        """Return ordered strategy names for this platform (winners first)."""
        preferred = list(_PLATFORM_PRIORITY.get(platform) or DEFAULT_STRATEGY_CHAIN)
        # Fill with any DEFAULT entries not already present.
        for name in DEFAULT_STRATEGY_CHAIN:
            if name not in preferred:
                preferred.append(name)

        domain = _domain_of(url)
        winner = self._winners.get(domain) if domain else None
        if winner and winner in preferred:
            preferred = [winner] + [s for s in preferred if s != winner]
        return preferred

    def remember_success(self, url: str, strategy: str) -> None:
        domain = _domain_of(url)
        if domain and strategy:
            self._winners[domain] = strategy

    def winner_for(self, url: str) -> str | None:
        return self._winners.get(_domain_of(url))
