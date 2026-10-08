"""Shared utilities for the universal extractor."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from app.utils.helpers import slugify

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; SentivoExtractor/1.0; +https://sentivo.tools) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Browser-like headers for Magento / anti-bot storefronts (e.g. sheetplastics.co.uk).
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.google.com/",
    "DNT": "1",
}

PERMANENT_HTTP_STATUS = frozenset({400, 401, 403, 404, 410})
TRANSIENT_HTTP_STATUS = frozenset({408, 429, 500, 502, 503, 504})

OPTION_NAME_ALIASES = {
    "size": "Size",
    "colour": "Color",
    "color": "Color",
    "material": "Material",
    "pack": "Pack Size",
    "pack size": "Pack Size",
    "style": "Style",
    "length": "Length",
    "width": "Width",
    "thickness": "Thickness",
    "finish": "Finish",
}


def configure_stdio_utf8() -> None:
    """Force stdout/stderr to UTF-8 so arrows and other Unicode don't crash cp1252."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def setup_logger(name: str = "sentivo_extractor", log_dir: Path | None = None) -> logging.Logger:
    configure_stdio_utf8()
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    # Avoid duplicate stream handlers
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "extractor.log"
        # Replace existing file handlers pointing at extractor.log
        for h in list(logger.handlers):
            if isinstance(h, logging.FileHandler):
                try:
                    h.close()
                except Exception:
                    pass
                logger.removeHandler(h)
        fh = logging.FileHandler(log_path, encoding="utf-8-sig")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


def close_logger(logger: logging.Logger) -> None:
    for h in list(logger.handlers):
        try:
            h.close()
        except Exception:
            pass
        logger.removeHandler(h)


