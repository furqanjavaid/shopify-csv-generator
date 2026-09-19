"""Shared utility helpers."""

from __future__ import annotations

import re
from typing import Any


def slugify(text: str) -> str:
    """Convert text to a Shopify-style handle slug."""
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def clean_value(value: Any) -> str:
    """Normalize cell values to stripped strings; treat NaN-like as empty."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat"}:
        return ""
    return text


def is_valid_url(url: str) -> bool:
    """Return True if URL starts with http:// or https://."""
    url = (url or "").strip()
    return url.startswith("http://") or url.startswith("https://")
