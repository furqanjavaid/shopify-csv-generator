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
from sentivo_extractor.decision_engine.captcha_detector import (
    CAPTCHA_LOG_MARKER,
    CaptchaBlockedError,
    CaptchaDetector,
)
from sentivo_extractor.decision_engine.confidence_scorer import ConfidenceScorer
from sentivo_extractor.decision_engine.fallback_manager import FallbackManager
from sentivo_extractor.decision_engine.retry_handler import RetryHandler
from sentivo_extractor.decision_engine.strategy_selector import StrategySelector

# Shared across crawler instances so domain winners persist for a process run.
_SHARED_SELECTOR = StrategySelector()


class DecisionCoordinator:
    """
    Intelligent coordinator over the existing HTTP / Playwright extractors.

    Flow per URL:
      1. Soft-fetch HTML + CAPTCHA/block detection (pause on hit)
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

    def extract(self, url: str) -> dict[str, Any]:
        """Extract one URL with retries. Raises CaptchaBlockedError on block."""

        def _once() -> dict[str, Any]:
            return self._extract_once(url)

        def _ok(outcome: dict[str, Any]) -> bool:
            return bool(outcome.get("success"))

        result, reason = self.retry.run(url, _once, is_success=_ok)
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

    def _extract_once(self, url: str) -> dict[str, Any]:
        pipeline = PDPExtractionPipeline(
            http=self.http,
            options=self.options,
            registry=self.registry,
            site_rules=self.site_rules,
            logger=self.logger,
        )

        html, fetch_blocked = self._soft_fetch(url)
        detected = self.selector.detect(
            url,
            html=html,
            session=getattr(self.http, "session", None),
        )
        platform = detected["platform"]
        strategies = self.selector.strategies_for(platform, url=url)
        self.logger.info(
            "DecisionEngine platform=%s strategies=%s url=%s",
            platform,
            " → ".join(strategies),
            url,
        )

        context: dict[str, Any] = {
            "http": self.http,
            "platform": platform,
            "site_rules": self.site_rules.get("selectors") or self.site_rules,
            "use_playwright": False,
            "timeout_ms": int(float(self.options.get("timeout") or 25) * 1000),
            "capture_network": True,
            "probe_variants": False,
            "logger": self.logger,
        }

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

        chain = self.fallback.run_chain(url, strategies, runner)
        if chain.get("success"):
            winning_strategy = chain.get("strategy")
            if winning_strategy:
                self.selector.remember_success(url, winning_strategy)

        # If still weak and Playwright not already tried, force one Playwright pass
        # when HTTP fetch was blocked or confidence failed.
        score = self.scorer.score(values)
        if (
            not score["passed"]
            and SOURCE_PLAYWRIGHT not in methods_attempted
            and (fetch_blocked or values)
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
                score = self.scorer.score(values)
                if score["passed"]:
                    winning_strategy = SOURCE_PLAYWRIGHT
                    self.selector.remember_success(url, SOURCE_PLAYWRIGHT)
                    self.fallback.success_log[url] = SOURCE_PLAYWRIGHT

        engine_html = html
        if pipeline._playwright_bundle and pipeline._playwright_bundle.get("html"):  # noqa: SLF001
            engine_html = pipeline._playwright_bundle["html"]  # noqa: SLF001
            # Re-check CAPTCHA on rendered HTML (Cloudflare interstitial).
            self.captcha.raise_if_blocked(html=engine_html, url=url)

        pipeline._ensure_price(values, sources, confidences, engine_html or html)  # noqa: SLF001
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
        product["field_sources"] = dict(sources)
        product["extraction_method"] = pipeline._summarize_method(sources)  # noqa: SLF001
        product["platform"] = platform
        product["decision_engine"] = {
            "platform": platform,
            "winning_strategy": winning_strategy,
            "confidence": self.scorer.score(values),
            "methods_attempted": list(methods_attempted),
            "strategy_chain": list(strategies),
        }

        product, variant_report = pipeline._apply_variant_engine(  # noqa: SLF001
            product, url, engine_html, context
        )
        pipeline._scrub_zero_prices(product)  # noqa: SLF001
        product, image_report = pipeline._apply_image_engine(  # noqa: SLF001
            product, url, engine_html, context
        )

        report_entry = pipeline._build_report_entry(  # noqa: SLF001
            url=url,
            product=product,
            values=values,
            sources=sources,
            confidences=confidences,
            missing=pipeline._missing_required(values),  # noqa: SLF001
            methods_attempted=methods_attempted,
        )
        pipeline._log_field_sources(sources)  # noqa: SLF001

        still_missing = [
            m for m in pipeline._missing_required(values) if m != "price"  # noqa: SLF001
        ]
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
        final_score = self.scorer.score(product)
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

    def _soft_fetch(self, url: str) -> tuple[str, bool]:
        """Fetch HTML without raising on HTTP errors; detect CAPTCHA/blocks."""
        session = getattr(self.http, "session", None)
        if session is None:
            try:
                html = self.http.get_text(url)
                self.captcha.raise_if_blocked(html=html, url=url)
                return html, False
            except CaptchaBlockedError:
                raise
            except Exception as exc:
                self.logger.warning("GET failed %s: %s", url, exc)
                return "", True

        # Respect robots / rate limit via HttpClient helpers when present.
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
        except CaptchaBlockedError:
            raise
        except Exception as exc:
            self.logger.warning("GET failed %s: %s", url, exc)
            return "", True

        html = resp.text or ""
        headers = {k: v for k, v in resp.headers.items()}
        try:
            self.captcha.raise_if_blocked(
                status_code=resp.status_code,
                html=html,
                url=url,
                headers=headers,
            )
        except CaptchaBlockedError as exc:
            self.logger.error("%s %s", CAPTCHA_LOG_MARKER, exc)
            print(f"{CAPTCHA_LOG_MARKER} {exc}", flush=True)
            raise

        if resp.status_code >= 400:
            self.logger.warning(
                "HTTP %s for %s — treating as fetch_blocked",
                resp.status_code,
                url,
            )
            return html, True
        return html, False
