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
MIN_INDICATORS = 2  # require ≥2 signals — bare "captcha" in JS never counts alone

# Title-only challenge phrases (do not include bare "captcha" / "cloudflare").
_TITLE_BLOCKED = (
    "just a moment",
    "attention required",
    "access denied",
    "security check",
    "verify you are human",
    "verify you are a human",
)

# Active challenge page markers (NOT "captcha"/"recaptcha"/"hcaptcha" alone —
# those appear in dormant Google scripts on normal storefronts).
_ACTIVE_CHALLENGE_MARKERS = (
    "cf-challenge",
    "cf-browser-check",
    "challenge-platform",
    "just a moment",
    "attention required",
    "checking your browser",
    "enable javascript and cookies",
    "verify you are human",
    "verify you are a human",
    "please complete the security check",
    "ddos protection by cloudflare",
)

_VISIBLE_CHALLENGE_SELECTORS = (
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[src*="recaptcha/api2/bframe"]',
    'iframe[src*="hcaptcha.com/captcha"]',
    'iframe[title*="challenge" i]',
    "#challenge-form",
    "#challenge-stage",
    ".cf-challenge-running",
    "#cf-challenge-running",
    "[data-ray]",
)

_BLOCK_STATUS = frozenset({403, 429})

_PRODUCT_SIGNALS = (
    "application/ld+json",
    '"@type":"product"',
    "'@type':'product'",
    'og:type" content="product',
    "itemprop=\"price\"",
    "itemprop='price'",
    "add to cart",
    "add-to-cart",
    "product-title",
    "product_title",
    "woocommerce-Price-amount",
    "shopify-section",
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
        from sentivo_extractor.core.utils import BROWSER_HEADERS, BROWSER_USER_AGENT

        opts: dict[str, Any] = {
            "viewport": {"width": 1440, "height": 900},
            "user_agent": user_agent or BROWSER_USER_AGENT,
            "extra_http_headers": {
                k: v for k, v in BROWSER_HEADERS.items() if k.lower() != "user-agent"
            },
        }
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

    def has_visible_challenge_iframe(self) -> bool:
        """True only if a challenge iframe/form is visible (not merely present in HTML)."""
        page = self.page
        if page is None:
            return False
        for sel in _VISIBLE_CHALLENGE_SELECTORS:
            try:
                loc = page.locator(sel)
                if loc.count() < 1:
                    continue
                if loc.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def has_cf_clearance(self) -> bool:
        if self.context is None:
            return False
        try:
            for cookie in self.context.cookies():
                if str(cookie.get("name") or "") == "cf_clearance":
                    return True
        except Exception:
            pass
        return False

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
            "visible_challenge_iframe": self.has_visible_challenge_iframe(),
            "cf_clearance": self.has_cf_clearance(),
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
    Detect active CAPTCHA / bot-block pages with a multi-indicator threshold.

    Bare "captcha" / reCAPTCHA script tags on a normal storefront do NOT count.
    Requires at least MIN_INDICATORS strong signals before pausing for a solve.
    """

    poll_interval = POLL_INTERVAL_SEC
    solve_timeout = SOLVE_TIMEOUT_SEC
    min_indicators = MIN_INDICATORS

    @staticmethod
    def has_product_content(html: str) -> bool:
        low = (html or "").lower()
        if len(low) < 400:
            return False
        return any(sig.lower() in low for sig in _PRODUCT_SIGNALS)

    @staticmethod
    def _active_challenge_markers(html: str) -> list[str]:
        low = (html or "").lower()
        return [m for m in _ACTIVE_CHALLENGE_MARKERS if m in low]

    @staticmethod
    def _cookie_names(cookies: Any) -> set[str]:
        names: set[str] = set()
        if not cookies:
            return names
        if isinstance(cookies, dict):
            names.update(str(k) for k in cookies)
            return names
        try:
            for item in cookies:
                if isinstance(item, dict) and item.get("name"):
                    names.add(str(item["name"]))
                else:
                    names.add(str(getattr(item, "name", "") or item))
        except Exception:
            pass
        return {n for n in names if n}

    def inspect(
        self,
        *,
        status_code: int | None = None,
        html: str = "",
        url: str = "",
        headers: dict[str, str] | None = None,
        title: str = "",
        cookies: Any = None,
        visible_challenge_iframe: bool = False,
        cf_clearance: bool | None = None,
        logger: logging.Logger | None = None,
    ) -> dict[str, Any]:
        """
        Score challenge indicators. ``blocked`` is True only when
        ``len(indicators) >= min_indicators`` (default 2).
        """
        indicators: list[str] = []
        ignored: list[str] = []
        code = int(status_code) if status_code is not None else None
        low = (html or "").lower()
        hdrs = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
        cookie_names = self._cookie_names(cookies)
        has_clearance = (
            bool(cf_clearance)
            if cf_clearance is not None
            else ("cf_clearance" in cookie_names)
        )
        has_product = self.has_product_content(html)
        markers = self._active_challenge_markers(html)

        # Ignore dormant captcha/recaptcha/hcaptcha script mentions entirely.
        if "captcha" in low or "recaptcha" in low or "hcaptcha" in low:
            ignored.append("dormant_captcha_script_mention")

        # 1) Hard HTTP blocks
        if code in _BLOCK_STATUS:
            indicators.append(f"http_status:{code}")

        # 2) Cloudflare challenge page (active markers; cf_clearance missing)
        cf_html = any(
            m in low
            for m in (
                "cf-challenge",
                "challenge-platform",
                "cf-browser-check",
                "cdn-cgi/challenge",
            )
        )
        cf_title = "just a moment" in (title or "").lower()
        cf_headers = ("cf-ray" in hdrs) or ("cloudflare" in hdrs.get("server", ""))
        if cf_html or (cf_title and (cf_headers or code in _BLOCK_STATUS)):
            if not has_clearance:
                indicators.append("cloudflare_challenge_page")
            else:
                ignored.append("cloudflare_markers_but_cf_clearance_present")

        # 3) Visible / interactable challenge iframe (Playwright-confirmed)
        if visible_challenge_iframe:
            indicators.append("visible_captcha_iframe")

        # 4) Blocked page title
        tlow = (title or "").lower().strip()
        for tok in _TITLE_BLOCKED:
            if tok in tlow:
                indicators.append(f"blocked_title:{title[:80] or tok}")
                break

        # 5) No product content AND active challenge markers in body
        if (not has_product) and markers:
            indicators.append(
                "no_product_content+challenge_markers:" + ",".join(markers[:4])
            )

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for item in indicators:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        indicators = unique

        blocked = len(indicators) >= self.min_indicators
        message = (
            f"CAPTCHA/block detected ({len(indicators)} indicators: "
            f"{'; '.join(indicators)})"
            if blocked
            else ""
        )

        log = logger or logging.getLogger(__name__)
        if blocked:
            log.warning(
                "CAPTCHA trigger url=%s indicators=%s ignored=%s",
                url or "-",
                indicators,
                ignored,
            )
            print(
                f"{CAPTCHA_WAIT_MARKER} reason={'; '.join(indicators)} url={url}",
                flush=True,
            )
        elif indicators:
            log.info(
                "CAPTCHA soft signals (below threshold %s) url=%s indicators=%s ignored=%s",
                self.min_indicators,
                url or "-",
                indicators,
                ignored,
            )

        return {
            "blocked": blocked,
            "reasons": indicators,
            "indicators": indicators,
            "indicator_count": len(indicators),
            "min_indicators": self.min_indicators,
            "ignored": ignored,
            "has_product_content": has_product,
            "status_code": code,
            "url": url,
            "title": title,
            "message": message,
        }

    def page_is_clear(
        self,
        *,
        html: str = "",
        title: str = "",
        visible_challenge_iframe: bool = False,
        cf_clearance: bool | None = None,
    ) -> bool:
        """True when the multi-indicator check says we are not on a challenge page."""
        probe = self.inspect(
            html=html,
            title=title,
            status_code=None,
            visible_challenge_iframe=visible_challenge_iframe,
            cf_clearance=cf_clearance,
        )
        if probe["blocked"]:
            return False
        body = (html or "").lower()
        if len(body) < 200:
            return False
        return True

    def raise_if_blocked(
        self,
        *,
        status_code: int | None = None,
        html: str = "",
        url: str = "",
        headers: dict[str, str] | None = None,
        title: str = "",
        cookies: Any = None,
        visible_challenge_iframe: bool = False,
        cf_clearance: bool | None = None,
    ) -> None:
        result = self.inspect(
            status_code=status_code,
            html=html,
            url=url,
            headers=headers,
            title=title,
            cookies=cookies,
            visible_challenge_iframe=visible_challenge_iframe,
            cf_clearance=cf_clearance,
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
        trigger_reasons: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Poll the visible page every 2s until the CAPTCHA is gone, or time out.

        Returns a Playwright snapshot dict. Raises CaptchaTimeoutError.
        """
        log = logger or logging.getLogger(__name__)
        timeout_sec = float(self.solve_timeout if timeout_sec is None else timeout_sec)
        interval_sec = float(self.poll_interval if interval_sec is None else interval_sec)
        deadline = time.monotonic() + timeout_sec
        reason_txt = "; ".join(trigger_reasons or []) or "active_challenge"

        msg = (
            f"{CAPTCHA_WAIT_MARKER} CAPTCHA detected at {url} "
            f"(reasons: {reason_txt}). "
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
            visible = bool(snap.get("visible_challenge_iframe"))
            clearance = bool(snap.get("cf_clearance"))
            if title and title != last_title:
                log.info("CAPTCHA poll title=%s", title[:80])
                last_title = title
            if self.page_is_clear(
                html=html,
                title=title,
                visible_challenge_iframe=visible,
                cf_clearance=clearance,
            ):
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
