"""HTTP client with rate limiting, retries, robots awareness, and Cloudflare bypass."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from sentivo_extractor.core.cloudflare import (
    clear_cloudscraper_cache,
    domain_uses_cloudscraper,
    fetch_with_cloudscraper,
    is_cloudflare_challenge,
)
from sentivo_extractor.core.utils import (
    BROWSER_HEADERS,
    BROWSER_USER_AGENT,
    DEFAULT_USER_AGENT,
    RateLimiter,
    RobotsCache,
    retry_call,
)

# Optional residential / datacenter proxy for blocked storefronts.
# GUI / CLI call set_proxy(); subprocesses inherit via SENTIVO_HTTP_PROXY.
_proxy_url: str | None = None
_PROXY_ENV = "SENTIVO_HTTP_PROXY"


def set_proxy(proxy_url: str | None) -> None:
    """Set global HTTP(S) proxy used by requests + cloudscraper fetch paths."""
    global _proxy_url
    cleaned = (proxy_url or "").strip() or None
    _proxy_url = cleaned
    if cleaned:
        os.environ[_PROXY_ENV] = cleaned
    else:
        os.environ.pop(_PROXY_ENV, None)
    # New proxy → drop sticky scrapers so they pick up proxies= on next use.
    clear_cloudscraper_cache()


def get_proxy_url() -> str | None:
    if _proxy_url:
        return _proxy_url
    return (os.environ.get(_PROXY_ENV) or "").strip() or None


def get_proxy() -> str | None:
    """Return the current `_proxy_url` (GUI / env). Alias of get_proxy_url()."""
    return get_proxy_url()


def proxy_dict() -> dict[str, str] | None:
    url = get_proxy()
    if not url:
        return None
    return {"http": url, "https": url}


def _playwright_proxy_from_url(raw: str | None) -> dict[str, str] | None:
    """Parse http://user:pass@host:port into Playwright launch proxy config."""
    from urllib.parse import unquote, urlparse

    if not (raw or "").strip():
        return None
    raw = raw.strip()
    parsed = urlparse(raw)
    if not parsed.hostname:
        return {"server": raw}
    scheme = parsed.scheme or "http"
    server = f"{scheme}://{parsed.hostname}"
    if parsed.port:
        server = f"{server}:{parsed.port}"
    cfg: dict[str, str] = {"server": server}
    if parsed.username:
        cfg["username"] = unquote(parsed.username)
    if parsed.password:
        cfg["password"] = unquote(parsed.password)
    return cfg


def playwright_proxy() -> dict[str, str] | None:
    """Playwright chromium.launch(proxy=...) from get_proxy() / SENTIVO_HTTP_PROXY."""
    return _playwright_proxy_from_url(get_proxy())


def fetch_with_playwright(
    url: str,
    proxy_url: str | None = None,
    *,
    timeout_ms: int = 60_000,
    logger_: logging.Logger | None = None,
    extract_woo_gallery: bool = False,
    headless: bool = True,
) -> str | tuple[str, list[dict[str, Any]]]:
    """
    Fetch HTML via headless Chromium, optional HTTP(S) proxy.
    """
    log = logger_ or logging.getLogger("sentivo_extractor")

    proxy_cfg = (
        _playwright_proxy_from_url(proxy_url)
        if proxy_url
        else playwright_proxy()
    )

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        log.warning("Playwright unavailable for %s: %s", url, exc)
        return ("", []) if extract_woo_gallery else ""

    log.info(
        "Playwright fetch %s (proxy=%s headless=%s)",
        url,
        (proxy_cfg or {}).get("server") or ("yes" if proxy_cfg else "no"),
        bool(headless),
    )
    html = ""
    gallery: list[dict[str, Any]] = []
    try:
        with sync_playwright() as p:
            launch_kwargs: dict[str, Any] = {
                "headless": bool(headless),
            }
            if proxy_cfg:
                launch_kwargs["proxy"] = proxy_cfg
            browser = p.chromium.launch(**launch_kwargs)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(3000)
                if extract_woo_gallery:
                    try:
                        from sentivo_extractor.core.woocommerce_images import (
                            extract_woocommerce_gallery_from_page,
                        )

                        gallery = extract_woocommerce_gallery_from_page(
                            page, url, logger=log
                        )
                    except Exception as gallery_exc:
                        log.warning(
                            "[WooCommerce] In-page gallery extract failed %s: %s",
                            url,
                            gallery_exc,
                        )
                        gallery = []
                html = page.content() or ""
                log.info("Playwright fetch OK %s → %s chars", url, len(html))
            finally:
                browser.close()
    except Exception as exc:
        log.warning("Playwright fetch failed for %s: %s", url, exc)
        return ("", []) if extract_woo_gallery else ""

    if extract_woo_gallery:
        return html, gallery
    return html


