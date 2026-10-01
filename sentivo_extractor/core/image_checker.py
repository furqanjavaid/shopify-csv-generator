"""Lightweight image URL accessibility checks (HEAD / streamed GET)."""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

import requests

from sentivo_extractor.core.utils import DEFAULT_USER_AGENT, retry_call

logger = logging.getLogger("sentivo_extractor")

_HTML_PREFIXES = (b"<!DOCTYPE", b"<html", b"<HTML", b"<?xml")


def check_image_url(
    url: str,
    *,
    session: requests.Session | None = None,
    timeout: float = 10.0,
    retries: int = 2,
) -> dict[str, Any]:
    """
    Check a single image URL.
    Returns {url, status, http_status, final_url, error}.
    status: ok | redirected | broken | blocked | invalid
    """
    result = {
        "url": url,
        "status": "invalid",
        "http_status": "",
        "final_url": "",
        "error": "",
    }
    if not url or not urlparse(url).scheme.startswith("http"):
        result["error"] = "not_absolute_http"
        return result

    sess = session or requests.Session()
    if "User-Agent" not in sess.headers:
        sess.headers["User-Agent"] = DEFAULT_USER_AGENT

    def _head() -> requests.Response:
        return sess.head(url, timeout=timeout, allow_redirects=True)

    try:
        resp = retry_call(_head, retries=retries, backoff=1.2, logger=None)
    except Exception:
        # Fallback small GET stream
        try:

            def _get() -> requests.Response:
                return sess.get(url, timeout=timeout, allow_redirects=True, stream=True)

            resp = retry_call(_get, retries=retries, backoff=1.2, logger=None)
            # Read tiny chunk then close
            try:
                next(resp.iter_content(chunk_size=1024), None)
            finally:
                resp.close()
        except Exception as exc:
            result["status"] = "broken"
            result["error"] = str(exc)[:200]
            return result

    result["http_status"] = resp.status_code
    result["final_url"] = str(resp.url)
    if resp.status_code in (401, 403):
        result["status"] = "blocked"
    elif resp.status_code >= 400:
        result["status"] = "broken"
        result["error"] = f"HTTP {resp.status_code}"
    elif urlparse(str(resp.url)).netloc.lower() != urlparse(url).netloc.lower() or str(
        resp.url
    ).rstrip("/") != url.rstrip("/"):
        # Consider redirected if final differs (still usable if 2xx)
        result["status"] = "redirected" if resp.history else "ok"
        if not resp.history:
            result["status"] = "ok"
    else:
        result["status"] = "ok"
    if resp.history and result["status"] == "ok":
        result["status"] = "redirected"
    return result


def verify_image_url(
    url: str,
    *,
    session: requests.Session | None = None,
    timeout: float = 15.0,
    retries: int = 2,
    min_width: int = 50,
    min_height: int = 50,
    max_bytes: int = 8_000_000,
) -> dict[str, Any]:
    """
    Full verification: HTTP 200, image content-type, not HTML, minimum dimensions.
    Returns {url, ok, http_status, final_url, content_type, width, height, error}.
    """
    result: dict[str, Any] = {
        "url": url,
        "ok": False,
        "http_status": "",
        "final_url": "",
        "content_type": "",
        "width": 0,
        "height": 0,
        "error": "",
    }
    if not url or not urlparse(url).scheme.startswith("http"):
        result["error"] = "not_absolute_http"
        return result

    sess = session or requests.Session()
    if "User-Agent" not in sess.headers:
        sess.headers["User-Agent"] = DEFAULT_USER_AGENT

    def _get() -> requests.Response:
        return sess.get(url, timeout=timeout, allow_redirects=True, stream=True)

    try:
        resp = retry_call(_get, retries=retries, backoff=1.2, logger=None)
    except Exception as exc:
        result["error"] = str(exc)[:200]
        return result

    result["http_status"] = resp.status_code
    result["final_url"] = str(resp.url)
    ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    result["content_type"] = ct

    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        resp.close()
        return result

    if ct.startswith("text/html") or ct.startswith("application/xhtml"):
        result["error"] = "html_content_type"
        resp.close()
        return result

    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            chunks.append(chunk)
            size += len(chunk)
            if size >= max_bytes:
                break
    finally:
        resp.close()

    data = b"".join(chunks)
    if not data:
        result["error"] = "empty_body"
        return result

    head = data[:256].lstrip()
    for prefix in _HTML_PREFIXES:
        if head.startswith(prefix):
            result["error"] = "html_body"
            return result

    if ct and not ct.startswith("image/"):
        # Some CDNs omit type; infer from magic bytes
        if not _looks_like_image_bytes(data):
            result["error"] = f"invalid_content_type:{ct}"
            return result

    width, height = _image_dimensions(data)
    result["width"] = width
    result["height"] = height
    if width and height:
        if width < min_width or height < min_height:
            result["error"] = f"too_small:{width}x{height}"
            return result
    elif not _looks_like_image_bytes(data):
        result["error"] = "not_image_data"
        return result

    result["ok"] = True
    return result


def _looks_like_image_bytes(data: bytes) -> bool:
    if len(data) < 12:
        return False
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return True
    return False


def _image_dimensions(data: bytes) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as im:
            return int(im.width or 0), int(im.height or 0)
    except Exception:
        return 0, 0


def check_product_images(
    products: list[dict[str, Any]],
    *,
    session: requests.Session | None = None,
    timeout: float = 10.0,
    max_images: int = 500,
) -> list[dict[str, Any]]:
    """Check unique image URLs across products; return issue rows."""
    seen: set[str] = set()
    issues: list[dict[str, Any]] = []
    checked = 0
    for product in products:
        handle = product.get("handle") or ""
        urls = []
        for img in product.get("images") or []:
            src = (img.get("src") or "").strip()
            if src:
                urls.append(src)
        for v in product.get("variants") or []:
            src = str(v.get("variant_image") or "").strip()
            if src:
                urls.append(src)
        for src in urls:
            if src in seen:
                continue
            seen.add(src)
            if checked >= max_images:
                issues.append(
                    {
                        "handle": handle,
                        "url": src,
                        "status": "skipped",
                        "http_status": "",
                        "final_url": "",
                        "error": "max_images_reached",
                    }
                )
                continue
            checked += 1
            result = check_image_url(src, session=session, timeout=timeout)
            if result["status"] != "ok":
                issues.append(
                    {
                        "handle": handle,
                        "url": result["url"],
                        "status": result["status"],
                        "http_status": result["http_status"],
                        "final_url": result["final_url"],
                        "error": result["error"],
                    }
                )
    return issues
