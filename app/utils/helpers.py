"""Shared utility helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def slugify(text: str) -> str:
    """Convert text to a Shopify-style handle slug (hyphenated)."""
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def filename_slug(text: str) -> str:
    """Lowercase slug for store/collection names — keep hyphens, strip other specials."""
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9-]+", "", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def upload_filename_slug(stem: str) -> str:
    """Slug from uploaded file stem — lowercase with underscores."""
    text = (stem or "").strip().lower()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def store_name_from_url(url: str) -> str:
    """
    Extract store name from URL domain.
    wildhavenco.com → wildhavenco
    www.aluspec.co.uk → aluspec
    forge-finishes.com → forge-finishes
    """
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    host = urlparse(raw).netloc.lower()
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]

    multi_tlds = (
        ".co.uk",
        ".com.au",
        ".co.nz",
        ".co.za",
        ".com.br",
        ".org.uk",
        ".me.uk",
    )
    for suffix in multi_tlds:
        if host.endswith(suffix):
            host = host[: -len(suffix)]
            break
    else:
        parts = host.rsplit(".", 1)
        if len(parts) == 2 and 2 <= len(parts[1]) <= 6:
            host = parts[0]

    return filename_slug(host) or "store"


def collection_handle_from_url(url: str) -> str:
    """Last path segment after /collections/, or 'products' if absent."""
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    path = urlparse(raw).path or ""
    match = re.search(r"/collections/([^/?#]+)", path, re.I)
    if match:
        return filename_slug(match.group(1)) or "products"
    return "products"


def output_filename_from_url(url: str) -> str:
    """{store_name}_{collection_handle}_products.csv"""
    store = store_name_from_url(url)
    collection = collection_handle_from_url(url)
    return f"{store}_{collection}_products.csv"


def output_filename_from_upload(filepath: str) -> str:
    """{uploaded_stem_slug}_products.csv"""
    stem = Path(filepath).stem
    base = upload_filename_slug(stem) or "upload"
    return f"{base}_products.csv"


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