def fetch_woocommerce_with_playwright(
    url: str,
    proxy_url: str | None = None,
    *,
    timeout_ms: int = 60_000,
    logger_: logging.Logger | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    WooCommerce PDP fetch: HTML + gallery images from the same proxied page.
    """
    result = fetch_with_playwright(
        url,
        proxy_url,
        timeout_ms=timeout_ms,
        logger_=logger_,
        extract_woo_gallery=True,
        headless=True,
    )
    if isinstance(result, tuple):
        return result[0] or "", list(result[1] or [])
    return result or "", []


def is_proxy_or_payment_fetch_error(exc: BaseException) -> bool:
    """True for ProxyError / HTTP 402 (common paid-proxy auth failures)."""
    try:
        import requests as req
    except Exception:
        req = None  # type: ignore[assignment]

    if req is not None and isinstance(
        exc, (req.exceptions.ProxyError, req.exceptions.ConnectTimeout)
    ):
        return True
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None) if resp is not None else None
    if status == 402:
        return True
    msg = str(exc).lower()
    if "402" in msg or "proxyerror" in msg or "tunnel connection failed" in msg:
        return True
    if "unable to connect to proxy" in msg or "proxy connection" in msg:
        return True
    return False


class HttpClient:
    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 25.0,
        delay_sec: float = 1.0,
        retries: int = 3,
        respect_robots: bool = True,
        logger: logging.Logger | None = None,
        browser_headers: bool = False,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.respect_robots = respect_robots
        self.logger = logger or logging.getLogger("sentivo_extractor")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.limiter = RateLimiter(delay_sec)
        self.robots = RobotsCache(user_agent, headers={"User-Agent": user_agent})
        self._browser_headers = False
        if browser_headers:
            self.apply_browser_headers()

    def apply_browser_headers(self) -> None:
        """Apply Magento-friendly browser headers (avoids 403 on some storefronts)."""
        self._browser_headers = True
        self.user_agent = BROWSER_USER_AGENT
        self.session.headers.update(BROWSER_HEADERS)
        self.robots = RobotsCache(self.user_agent, headers=dict(BROWSER_HEADERS))

    def _cloudscraper_get(self, url: str, **kwargs: Any) -> requests.Response:
        self.limiter.wait()
        timeout = float(kwargs.pop("timeout", self.timeout) or self.timeout)
        # Prefer a slightly longer timeout for CF solve.
        timeout = max(timeout, 60.0)
        headers = kwargs.pop("headers", None)
        if headers is None and self._browser_headers:
            headers = dict(self.session.headers)
        kwargs.pop("proxies", None)  # always use global proxy setting
        resp = fetch_with_cloudscraper(
            url,
            timeout=timeout,
            headers=headers,
            proxies=proxy_dict(),
            logger_=self.logger,
        )
        if resp is None:
            raise requests.RequestException(
                f"cloudscraper returned None for {url} (CF hard block / proxy failure)"
            )
        status = getattr(resp, "status_code", None)
        if status is not None and int(status) >= 400:
            # Avoid AttributeError if a weird mock has no raise_for_status.
            raise_for_status = getattr(resp, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
            raise requests.HTTPError(
                f"HTTP {status} for {url}", response=resp
            )
        return resp

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        if not self.robots.allowed(url, self.respect_robots):
            raise PermissionError(f"Blocked by robots.txt: {url}")

        proxies = proxy_dict()

        # Sticky: once a host needed cloudscraper, keep using it.
        if domain_uses_cloudscraper(url):
            return self._cloudscraper_get(url, **kwargs)

        def _do() -> requests.Response:
            self.limiter.wait()
            opts: dict[str, Any] = {
                "timeout": self.timeout,
                "allow_redirects": True,
            }
            opts.update(kwargs)
            if proxies:
                opts["proxies"] = proxies
                # Proxy CF solves often need a longer timeout.
                opts["timeout"] = max(float(opts.get("timeout") or self.timeout), 60.0)
            resp = self.session.get(url, **opts)
            if resp is None:
                raise requests.RequestException(f"session.get returned None for {url}")
            # Do not raise on CF 403/challenge here — fallback runs after retry_call.
            if is_cloudflare_challenge(
                resp.text or "",
                status_code=getattr(resp, "status_code", None),
            ):
                return resp
            resp.raise_for_status()
            return resp

        try:
            resp = retry_call(_do, retries=self.retries, logger=self.logger)
        except requests.HTTPError as exc:
            # Non-CF HTTP errors (404 etc.) still surface.
            r = getattr(exc, "response", None)
            status = getattr(r, "status_code", None) if r is not None else None
            body = ""
            try:
                body = (r.text or "") if r is not None else ""
            except Exception:
                body = ""
            if is_cloudflare_challenge(body, status_code=status):
                return self._cloudscraper_get(url, **kwargs)
            raise

        if resp is None:
            raise requests.RequestException(f"GET returned None for {url}")
        if is_cloudflare_challenge(
            resp.text or "",
            status_code=getattr(resp, "status_code", None),
        ):
            return self._cloudscraper_get(url, **kwargs)
        return resp

    def get_text(self, url: str, **kwargs: Any) -> str:
        resp = self.get(url, **kwargs)
        if resp is None:
            return ""
        return str(getattr(resp, "text", None) or "")

    def get_json(self, url: str, **kwargs: Any) -> Any:
        resp = self.get(url, **kwargs)
        if resp is None:
            return None
        return resp.json()
