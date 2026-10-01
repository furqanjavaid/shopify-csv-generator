"""Image extraction helpers: normalize, dedupe, optional download/convert."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests

from sentivo_extractor.core.utils import absolute_url, content_hash

logger = logging.getLogger("sentivo_extractor")


def unwrap_next_image(src: str, base_url: str) -> str:
    if not src:
        return ""
    absolute = absolute_url(base_url, src)
    if "_next/image" not in absolute:
        return absolute
    try:
        qs = parse_qs(urlparse(absolute).query)
        raw = (qs.get("url") or [""])[0]
        if not raw:
            return absolute
        real = unquote(raw)
        return absolute_url(base_url, real)
    except Exception:
        return absolute


def prefer_largest_srcset(srcset: str, base_url: str) -> str:
    if not srcset:
        return ""
    best = ""
    best_w = -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        url = bits[0]
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                w = int(bits[1][:-1])
            except ValueError:
                w = 0
        if w >= best_w:
            best_w = w
            best = url
    return unwrap_next_image(best, base_url) if best else ""


def process_product_images(
    product: dict[str, Any],
    *,
    download: bool = False,
    convert: bool = True,
    images_dir: Path | None = None,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """
    Normalize product images and optionally download/convert.
    Returns manifest rows for images_manifest.csv.
    Mutates product["images"] src when downloaded locally (keeps remote URL by default).
    """
    handle = product.get("handle") or "product"
    base = product.get("source_url") or ""
    sess = session or requests.Session()
    manifest: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    cleaned: list[dict[str, Any]] = []

    if images_dir:
        images_dir.mkdir(parents=True, exist_ok=True)

    for img in product.get("images") or []:
        src = unwrap_next_image(str(img.get("src") or ""), base)
        if not src:
            continue
        row = {
            "handle": handle,
            "position": img.get("position") or len(cleaned) + 1,
            "src": src,
            "local_path": "",
            "content_hash": "",
            "converted_from": "",
            "status": "remote",
        }
        if download and images_dir:
            try:
                resp = sess.get(src, timeout=30)
                resp.raise_for_status()
                data = resp.content
                digest = content_hash(data)
                row["content_hash"] = digest
                if digest in seen_hashes:
                    row["status"] = "duplicate_skipped"
                    manifest.append(row)
                    continue
                seen_hashes.add(digest)
                ext = _guess_ext(src, resp.headers.get("Content-Type", ""))
                local = images_dir / f"{handle}_{row['position']}{ext}"
                local.write_bytes(data)
                converted_from = ""
                if convert and ext.lower() in {".webp", ".avif"}:
                    converted = _convert_to_jpeg(local)
                    if converted:
                        converted_from = ext.lstrip(".")
                        local = converted
                        ext = local.suffix
                row["local_path"] = str(local)
                row["converted_from"] = converted_from
                row["status"] = "downloaded"
                # Keep remote URL for Shopify CSV (hosted externally)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Image download failed %s: %s", src, exc)
                row["status"] = f"failed:{exc}"
        cleaned.append(
            {
                "src": src,
                "alt": img.get("alt") or "",
                "position": len(cleaned) + 1,
            }
        )
        row["position"] = len(cleaned)
        manifest.append(row)

    product["images"] = cleaned
    return manifest


def _guess_ext(url: str, content_type: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    ct = (content_type or "").lower()
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "avif" in ct:
        return ".avif"
    return ".jpg"


def _convert_to_jpeg(path: Path) -> Path | None:
    try:
        from PIL import Image

        out = path.with_suffix(".jpg")
        with Image.open(path) as im:
            rgb = im.convert("RGB")
            rgb.save(out, format="JPEG", quality=90)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("Image convert failed %s: %s", path, exc)
        return None
