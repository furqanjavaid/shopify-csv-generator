"""Cloudflare challenge detection + cloudscraper fetch helpers (fetch layer only)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("sentivo_extractor")

# Sticky scrapers / hosts that already needed a CF bypass.
_SCRAPERS: dict[str, Any] = {}
_CF_HOSTS: set[str] = set()

_CF_HTML_MARKERS = (
    "cf-browser-verification",
    "just a moment",
    "cf-browser-check",
    "challenge-platform",
    "cdn-cgi/challenge",
    "cf-challenge",
    "checking your browser before accessing",
    "attention required! | cloudflare",
    "sorry, you have been blocked",
)


def host_from_url(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def is_cloudflare_challenge(
    html: str | None,
    *,
    status_code: int | None = None,
) -> bool:
    """True when status is 403 or HTML looks like a Cloudflare challenge page."""
    if status_code == 403:
        return True
    text = (html or "").lower()
    if not text:
        return False
    if any(m in text for m in _CF_HTML_MARKERS):
        return True
    # Broad "challenge" only on short challenge-like pages (avoid PDP false positives).
    if "challenge" in text and (
        "cloudflare" in text or len(text) < 12_000
    ):
        return True
    return False


def is_attention_required_block(html: str | None = "", title: str = "") -> bool:
    """
    Hard Cloudflare block page: title 'Attention Required' or
    body 'Sorry, you have been blocked'.
    """
    tlow = (title or "").lower()
    body = (html or "").lower()
    if "attention required" in tlow:
        return True
    if "attention required" in body[:4000]:
        return True
    if "sorry, you have been blocked" in body:
        return True
    return False


def html_title(html: str | None) -> str:
    """Best-effort <title> extract from HTML."""
    import re

    if not html:
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()


def playwright_needs_cloudscraper(html: str | None) -> bool:
    """Playwright rule: short HTML + Cloudflare challenge indicators."""
    text = html or ""
    if len(text) >= 10_000:
        return False
    return is_cloudflare_challenge(text)


def domain_uses_cloudscraper(url: str) -> bool:
    host = host_from_url(url)
    return bool(host and host in _CF_HOSTS)


def remember_cloudscraper_host(url: str) -> None:
    host = host_from_url(url)
    if host:
        _CF_HOSTS.add(host)


def clear_cloudscraper_cache() -> None:
    """Drop sticky scraper sessions (e.g. after proxy change)."""
    _SCRAPERS.clear()


def get_cloudscraper(url: str = "") -> Any:
    """Return a cached cloudscraper session for the URL host (or a default key)."""
    try:
        import cloudscraper
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "cloudscraper is not installed — run: pip install cloudscraper"
        ) from exc

    host = host_from_url(url) or "_default"
    scraper = _SCRAPERS.get(host)
    if scraper is None:
        scraper = cloudscraper.create_scraper()
        _SCRAPERS[host] = scraper
    return scraper


def fetch_with_cloudscraper(
    url: str,
    *,
    timeout: float = 60.0,
    headers: dict[str, str] | None = None,
    proxies: dict[str, str] | None = None,
    logger_: logging.Logger | None = None,
) -> Any:
    """
    GET via cloudscraper. Remembers the host so later fetches skip straight here.
    Returns a requests-compatible Response, or None on hard CF / proxy failure.
    """
    log = logger_ or logger
    log.info("Using cloudscraper for %s (Cloudflare bypass)", url)
    remember_cloudscraper_host(url)
    try:
        scraper = get_cloudscraper(url)
    except Exception as exc:
        log.warning("cloudscraper unavailable for %s: %s", url, exc)
        return None
    # Prefer explicit proxies; else pick up HttpClient.set_proxy() / env.
    if proxies is None:
        try:
            from sentivo_extractor.core.http_client import proxy_dict

            proxies = proxy_dict()
        except Exception:
            proxies = None
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "allow_redirects": True,
    }
    if headers:
        kwargs["headers"] = headers
    if proxies:
        kwargs["proxies"] = proxies
    try:
        resp = scraper.get(url, **kwargs)
    except Exception as exc:
        log.warning("cloudscraper GET failed for %s: %s", url, exc)
        return None
    if resp is None:
        log.warning("cloudscraper returned None for %s (likely CF hard block)", url)
    return resp


def fetch_text_with_cloudscraper(
    url: str,
    *,
    timeout: float = 60.0,
    headers: dict[str, str] | None = None,
    proxies: dict[str, str] | None = None,
    logger_: logging.Logger | None = None,
) -> str:
    resp = fetch_with_cloudscraper(
        url,
        timeout=timeout,
        headers=headers,
        proxies=proxies,
        logger_=logger_,
    )
    if resp is None:
        return ""
    return str(getattr(resp, "text", None) or "")