def absolute_url(base: str, href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith("//"):
        scheme = urlparse(base).scheme or "https"
        return f"{scheme}:{href}"
    return urljoin(base, href)


def normalize_price(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # Strip currency symbols / codes and thousands separators before float parse.
    text = text.replace(",", "")
    for sym in ("£", "$", "€", "¥", "₹"):
        text = text.replace(sym, "")
    text = re.sub(r"(?i)\b(gbp|usd|eur|cad|aud)\b", "", text)
    text = text.strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return ""
    try:
        return f"{float(match.group(0)):.2f}"
    except ValueError:
        return match.group(0)


def is_blank_price(value: Any) -> bool:
    """True when price is missing or numerically zero (0 / 0.0 / 0.00)."""
    text = normalize_price(value)
    if not text:
        return True
    try:
        return abs(float(text)) < 1e-9
    except ValueError:
        return True


_CURRENCY_PRICE_RE = re.compile(r"[£$€]\s*\d+(?:[.,]\d{1,2})?")


def resolve_price_from_html(html: str) -> tuple[str, str]:
    """
    Fallback price extraction when primary sources are empty/zero.

    Order: JSON-LD offers.price → meta product:price:amount →
    CSS (.price, .product-price, [data-price]) → currency+digits text.
    Returns (normalized_price, source_label) or ("", "").
    """
    if not (html or "").strip():
        return "", ""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return "", ""

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return "", ""

    # 1. JSON-LD offers.price
    for tag in soup.select('script[type="application/ld+json"]'):
        text = (tag.string or tag.get_text() or "").strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except Exception:
            try:
                cleaned = re.sub(r",\s*}", "}", text)
                cleaned = re.sub(r",\s*]", "]", cleaned)
                data = json.loads(cleaned)
            except Exception:
                continue
        nodes: list[Any] = []
        if isinstance(data, list):
            nodes = [n for n in data if isinstance(n, dict)]
        elif isinstance(data, dict):
            nodes = [data]
            graph = data.get("@graph")
            if isinstance(graph, list):
                nodes.extend(n for n in graph if isinstance(n, dict))
        for node in nodes:
            offers = node.get("offers")
            offer_list: list[Any]
            if isinstance(offers, list):
                offer_list = offers
            elif isinstance(offers, dict):
                offer_list = [offers]
            else:
                continue
            for off in offer_list:
                if not isinstance(off, dict):
                    continue
                raw = off.get("price") if off.get("price") is not None else off.get("lowPrice")
                price = normalize_price(raw)
                if not is_blank_price(price):
                    return price, "JSON-LD offers.price"

    # 2. meta[property="product:price:amount"]
    for attrs in (
        {"property": "product:price:amount"},
        {"name": "product:price:amount"},
        {"property": "og:price:amount"},
        {"name": "og:price:amount"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content") is not None:
            price = normalize_price(tag.get("content"))
            if not is_blank_price(price):
                return price, 'meta[property="product:price:amount"]'

    # 3. CSS selectors
    for sel in (".price", ".product-price", "[data-price]"):
        try:
            nodes = soup.select(sel)
        except Exception:
            continue
        for el in nodes:
            raw = el.get("content") or el.get("data-price") or el.get_text(" ", strip=True)
            price = normalize_price(raw)
            if not is_blank_price(price):
                return price, sel

    # 4. Currency symbol + digits anywhere in page text
    for match in _CURRENCY_PRICE_RE.finditer(html):
        price = normalize_price(match.group(0))
        if not is_blank_price(price):
            return price, "currency_text"

    return "", ""


_INVALID_SKU_PHRASES = (
    "only %",
    "left",
    "in stock",
    "out of stock",
)
_SKU_ALLOWED_RE = re.compile(r"^[A-Za-z0-9\-/]+$")


def sanitize_sku(
    value: Any,
    *,
    logger: logging.Logger | None = None,
) -> str:
    """
    Return a cleaned SKU, or "" when the value looks like stock/availability copy
    or contains illegal characters (anything except letters, digits, -, /).
    """
    text = str(value or "").strip()
    if not text:
        return ""
    low = text.lower()
    invalid = any(phrase in low for phrase in _INVALID_SKU_PHRASES)
    if not invalid and not _SKU_ALLOWED_RE.match(text):
        invalid = True
    if invalid:
        log = logger or logging.getLogger(__name__)
        log.info("Invalid SKU rejected: %s", text)
        return ""
    return text


def detect_currency(text: str) -> str:
    if not text:
        return ""
    if "£" in text or "GBP" in text.upper():
        return "GBP"
    if "€" in text or "EUR" in text.upper():
        return "EUR"
    if "$" in text or "USD" in text.upper():
        return "USD"
    return ""


def canonicalize_option_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    key = raw.lower()
    return OPTION_NAME_ALIASES.get(key, raw[:1].upper() + raw[1:])


def stable_handle(title: str, url: str = "") -> str:
    handle = slugify(title)
    if handle:
        return handle
    path = urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1] if path else ""
    return slugify(slug) or "product"


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8-sig")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


class RateLimiter:
    def __init__(self, delay_sec: float = 1.0) -> None:
        self.delay_sec = max(0.0, float(delay_sec))
        self._last = 0.0

    def wait(self) -> None:
        if self.delay_sec <= 0:
            return
        elapsed = time.monotonic() - self._last
        if elapsed < self.delay_sec:
            time.sleep(self.delay_sec - elapsed)
        self._last = time.monotonic()


class RobotsCache:
    """
    robots.txt cache.

    Fetches via requests with the configured User-Agent (not bare urllib) so
    Cloudflare bot blocks on /robots.txt do not falsely set disallow_all.
    On 5xx / empty / non-robots bodies: fail open (allow).
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.headers = dict(headers or {"User-Agent": user_agent})
        self._parsers: dict[str, RobotFileParser | None] = {}

    def _load(self, origin: str) -> RobotFileParser | None:
        robots_url = f"{origin.rstrip('/')}/robots.txt"
        if requests is None:
            return None
        try:
            resp = requests.get(
                robots_url,
                headers=self.headers,
                timeout=12,
                allow_redirects=True,
            )
        except Exception:
            return None  # fail open

        # Transient / gateway / empty → allow crawl (do not mirror urllib's
        # 403→disallow_all which Cloudflare triggers for bot UAs).
        if resp.status_code >= 500 or resp.status_code in (408, 429):
            return None
        if resp.status_code in (401, 403):
            body = (resp.text or "").strip().lower()
            # Cloudflare / WAF challenge pages are not real robots policies.
            if (
                not body
                or "cloudflare" in body
                or "cf-ray" in body
                or body.startswith("<!DOCTYPE")
                or "error code" in body
                or len(body) < 40
            ):
                return None
            rp = RobotFileParser()
            rp.disallow_all = True
            return rp
        if resp.status_code >= 400:
            return None

        text = resp.text or ""
        # HTML error pages served as 200 are not robots.txt.
        low = text.lstrip()[:200].lower()
        if low.startswith("<!DOCTYPE") or low.startswith("<html"):
            return None

        rp = RobotFileParser()
        rp.set_url(robots_url)
        try:
            rp.parse(text.splitlines())
        except Exception:
            return None
        return rp

    def allowed(self, url: str, respect: bool = True) -> bool:
        if not respect:
            return True
        try:
            parsed = urlparse(url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin not in self._parsers:
                self._parsers[origin] = self._load(origin)
            rp = self._parsers.get(origin)
            if rp is None:
                return True
            return bool(rp.can_fetch(self.user_agent, url))
        except Exception:
            return True


def http_status_from_exception(exc: BaseException) -> int | None:
    """Extract HTTP status code from requests errors or message text."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if isinstance(code, int):
            return code
    text = str(exc)
    m = re.search(r"\b([45]\d{2})\s+(?:Client|Server)\s+Error\b", text)
    if m:
        return int(m.group(1))
    m = re.search(r"\bHTTP\s+([45]\d{2})\b", text, flags=re.I)
    if m:
        return int(m.group(1))
    return None


def is_permanent_http_failure(exc: BaseException) -> bool:
    code = http_status_from_exception(exc)
    return code in PERMANENT_HTTP_STATUS if code is not None else False


def is_transient_failure(exc: BaseException) -> bool:
    """True only for retriable network / HTTP failures."""
    if requests is not None:
        if isinstance(
            exc,
            (
                requests.ConnectionError,
                requests.Timeout,
                requests.exceptions.ChunkedEncodingError,
            ),
        ):
            return True
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    code = http_status_from_exception(exc)
    return code in TRANSIENT_HTTP_STATUS if code is not None else False


def retry_call(fn, *, retries: int = 3, backoff: float = 1.5, logger: logging.Logger | None = None):
    """
    Retry only transient failures (408/429/5xx, ConnectionError, Timeout).
    Permanent HTTP codes (400/401/403/404/410) are attempted once.
    """
    last_exc: Exception | None = None
    attempts = max(1, int(retries))
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if is_permanent_http_failure(exc):
                if logger:
                    logger.info(
                        "Permanent HTTP failure - skipping retries. %s",
                        exc,
                    )
                raise
            if not is_transient_failure(exc):
                if logger:
                    logger.info(
                        "Non-retriable failure - skipping retries. %s",
                        exc,
                    )
                raise
            if logger:
                logger.warning("Attempt %s/%s failed: %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(backoff ** (attempt - 1))
    assert last_exc is not None
    raise last_exc
