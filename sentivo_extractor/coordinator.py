"""
Decision Engine coordinator — wraps PDPExtractionPipeline without rewriting it.

Entry point used by UniversalCrawler.extract_one.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from sentivo_extractor.core.pdp_pipeline import (
    SOURCE_PLAYWRIGHT,
    PDPExtractionPipeline,
)
from sentivo_extractor.core.schema import ensure_product
from sentivo_extractor.core.woocommerce_images import (
    apply_woocommerce_image_cap,
    resolve_woocommerce_product_images,
)
from sentivo_extractor.core.woocommerce_title import apply_woocommerce_title_and_handle
from sentivo_extractor.decision_engine.captcha_detector import (
    CAPTCHA_WAIT_MARKER,
    CaptchaBlockedError,
    CaptchaDetector,
    CaptchaTimeoutError,
    PlaywrightLiveSession,
    close_shared_live_session,
    get_shared_live_session,
    peek_shared_live_session,
)
from sentivo_extractor.decision_engine.confidence_scorer import ConfidenceScorer
from sentivo_extractor.decision_engine.fallback_manager import FallbackManager
from sentivo_extractor.decision_engine.magento_browser import (
    close_shared_magento_pool,
    shared_magento_pool,
)
from sentivo_extractor.decision_engine.retry_handler import RetryHandler
from sentivo_extractor.decision_engine.strategy_selector import StrategySelector

# Shared across crawler instances so domain winners persist for a process run.
_SHARED_SELECTOR = StrategySelector()
_SHARED_PW_SESSION: PlaywrightLiveSession | None = None


class DecisionCoordinator:
    """
    Intelligent coordinator over the existing HTTP / Playwright extractors.

    Flow per URL:
      1. Soft-fetch HTML + CAPTCHA detection (visible browser + poll until solved)
      2. Platform detect → strategy order (domain memory)
      3. Fallback chain (2 attempts each) until confidence ≥ 70%
      4. Finalize via existing PDP pipeline helpers (variants/images/report)
      5. URL-level retries with exponential backoff (max 3)
    """

    def __init__(
        self,
        *,
        http: Any,
        options: dict[str, Any],
        registry: Any,
        site_rules: dict[str, Any] | None = None,
        logger: logging.Logger | None = None,
        selector: StrategySelector | None = None,
    ) -> None:
        self.http = http
        self.options = options
        self.registry = registry
        self.site_rules = site_rules or {}
        self.logger = logger or logging.getLogger(__name__)
        self.selector = selector or _SHARED_SELECTOR
        self.scorer = ConfidenceScorer()
        self.captcha = CaptchaDetector()
        self.retry = RetryHandler(logger=self.logger)
        self.fallback = FallbackManager(scorer=self.scorer, logger=self.logger)
        self._last_configurator_pricing: dict[str, Any] | None = None
        self._woo_gallery_images: list[dict[str, Any]] = []

    @classmethod
    def shared_playwright(cls) -> PlaywrightLiveSession | None:
        return peek_shared_live_session() or _SHARED_PW_SESSION

    @classmethod
    def shared_selector(cls) -> StrategySelector:
        return _SHARED_SELECTOR

    @classmethod
    def remember_platform(cls, url_or_domain: str, platform: str) -> None:
        _SHARED_SELECTOR.remember_platform(url_or_domain, platform)

    @classmethod
    def close_shared_session(cls) -> None:
        global _SHARED_PW_SESSION
        close_shared_live_session()
        _SHARED_PW_SESSION = None
        close_shared_magento_pool()

    def _live_session(self) -> PlaywrightLiveSession:
        global _SHARED_PW_SESSION
        session = get_shared_live_session(self.logger)
        _SHARED_PW_SESSION = session
        return session

    def extract(
        self,
        url: str,
        *,
        html: str | None = None,
        platform: str | None = None,
        configurator_pricing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract one URL with retries. CaptchaTimeoutError is not retried."""

        def _once() -> dict[str, Any]:
            return self._extract_once(
                url,
                html=html,
                platform=platform,
                configurator_pricing=configurator_pricing,
            )

        def _ok(outcome: dict[str, Any]) -> bool:
            return bool(outcome.get("success"))

        try:
            result, reason = self.retry.run(url, _once, is_success=_ok)
        except CaptchaTimeoutError as exc:
            reason = getattr(exc, "reason", None) or "captcha_timeout_site_blocked"
            return {
                "success": False,
                "product": None,
                "failed_record": {"source_url": url, "_fail_reason": reason},
                "reason": reason,
                "report": None,
                "decision_engine": {"failed": True, "reason": reason},
            }
        if result is None:
            return {
                "success": False,
                "product": None,
                "failed_record": {"source_url": url, "_fail_reason": reason},
                "reason": reason or "extraction_failed",
                "report": None,
                "decision_engine": {"failed": True, "reason": reason},
            }
        if not result.get("success") and reason:
            result = dict(result)
            result.setdefault("reason", reason)
        return result

    # ── single attempt ────────────────────────────────────

    def _extract_once(
        self,
        url: str,
        *,
        html: str | None = None,
        platform: str | None = None,
        configurator_pricing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pipeline = PDPExtractionPipeline(
            http=self.http,
            options=self.options,
            registry=self.registry,
            site_rules=self.site_rules,
            logger=self.logger,
        )

        # Prefer pinned / remembered platform — never re-detect Magento on Woo domains.
        from sentivo_extractor.decision_engine.strategy_selector import (
            is_woocommerce_url_hint,
        )

        remembered = self.selector.platform_for(url)
        if platform:
            resolved_platform = str(platform)
            if resolved_platform in (
                "Magento",
                "Shopify",
                "WooCommerce",
                "BigCommerce",
                "Next.js",
            ):
                self.selector.remember_platform(url, resolved_platform)
        elif remembered:
            resolved_platform = remembered
        elif is_woocommerce_url_hint(url):
            resolved_platform = "WooCommerce"
            self.selector.remember_platform(url, "WooCommerce")
        else:
            detected = self.selector.detect(
                url,
                html=html or "",
                session=getattr(self.http, "session", None),
                live_fallback=not bool(html),
            )
            resolved_platform = detected["platform"]
            if resolved_platform in (
                "Magento",
                "Shopify",
                "WooCommerce",
                "BigCommerce",
                "Next.js",
            ):
                self.selector.remember_platform(url, resolved_platform)

        platform = resolved_platform
        # WooCommerce domain lock: never flip to Magento mid-run.
        if remembered == "WooCommerce" or is_woocommerce_url_hint(url):
            platform = "WooCommerce"
            self.selector.remember_platform(url, "WooCommerce")

        price_optional = platform == "Magento"
        strategies = self.selector.strategies_for(platform, url=url)
        # Pre-fetched Vue CutConfigurator pricing (sheetplastics) — apply before score.
        if configurator_pricing and isinstance(configurator_pricing, dict):
            self._last_configurator_pricing = configurator_pricing
        if platform == "WooCommerce":
            self.logger.info(
                "[WooCommerce] Extracting with WooCommerce strategies"
            )
        self.logger.info(
            "DecisionEngine platform=%s strategies=%s url=%s",
            platform,
            " → ".join(strategies),
            url,
        )

        fetch_blocked = False
        if html is not None:
            # Pre-fetched (parallel Magento pool) — skip network entirely.
            html = html or ""
            fetch_blocked = not bool(html.strip())
        elif platform == "Magento":
            html, fetch_blocked = self._magento_playwright_fetch(url)
        elif platform == "WooCommerce":
            # Hard CF sites (e.g. thepolycarbonatestore): Playwright+proxy works;
            # requests/cloudscraper get blocked. Same path as category crawl.
            html, fetch_blocked = self._woocommerce_fetch(url)
        else:
            html, fetch_blocked = self._soft_fetch(url)
            # Refine platform from fetched HTML when initial probe was Custom.
            if platform in ("Custom", "Unknown") and html:
                detected = self.selector.detect(
                    url,
                    html=html,
                    session=getattr(self.http, "session", None),
                    live_fallback=False,
                )
                platform = detected["platform"]
                price_optional = platform == "Magento"
                strategies = self.selector.strategies_for(platform, url=url)
                if platform == "Magento":
                    self.logger.info(
                        "DecisionEngine reclassified Magento — Playwright PDP fetch %s",
                        url,
                    )
                    html, fetch_blocked = self._magento_playwright_fetch(url)
                elif platform == "WooCommerce":
                    html, fetch_blocked = self._woocommerce_fetch(url)

        context: dict[str, Any] = {
            "http": self.http,
            "platform": platform,
            "site_rules": self.site_rules.get("selectors") or self.site_rules,
            "use_playwright": platform == "Magento",
            "timeout_ms": int(float(self.options.get("timeout") or 45) * 1000),
            "capture_network": True,
            "probe_variants": False,
            "logger": self.logger,
            "browser_headers": platform == "Magento",
            "playwright_headless": False,
            # Gallery URLs from the same proxied Playwright page as `html`.
            "gallery_images": list(getattr(self, "_woo_gallery_images", None) or []),
        }
        if platform == "Magento" and hasattr(self.http, "apply_browser_headers"):
            try:
                self.http.apply_browser_headers()
            except Exception:
                pass
        live = self.shared_playwright()
        if live is not None and live.alive:
            context["playwright_session"] = live
            context["playwright_headless"] = False

        values: dict[str, Any] = {}
        sources: dict[str, str] = {}
        confidences: dict[str, float] = {}
        methods_attempted: list[str] = []
        winning_strategy: str | None = None

        def runner(strategy: str, attempt: int) -> dict[str, Any] | None:
            del attempt
            if strategy == SOURCE_PLAYWRIGHT:
                context["use_playwright"] = True
            methods_attempted.append(strategy)
            partial = pipeline._run_method(strategy, url, html, context)  # noqa: SLF001
            if not partial:
                # Score accumulated values if this strategy added nothing new.
                if values:
                    return dict(values)
                return None
            pipeline._merge_partial(  # noqa: SLF001
                values, sources, confidences, partial, strategy
            )
            return dict(values)

        chain = self.fallback.run_chain(
            url, strategies, runner, price_optional=price_optional
        )
        if chain.get("success"):
            winning_strategy = chain.get("strategy")
            if winning_strategy:
                self.selector.remember_success(url, winning_strategy)

        # Merge Vue CutConfigurator SKU/price into values so confidence can pass.
        self._seed_values_from_configurator(values, sources, confidences)

        # If still weak and Playwright not already tried, force one Playwright pass
        # when HTTP fetch was blocked or confidence failed.
        score = self.scorer.score(values, price_optional=price_optional)
        if (
            not score["passed"]
            and SOURCE_PLAYWRIGHT not in methods_attempted
            and (fetch_blocked or values or platform == "Magento")
        ):
            self.logger.info(
                "DecisionEngine activating Playwright after low confidence / block"
            )
            context["use_playwright"] = True
            methods_attempted.append(SOURCE_PLAYWRIGHT)
            partial = pipeline._run_method(  # noqa: SLF001
                SOURCE_PLAYWRIGHT, url, html, context
            )
            if partial:
                pipeline._merge_partial(  # noqa: SLF001
                    values, sources, confidences, partial, SOURCE_PLAYWRIGHT
                )
                score = self.scorer.score(values, price_optional=price_optional)
                if score["passed"]:
                    winning_strategy = SOURCE_PLAYWRIGHT
                    self.selector.remember_success(url, SOURCE_PLAYWRIGHT)
                    self.fallback.success_log[url] = SOURCE_PLAYWRIGHT

        engine_html = html
        if pipeline._playwright_bundle and pipeline._playwright_bundle.get("html"):  # noqa: SLF001
            engine_html = pipeline._playwright_bundle["html"]  # noqa: SLF001
            # Re-check CAPTCHA on rendered HTML (Cloudflare interstitial).
            live_pw = self.shared_playwright()
            probe_pw = self.captcha.inspect(
                html=engine_html,
                url=url,
                visible_challenge_iframe=(
                    live_pw.has_visible_challenge_iframe()
                    if live_pw is not None and live_pw.alive
                    else False
                ),
                cf_clearance=(
                    live_pw.has_cf_clearance()
                    if live_pw is not None and live_pw.alive
                    else None
                ),
                logger=self.logger,
            )
            if probe_pw.get("blocked"):
                engine_html = self._wait_out_captcha(
                    url, reasons=list(probe_pw.get("reasons") or [])
                )
                if pipeline._playwright_bundle is not None:  # noqa: SLF001
                    pipeline._playwright_bundle["html"] = engine_html  # noqa: SLF001
                live2 = self.shared_playwright()
                if live2 is not None and live2.alive:
                    context["playwright_session"] = live2
                    context["playwright_headless"] = False

        pipeline._ensure_price(values, sources, confidences, engine_html or html)  # noqa: SLF001
        # Magento cut-to-size: price is JS-calculated — empty string is OK.
        if price_optional and not str(values.get("price") or "").strip():
            values["price"] = ""
        pipeline._ensure_description(  # noqa: SLF001
            values,
            sources,
            confidences,
            url,
            html=html,
            engine_html=engine_html or html,
            context=context,
        )

        product = pipeline._build_product(values, url)  # noqa: SLF001
        if price_optional and not str(product.get("price") or "").strip():
            product["price"] = ""
        product["field_sources"] = dict(sources)
        product["extraction_method"] = pipeline._summarize_method(sources)  # noqa: SLF001
        product["platform"] = platform
        product["decision_engine"] = {
            "platform": platform,
            "winning_strategy": winning_strategy,
            "confidence": self.scorer.score(values, price_optional=price_optional),
            "methods_attempted": list(methods_attempted),
            "strategy_chain": list(strategies),
            "price_optional": price_optional,
        }

        # WooCommerce: strip " | Site Name", prefer H1 thickness, regen handle.
        # Gallery images come from the same proxied Playwright page used for HTML.
        if platform == "WooCommerce":
            page_html = engine_html or html or ""
            woo_gallery = list(getattr(self, "_woo_gallery_images", None) or [])
            if woo_gallery:
                product["images"] = woo_gallery
                values["images"] = woo_gallery
            resolve_woocommerce_product_images(
                product,
                page_html,
                gallery_images=woo_gallery or None,
                logger=self.logger,
                force=True,
            )
            apply_woocommerce_title_and_handle(
                product,
                page_html,
                logger=self.logger,
                force=True,
            )
            values["title"] = product.get("title") or values.get("title")
            values["images"] = product.get("images") or values.get("images")

        product, variant_report = pipeline._apply_variant_engine(  # noqa: SLF001
            product, url, engine_html, context
        )
        pipeline._scrub_zero_prices(product)  # noqa: SLF001
        if price_optional and not str(product.get("price") or "").strip():
            product["price"] = ""
        product, image_report = pipeline._apply_image_engine(  # noqa: SLF001
            product, url, engine_html, context
        )
        if platform == "WooCommerce":
            woo_gallery = list(getattr(self, "_woo_gallery_images", None) or [])
            resolve_woocommerce_product_images(
                product,
                engine_html or html or "",
                gallery_images=woo_gallery or None,
                logger=self.logger,
                force=True,
            )
            apply_woocommerce_image_cap(product)
            self._woo_gallery_images = []

        # Sheet Plastics Vue CutConfiguratorWidget → structured pricing fields.
        pricing = getattr(self, "_last_configurator_pricing", None)
        if pricing:
            from sentivo_extractor.extractors.cut_configurator import (
                apply_configurator_pricing,
            )

            product = apply_configurator_pricing(
                product, pricing, logger=self.logger
            )
            self._last_configurator_pricing = None

        missing_for_report = pipeline._missing_required(values)  # noqa: SLF001
        if price_optional:
            missing_for_report = [m for m in missing_for_report if m != "price"]
        report_entry = pipeline._build_report_entry(  # noqa: SLF001
            url=url,
            product=product,
            values=values,
            sources=sources,
            confidences=confidences,
            missing=missing_for_report,
            methods_attempted=methods_attempted,
        )
        pipeline._log_field_sources(sources)  # noqa: SLF001

        # Prefer product after image engine (values may lack images Magento DOM missed).
        still_missing = [
            m for m in pipeline._missing_required(values) if m != "price"  # noqa: SLF001
        ]
        if product.get("images") and "images" in still_missing:
            still_missing = [m for m in still_missing if m != "images"]
            values["images"] = product.get("images")
        if price_optional:
            still_missing = [m for m in still_missing if m != "price"]
        debug = pipeline._debug_artifacts(engine_html)  # noqa: SLF001
        de_meta = product.get("decision_engine") or {}

        if still_missing:
            reason = f"missing_required: {', '.join(still_missing)}"
            failed = ensure_product(product)
            failed["_fail_reason"] = reason
            failed["missing_fields"] = still_missing
            return {
                "success": False,
                "product": None,
                "failed_record": failed,
                "reason": reason,
                "report": report_entry,
                "image_report": image_report,
                "debug_artifacts": debug,
                "decision_engine": de_meta,
            }

        if not product.get("images"):
            reason = "missing_valid_images"
            failed = ensure_product(product)
            failed["_fail_reason"] = reason
            failed["missing_fields"] = ["images"]
            failed["image_engine_report"] = image_report
            return {
                "success": False,
                "product": None,
                "failed_record": failed,
                "reason": reason,
                "report": report_entry,
                "image_report": image_report,
                "debug_artifacts": debug,
                "decision_engine": de_meta,
            }

        # Final field confidence gate (Decision Engine requirement).
        final_score = self.scorer.score(product, price_optional=price_optional)
        de_meta = dict(de_meta)
        de_meta["final_confidence"] = final_score
        product["decision_engine"] = de_meta
        if not final_score["passed"]:
            reason = (
                f"decision_engine_low_confidence:{final_score['percent']}%"
                f" (need {final_score['threshold']}%)"
            )
            failed = ensure_product(deepcopy(product))
            failed["_fail_reason"] = reason
            return {
                "success": False,
                "product": None,
                "failed_record": failed,
                "reason": reason,
                "report": report_entry,
                "variant_report": variant_report,
                "image_report": image_report,
                "debug_artifacts": debug,
                "decision_engine": de_meta,
            }

        if winning_strategy:
            self.logger.info(
                "DecisionEngine finished url=%s strategy=%s confidence=%s%%",
                url,
                winning_strategy,
                final_score["percent"],
            )

        return {
            "success": True,
            "product": product,
            "failed_record": None,
            "reason": "",
            "report": report_entry,
            "variant_report": variant_report,
            "image_report": image_report,
            "debug_artifacts": debug,
            "decision_engine": de_meta,
        }

    def _seed_values_from_configurator(
        self,
        values: dict[str, Any],
        sources: dict[str, str],
        confidences: dict[str, float],
    ) -> None:
        """Push Vue configurator SKU/price into scored values (sheetplastics)."""
        pricing = getattr(self, "_last_configurator_pricing", None)
        if not isinstance(pricing, dict):
            return
        sku = str(pricing.get("sku") or "").strip()
        if sku and not str(values.get("sku") or "").strip():
            values["sku"] = sku
            sources["sku"] = "cut_configurator"
            confidences["sku"] = 0.9
        price_sqm = pricing.get("pricePerSqm")
        if price_sqm is not None and not str(values.get("price") or "").strip():
            try:
                from sentivo_extractor.core.utils import format_cut_to_size_price

                per_cm2 = float(price_sqm) / 10000.0
                priced = format_cut_to_size_price(per_cm2)
            except (TypeError, ValueError):
                priced = ""
            if priced:
                values["price"] = priced
                sources["price"] = "cut_configurator"
                confidences["price"] = 0.9

    def _magento_playwright_fetch(self, url: str) -> tuple[str, bool]:
        """
        Magento PDP fetch — Playwright headed Chrome ONLY (never requests/httpx).
        Uses the shared MagentoBrowserPool so one browser serves every product.
        """
        self.logger.info(
            "[Magento] Playwright product fetch (shared pool): %s", url
        )
        try:
            pool = shared_magento_pool(logger=self.logger)
            page = pool.fetch_page(url, timeout_ms=45000)
            html = str(page.get("html") or "")
            # Stash Vue configurator pricing for post-extract merge.
            self._last_configurator_pricing = page.get("configurator_pricing")
            if not (html or "").strip():
                # Fallback to CAPTCHA/live session if pool got an empty/blocked page.
                session = self._live_session()
                snap = session.render_url(url, timeout_ms=45000)
                html = str(snap.get("html") or "")
                probe = self.captcha.inspect(
                    html=html,
                    title=str(snap.get("title") or ""),
                    url=url,
                    visible_challenge_iframe=bool(
                        snap.get("visible_challenge_iframe")
                    ),
                    cf_clearance=bool(snap.get("cf_clearance")),
                    logger=self.logger,
                )
                if probe.get("blocked"):
                    html = self._wait_out_captcha(
                        url, reasons=list(probe.get("reasons") or [])
                    )
                session.apply_cookies_to_requests(
                    getattr(self.http, "session", None)
                )
            from sentivo_extractor.core.cloudflare import (
                fetch_text_with_cloudscraper,
                playwright_needs_cloudscraper,
            )

            if playwright_needs_cloudscraper(html):
                self.logger.warning(
                    "Cloudflare detected — falling back to cloudscraper"
                )
                try:
                    html = fetch_text_with_cloudscraper(
                        url, timeout=60.0, logger_=self.logger
                    )
                except Exception as cf_exc:
                    self.logger.warning(
                        "[Magento] cloudscraper fallback failed %s: %s", url, cf_exc
                    )
            if not (html or "").strip():
                self.logger.warning(
                    "[Magento] Playwright returned empty HTML for %s", url
                )
                return "", True
            return html, False
        except CaptchaTimeoutError:
            raise
        except Exception as exc:
            self.logger.warning(
                "[Magento] Playwright product fetch failed %s: %s", url, exc
            )
            return "", True

    def _wait_out_captcha(
        self, url: str, *, reasons: list[str] | None = None
    ) -> str:
        """Open a visible browser, poll until solved, return page HTML."""
        session = self._live_session()
        ua = ""
        try:
            ua = str(self.http.session.headers.get("User-Agent") or "")
        except Exception:
            ua = ""
        session.ensure_visible(user_agent=ua)
        snap = self.captcha.wait_until_cleared(
            session,
            url,
            logger=self.logger,
            trigger_reasons=reasons,
        )
        session.apply_cookies_to_requests(getattr(self.http, "session", None))
        return str(snap.get("html") or "")

    def _woocommerce_fetch(self, url: str) -> tuple[str, bool]:
        """
        Fetch WooCommerce PDP HTML for JSON-LD / OpenGraph strategies.

        When proxy is set: Playwright first (handles HTTPS via proxy; CF
        hard blocks break requests/cloudscraper). Gallery image URLs are
        extracted from that same page object (no second browser / navigation).
        Empty Playwright → cloudscraper.
        """
        from sentivo_extractor.core.cloudflare import fetch_text_with_cloudscraper
        from sentivo_extractor.core.http_client import (
            fetch_woocommerce_with_playwright,
            get_proxy,
        )

        self._woo_gallery_images = []
        proxy = get_proxy()
        html = ""
        if proxy:
            self.logger.info(
                "[WooCommerce] Fetching product via Playwright (proxy): %s",
                url,
            )
            try:
                html, gallery = fetch_woocommerce_with_playwright(
                    url, logger_=self.logger
                )
                self._woo_gallery_images = list(gallery or [])
            except Exception as exc:
                self.logger.warning(
                    "[WooCommerce] Playwright fetch failed %s: %s", url, exc
                )
                html = ""
                self._woo_gallery_images = []
            if (html or "").strip():
                probe = self.captcha.inspect(
                    html=html, url=url, logger=self.logger
                )
                if probe.get("blocked"):
                    self._woo_gallery_images = []
                    return (
                        self._wait_out_captcha(
                            url, reasons=list(probe.get("reasons") or [])
                        ),
                        False,
                    )
                return html, False
            self.logger.warning(
                "[WooCommerce] Playwright returned empty for %s — "
                "falling back to cloudscraper",
                url,
            )
            try:
                html = fetch_text_with_cloudscraper(
                    url, timeout=60.0, logger_=self.logger
                )
            except Exception as cf_exc:
                self.logger.warning(
                    "[WooCommerce] cloudscraper fallback failed %s: %s",
                    url,
                    cf_exc,
                )
                html = ""
            if not (html or "").strip():
                return "", True
            return html, False

        # No proxy — still prefer Playwright gallery extract when available.
        try:
            html, gallery = fetch_woocommerce_with_playwright(
                url, logger_=self.logger
            )
            self._woo_gallery_images = list(gallery or [])
            if (html or "").strip():
                return html, False
        except Exception as exc:
            self.logger.info(
                "[WooCommerce] Playwright fetch (no proxy) failed %s: %s",
                url,
                exc,
            )
        self._woo_gallery_images = []
        return self._soft_fetch(url)

    def _soft_fetch(self, url: str) -> tuple[str, bool]:
        """Fetch HTML without raising on HTTP errors; wait out CAPTCHA in-browser."""
        session = getattr(self.http, "session", None)
        live = self.shared_playwright()
        if live is not None and live.alive:
            try:
                snap = live.render_url(url)
                html = str(snap.get("html") or "")
                title = str(snap.get("title") or "")
                probe = self.captcha.inspect(
                    html=html,
                    title=title,
                    url=url,
                    visible_challenge_iframe=bool(
                        snap.get("visible_challenge_iframe")
                    ),
                    cf_clearance=bool(snap.get("cf_clearance")),
                    logger=self.logger,
                )
                if probe.get("blocked"):
                    html = self._wait_out_captcha(
                        url, reasons=list(probe.get("reasons") or [])
                    )
                live.apply_cookies_to_requests(session)
                return html, False
            except CaptchaTimeoutError:
                raise
            except Exception as exc:
                self.logger.warning("Live browser fetch failed %s: %s", url, exc)

        if session is None:
            try:
                html = self.http.get_text(url)
                probe = self.captcha.inspect(
                    html=html, url=url, logger=self.logger
                )
                if probe.get("blocked"):
                    return (
                        self._wait_out_captcha(
                            url, reasons=list(probe.get("reasons") or [])
                        ),
                        False,
                    )
                return html, False
            except CaptchaTimeoutError:
                raise
            except CaptchaBlockedError as exc:
                return self._wait_out_captcha(url, reasons=[str(exc)]), False
            except Exception as exc:
                self.logger.warning("GET failed %s: %s", url, exc)
                return "", True

        robots = getattr(self.http, "robots", None)
        respect = getattr(self.http, "respect_robots", True)
        if robots is not None and not robots.allowed(url, respect):
            raise PermissionError(f"Blocked by robots.txt: {url}")

        limiter = getattr(self.http, "limiter", None)
        if limiter is not None:
            limiter.wait()

        timeout = float(getattr(self.http, "timeout", 25) or 25)
        try:
            resp = session.get(url, timeout=timeout, allow_redirects=True)
        except CaptchaTimeoutError:
            raise
        except Exception as exc:
            self.logger.warning("GET failed %s: %s", url, exc)
            return "", True

        if resp is None:
            self.logger.warning("GET returned None for %s", url)
            return "", True

        html = resp.text or ""
        from sentivo_extractor.core.cloudflare import (
            fetch_text_with_cloudscraper,
            is_cloudflare_challenge,
        )

        status = getattr(resp, "status_code", None)
        if is_cloudflare_challenge(html, status_code=status):
            try:
                html = fetch_text_with_cloudscraper(
                    url,
                    timeout=max(timeout, 60.0),
                    logger_=self.logger,
                )
                resp = None  # cloudscraper path — no response object retained
            except Exception as cf_exc:
                self.logger.warning(
                    "cloudscraper fallback failed %s: %s", url, cf_exc
                )
        headers = {
            k: v for k, v in (resp.headers.items() if resp is not None else [])
        }
        cookie_jar = getattr(session, "cookies", None)
        probe = self.captcha.inspect(
            status_code=(
                getattr(resp, "status_code", None) if resp is not None else 200
            ),
            html=html,
            url=url,
            headers=headers,
            cookies=cookie_jar,
            logger=self.logger,
        )
        if probe["blocked"]:
            self.logger.warning(
                "%s %s", CAPTCHA_WAIT_MARKER, probe["message"] or probe["reasons"]
            )
            return (
                self._wait_out_captcha(
                    url, reasons=list(probe.get("reasons") or [])
                ),
                False,
            )

        if resp is not None and getattr(resp, "status_code", 0) >= 400:
            self.logger.warning(
                "HTTP %s for %s — treating as fetch_blocked",
                resp.status_code,
                url,
            )
            return html, True
        if not (html or "").strip():
            return "", True
        return html, False
