"""Extractor base class + registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseExtractor(ABC):
    name: str = "base"
    platforms: tuple[str, ...] = ()

    def supports(self, platform: str, html: str = "", url: str = "") -> bool:
        if not self.platforms:
            return True
        return platform in self.platforms

    @abstractmethod
    def extract(
        self,
        url: str,
        html: str = "",
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Return a partial product dict or None if this extractor cannot parse."""


class ExtractorRegistry:
    def __init__(self) -> None:
        self._extractors: list[BaseExtractor] = []

    def register(self, extractor: BaseExtractor) -> None:
        self._extractors.append(extractor)

    def all(self) -> list[BaseExtractor]:
        return list(self._extractors)

    def for_platform(self, platform: str) -> list[BaseExtractor]:
        preferred = [e for e in self._extractors if platform in e.platforms]
        fallback = [e for e in self._extractors if e not in preferred]
        return preferred + fallback
