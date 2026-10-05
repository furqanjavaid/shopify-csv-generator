"""Domain-based output folder + prefixed artifact naming."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

_SAFE_RE = re.compile(r"[^\w\-]+")


def host_from_url(url: str) -> str:
    """Return hostname without leading www."""
    host = (urlparse(url or "").netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def domain_folder_name(url_or_host: str) -> str:
    """
    First DNS label of the host for folder naming.

    'https://www.directplastics.co.uk/x' → 'directplastics'
    'shop.example.com' → 'shop'
    """
    text = (url_or_host or "").strip()
    if not text:
        return "unknown"
    if "://" in text or "/" in text:
        host = host_from_url(text)
    else:
        host = text.lower().removeprefix("www.")
    if not host:
        return "unknown"
    first = host.split(".", 1)[0].strip()
    safe = _SAFE_RE.sub("_", first).strip("_")
    return safe or "unknown"


def domain_output_dir(base_dir: Path | str, domain_key: str) -> Path:
    """base/directplastics"""
    return Path(base_dir) / domain_key


def prefixed_filename(domain_key: str, filename: str) -> str:
    """directplastics_shopify_import.csv"""
    name = Path(filename).name
    prefix = f"{domain_key}_"
    if name.startswith(prefix):
        return name
    return f"{prefix}{name}"


def domain_artifact_path(
    base_dir: Path | str,
    domain_key: str,
    filename: str,
) -> Path:
    """base/directplastics/directplastics_shopify_import.csv"""
    return domain_output_dir(base_dir, domain_key) / prefixed_filename(
        domain_key, filename
    )


def ensure_domain_dir(base_dir: Path | str, domain_key: str) -> Path:
    path = domain_output_dir(base_dir, domain_key)
    path.mkdir(parents=True, exist_ok=True)
    return path


def infer_domain_key_from_dir(path: Path | str) -> str:
    """
    Infer domain key from a domain output folder path
    (folder name, or prefix on existing artifacts).
    """
    p = Path(path)
    if p.is_file():
        p = p.parent
    name = p.name.strip()
    if name and name not in {"reprocessed", "raw_json_backup", "qa", "debug", "logs", "images"}:
        return domain_folder_name(name) if "." in name else name
    return "unknown"
