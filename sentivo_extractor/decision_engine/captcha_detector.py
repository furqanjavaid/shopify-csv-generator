"""Detect CAPTCHA / bot-block pages and wait for a human to solve them."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlparse

# Special markers for CLI ↔ UI signalling
CAPTCHA_LOG_MARKER = "DECISION_ENGINE:CAPTCHA_BLOCKED"
CAPTCHA_WAIT_MARKER = "DECISION_ENGINE:CAPTCHA_WAITING"
CAPTCHA_CLEARED_MARKER = "DECISION_ENGINE:CAPTCHA_CLEARED"
CAPTCHA_TIMEOUT_MARKER = "DECISION_ENGINE:CAPTCHA_TIMEOUT"
CAPTCHA_EXIT_CODE = 75  # unused — scrape no longer exits on CAPTCHA

POLL_INTERVAL_SEC = 2.0
SOLVE_TIMEOUT_SEC = 300.0  # 5 minutes

_CAPTCHA_KEYWORDS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "cf-challenge",
    "cf-browser-check",
    "challenge-platform",
    "attention required",
    "verify you are human",
    "verify you are a human",
    "are you a robot",
    "i'm not a robot",
    "security check",
    "access denied",
    "just a moment",
    "checking your browser",
    "enable javascript and cookies",
    "ray id",
    "cloudflare",
    "ddos protection",
    "bot detection",
    "perimeterx",
    "datadome",
    "please complete the security check",
)

_TITLE_BLOCKED = (
    "just a moment",
    "attention required",
    "access denied",
    "verify you are human",
    "security check",
    "cloudflare",
    "captcha",
    "are you a robot",
)

_BLOCK_STATUS = frozenset({403, 429, 503})

_PRODUCT_SIGNALS = (
    "application/ld+json",
    "og:type",
    "product",
    "itemprop",
    "add to cart",
    "add-to-cart",
    "product-title",
    "product_title",
)


class CaptchaBlockedError(RuntimeError):
    """Raised when a CAPTCHA / rate-limit / Cloudflare challenge is detected."""

    def __init__(
        self,
        message: str,
        *,
        url: str = "",
        status_code: int | None = None,
        reason: str = "captcha_or_block",
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code
        self.reason = reason


class CaptchaTimeoutError(RuntimeError):
    """CAPTCHA was not solved within the wait window."""

    def __init__(self, message: str, *, url: str = "") -> None:
        super().__init__(message)
        self.url = url
        self.reason = "captcha_timeout_site_blocked"


class PlaywrightLiveSession:
    """Persistent visible Chromium session (cookies/state kept across URLs)."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self._manager: Any = None
        self._pw: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self.network_json: list[Any] = []
        self._on_response: Any = None

    @property
    def alive(self) -> bool:
        return self.page is not None and self.browser is not None

    def ensure_visible(self, *, user_agent: str = "") -> None:
        if self.alive:
            return
        from playwright.sync_api import sync_playwright

        self._manager = sync_playwright()
        self._pw = self._manager.start()
        self.browser = self._pw.chromium.launch(headless=False)
        opts: dict[str, Any] = {"viewport": {"width": 1440, "height": 900}}
        if user_agent:
            opts["user_agent"] = user_agent
        self.context = self.browser.new_context(**opts)
        self.page = self.context.new_page()
        self.logger.info("Opened visible Playwright window for CAPTCHA / remaining URLs")

    def goto(self, url: str, *, timeout_ms: int = 30000) -> Any:
        self.ensure_visible()
        self.network_json = []
        page = self.page
        if self._on_response is not None:
            try:
                page.remove_listener("response", self._on_response)
            except Exception:
                pass

        def on_response(response: Any) -> None:
            try:
                ct = (response.headers.get("content-type") or "").lower()
                rurl = (response.url or "").lower()
                if "json" not in ct and not any(
                    x in rurl for x in ("/products", "variant", "graphql", "api/")
                ):
                    return
                if response.status >= 400:
                    return
                data = response.json()
                self.network_json.append({"url": response.url, "data": data})
            except Exception:
                return

        self._on_response = on_response
        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return page

    def snapshot(self) -> dict[str, Any]:
        page = self.page
        html = ""
        title = ""
        try:
            html = page.content() if page else ""
        except Exception:
            html = ""
        try:
            title = page.title() if page else ""
        except Exception:
            title = ""
        screenshot_png = None
        try:
            screenshot_png = page.screenshot(type="png", full_page=False)
        except Exception:
            screenshot_png = None
        return {
            "html": html,
            "title": title,
            "network_json": list(self.network_json),
            "screenshot_png": screenshot_png,
            "variant_probe": None,
        }

    def render_url(self, url: str, *, timeout_ms: int = 30000) -> dict[str, Any]:
        self.goto(url, timeout_ms=timeout_ms)
        try:
            self.page.wait_for_timeout(1500)
        except Exception:
            pass
        return self.snapshot()

    def apply_cookies_to_requests(self, session: Any) -> None:
        if session is None or self.context is None:
            return
        try:
            for cookie in self.context.cookies():
                name = cookie.get("name")
                value = cookie.get("value")
                if not name:
                    continue
                domain = str(cookie.get("domain") or "").lstrip(".")
                path = cookie.get("path") or "/"
                try:
                    session.cookies.set(name, value, domain=domain or None, path=path)
                except Exception:
                    try:
                        session.cookies.set(name, value)
                    except Exception:
                        pass
        except Exception as exc:
            self.logger.warning("Could not copy Playwright cookies: %s", exc)

    def close(self) -> None:
        for obj in (self.page, self.context, self.browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self.page = None
        self.context = None
        self.browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw = None
        self._manager = None


class CaptchaDetector:
    """
    Detect status 403/429, CAPTCHA keywords, and Cloudflare challenges.
    On detection, open a visible browser and poll until the user solves it.
    """

    poll_interval = POLL_INTERVAL_SEC
    solve_timeout = SOLVE_TIMEOUT_SEC

    def inspect(
        self,
        *,
        status_code: int | None = None,
        html: str = "",
        url: str = "",
        headers: dict[str, str] | None = None,
        title: str = "",
    ) -> dict[str, Any]:
        reasons: list[str] = []
        code = int(status_code) if status_code is not None else None
        if code in _BLOCK_STATUS:
            reasons.append(f"http_{code}")

        hdrs = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
        server = hdrs.get("server", "")
        if "cloudflare" in server and code in (403, 429, 503):
            reasons.append("cloudflare_server")
        if "cf-ray" in hdrs and code in (403, 429, 503):
            reasons.append("cloudflare_ray")

        low = (html or "").lower()
        hits = [kw for kw in _CAPTCHA_KEYWORDS if kw in low]
        strong = [
            h
            for h in hits
            if h
            not in (
                "cloudflare",
                "ray id",
            )
        ]
        if strong:
            reasons.append("captcha_keywords:" + ",".join(strong[:5]))
        elif hits and code in _BLOCK_STATUS:
            reasons.append("block_page_keywords:" + ",".join(hits[:5]))

        if ("cf-challenge" in low or "challenge-platform" in low) and (
            code in _BLOCK_STATUS or "just a moment" in low
        ):
            if "cloudflare_challenge" not in reasons:
                reasons.append("cloudflare_challenge")

        tlow = (title or "").lower()
        if any(tok in tlow for tok in _TITLE_BLOCKED):
            reasons.append(f"blocked_title:{title[:80]}")

        blocked = bool(reasons)
        return {
            "blocked": blocked,
            "reasons": reasons,
            "status_code": code,
            "url": url,
            "title": title,
            "message": (
                f"CAPTCHA/block detected ({'; '.join(reasons)})"
                if blocked
                else ""
            ),
        }

    def page_is_clear(self, *, html: str = "", title: str = "") -> bool:
        """True when challenge keywords are gone and the page looks like real content."""
        probe = self.inspect(html=html, title=title, status_code=None)
        if probe["blocked"]:
            return False
        body = (html or "").lower()
        if len(body) < 400:
            return False
        if any(sig in body for sig in _PRODUCT_SIGNALS):
            return True
        # Generic storefront / HTML document without challenge markers.
        return "<html" in body and "just a moment" not in body

    def raise_if_blocked(
        self,
        *,
        status_code: int | None = None,
        html: str = "",
        url: str = "",
        headers: dict[str, str] | None = None,
        title: str = "",
    ) -> None:
        result = self.inspect(
            status_code=status_code,
            html=html,
            url=url,
            headers=headers,
            title=title,
        )
        if result["blocked"]:
            raise CaptchaBlockedError(
                result["message"],
                url=url,
                status_code=status_code,
                reason=";".join(result["reasons"]),
            )

    def wait_until_cleared(
        self,
        session: PlaywrightLiveSession,
        url: str,
        *,
        logger: logging.Logger | None = None,
        timeout_sec: float | None = None,
        interval_sec: float | None = None,
    ) -> dict[str, Any]:
        """
        Poll the visible page every 2s until the CAPTCHA is gone, or time out.

        Returns a Playwright snapshot dict. Raises CaptchaTimeoutError.
        """
        log = logger or logging.getLogger(__name__)
        timeout_sec = float(self.solve_timeout if timeout_sec is None else timeout_sec)
        interval_sec = float(self.poll_interval if interval_sec is None else interval_sec)
        deadline = time.monotonic() + timeout_sec

        msg = (
            f"{CAPTCHA_WAIT_MARKER} CAPTCHA detected at {url}. "
            "Solve it in the browser window. Scraping will resume automatically."
        )
        log.warning(msg)
        print(msg, flush=True)

        session.ensure_visible()
        try:
            session.goto(url)
        except Exception as exc:
            log.warning("Visible browser navigation failed (%s) — still polling", exc)

        last_title = ""
        while time.monotonic() < deadline:
            snap = session.snapshot()
            html = str(snap.get("html") or "")
            title = str(snap.get("title") or "")
            if title and title != last_title:
                log.info("CAPTCHA poll title=%s", title[:80])
                last_title = title
            if self.page_is_clear(html=html, title=title):
                done = (
                    f"{CAPTCHA_CLEARED_MARKER} CAPTCHA cleared at {url} — resuming scrape."
                )
                log.info(done)
                print(done, flush=True)
                return snap
            remaining = int(deadline - time.monotonic())
            log.info("CAPTCHA still present — polling again (%ss left)", max(0, remaining))
            time.sleep(interval_sec)

        fail = (
            f"{CAPTCHA_TIMEOUT_MARKER} CAPTCHA not solved within "
            f"{int(timeout_sec)}s at {url} — marking site blocked."
        )
        log.error(fail)
        print(fail, flush=True)
        raise CaptchaTimeoutError(fail, url=url)

    @staticmethod
    def domain_of(url: str) -> str:
        try:
            return (urlparse(url).netloc or "").lower().removeprefix("www.")
        except Exception:
            return ""
