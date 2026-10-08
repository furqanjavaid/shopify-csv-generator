"""Orchestrate discovery → extraction → normalize → validate → export."""

from __future__ import annotations

import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from sentivo_extractor.core.bug_report import BugReportCollector, print_failure_groups
from sentivo_extractor.core.checkpoint import DomainCheckpointManager
from sentivo_extractor.core.confidence import confidence_band
from sentivo_extractor.core.output_layout import (
    domain_artifact_path,
    domain_folder_name,
    ensure_domain_dir,
)
from sentivo_extractor.core.coverage import (
    build_coverage_report,
    coverage_enforcement_failed,
    domain_of,
)
from sentivo_extractor.core.http_client import HttpClient
from sentivo_extractor.core.image_checker import check_product_images
from sentivo_extractor.core.image_pipeline import process_product_images
from sentivo_extractor.core.input_csv import (
    apply_seed_metadata,
    read_seed_csv,
    seed_metadata,
)
from sentivo_extractor.core.normalizer import normalize_product
from sentivo_extractor.coordinator import DecisionCoordinator
from sentivo_extractor.decision_engine.captcha_detector import (
    CaptchaBlockedError,
    CaptchaTimeoutError,
    SOLVE_TIMEOUT_SEC,
)
from sentivo_extractor.decision_engine.magento_browser import shared_magento_pool
from sentivo_extractor.core.pdp_pipeline import (
    write_pdp_extraction_report,
    write_retry_queue,
)
from sentivo_extractor.core.image_engine import write_image_report
from sentivo_extractor.core.variant_engine import write_variant_report
from sentivo_extractor.core.platform_detector import detect_platform
from sentivo_extractor.core.product_discovery import (
    canonicalize_product_url,
    clear_sitemap_failure_cache,
    discover_domain_products,
    unique_preserve,
)
from sentivo_extractor.core.production_validation import (
    STATUS_FAILED,
    STATUS_RECOVERED,
    STATUS_SUCCESS,
    assess_production_fields,
    build_validation_summary,
    validation_row,
    write_final_validation_report,
)
from sentivo_extractor.core.production_workbook import write_production_summary_workbook
from sentivo_extractor.core.qa_report import sample_products_for_qa, write_qa_sample_workbook
from sentivo_extractor.core.shopify_csv_exporter import (
    append_shopify_product_csv,
    export_failed_csv,
    export_images_manifest,
    export_magento_failed_csv,
    export_shopify_csv,
    export_validation_report,
)
from sentivo_extractor.core.shopify_preimport import (
    apply_duplicate_sku_policy,
    validate_shopify_csv,
    write_preimport_validation_report,
)
from sentivo_extractor.core.site_rule_suggester import domain_from_url
from sentivo_extractor.core.utils import DEFAULT_USER_AGENT, close_logger, setup_logger, write_json
from sentivo_extractor.core.validator import validate_products
from sentivo_extractor.extractors import build_default_registry
from sentivo_extractor.post_processors.variant_merger import merge_products_by_base_title


class UniversalCrawler:
    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options
        # User-selected base path; per-domain artifacts go under base/<domain>/.
        self.base_output_dir = Path(options.get("output") or "output")
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        # Back-compat alias used by summaries / older callers.
        self.output_dir = self.base_output_dir
        self.logger = setup_logger(log_dir=self.base_output_dir / "logs")
        self.http = HttpClient(
            user_agent=options.get("user_agent") or DEFAULT_USER_AGENT,
            timeout=float(options.get("timeout") or 25),
            delay_sec=float(options.get("delay") or 1.0),
            retries=int(options.get("retries") or 3),
            respect_robots=bool(options.get("respect_robots", True)),
            logger=self.logger,
        )
        self.registry = build_default_registry()
        self.site_rules = self._load_site_rules(options.get("site_rules_dir"))
        self.overwrite = bool(options.get("overwrite"))
        self.checkpoint = DomainCheckpointManager(
            self.base_output_dir,
            load_existing=not self.overwrite,
            overwrite=self.overwrite,
        )
        # Cap discovered PDP URLs per domain. 0 / negative = unlimited.
        raw_max = options.get("max_products_per_domain", 500)
        try:
            self.max_products_per_domain = int(raw_max)
        except (TypeError, ValueError):
            self.max_products_per_domain = 500
        self.pilot = bool(options.get("pilot"))
        self.pilot_size = int(options.get("pilot_size_per_domain") or 20)
        if self.pilot:
            if self.max_products_per_domain <= 0:
                self.max_products_per_domain = self.pilot_size
            else:
                self.max_products_per_domain = min(
                    self.max_products_per_domain, self.pilot_size
                )
        self.min_coverage_percent = float(options.get("min_coverage_percent") or 90)
        self.enforce_expected_count = bool(options.get("enforce_expected_count"))
        self.duplicate_sku_policy = str(options.get("duplicate_sku_policy") or "warn")
        self.check_image_urls = bool(options.get("check_image_urls"))
        self.qa_sample_size = int(options.get("qa_sample_size_per_domain") or 20)
        self.qa_random_seed = int(options.get("qa_random_seed") or 42)
        self.production_validation = bool(options.get("production_validation"))
        self.strict_mode = bool(options.get("strict"))
        if self.strict_mode:
            self.production_validation = True
        vendor_raw = str(options.get("vendor") or "").strip()
        self.vendor_override = vendor_raw or None
        self._url_meta: dict[str, dict[str, Any]] = {}
        self._failed_reasons: list[tuple[str, str]] = []
        self._discovered_by_domain: dict[str, int] = defaultdict(int)
        self._pdp_reports: list[dict[str, Any]] = []
        self._retry_queue: list[dict[str, Any]] = []
        self._extraction_failed: list[dict[str, Any]] = []
        self._variant_reports: list[dict[str, Any]] = []
        self._image_reports: list[dict[str, Any]] = []
        self._validation_rows: list[dict[str, Any]] = []
        run_mode = "pilot" if self.pilot else (
            "validation" if self.production_validation else "full"
        )
        self.bug_report = BugReportCollector(self.base_output_dir, run_mode=run_mode)
        self._crawl_stats: dict[str, int] = {
            "discovered": 0,
            "processed": 0,
            "skipped": 0,
            "failed": 0,
        }
        self._domain_dirs: dict[str, Path] = {}
        self.captcha_paused = False
        self.captcha_pause_info: dict[str, Any] = {}
        self._captcha_blocked_hosts: set[str] = set()
        self._magento_domains: set[str] = set()
        self._magento_failed_rows: list[dict[str, Any]] = []
        self._checkpoint_handles: set[str] = set()
        self._extract_pool = ThreadPoolExecutor(max_workers=1)

    def _domain_dir(self, domain_key: str) -> Path:
        if domain_key not in self._domain_dirs:
            self._domain_dirs[domain_key] = ensure_domain_dir(
                self.base_output_dir, domain_key
            )
        return self._domain_dirs[domain_key]

    def _artifact(self, domain_key: str, filename: str) -> Path:
        return domain_artifact_path(self.base_output_dir, domain_key, filename)

    @staticmethod
    def _product_domain_key(product: dict[str, Any]) -> str:
        return domain_folder_name(str(product.get("source_url") or ""))

    def _load_site_rules(self, rules_dir: str | None) -> dict[str, Any]:
        root = (
            Path(rules_dir)
            if rules_dir
            else Path(__file__).resolve().parents[1] / "configs" / "site_rules"
        )
        rules: dict[str, Any] = {}
        if not root.exists():
            return rules
        for path in root.glob("*.y*ml"):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if data.get("suggestions_only"):
                    continue  # never auto-trust suggestions
                hosts = data.get("hosts") or data.get("domains") or []
                if isinstance(hosts, str):
                    hosts = [hosts]
                for host in hosts:
                    rules[str(host).lower().removeprefix("www.")] = data
            except Exception as exc:
                self.logger.warning("Failed loading site rule %s: %s", path, exc)
        return rules

    def _rules_for_url(self, url: str) -> dict[str, Any]:
        host = domain_from_url(url)
        return self.site_rules.get(host) or {}

    def run(self, input_csv: Path) -> dict[str, Any]:
        seeds = read_seed_csv(input_csv)

        # Ensure domain folders exist early; pilot overwrite guard per domain.
        seed_domains = []
        for seed in seeds:
            key = domain_folder_name(str(seed.get("url") or ""))
            if key not in seed_domains:
                seed_domains.append(key)
            self._domain_dir(key)
        if self.pilot and not self.overwrite:
            for key in seed_domains:
                csv_out = self._artifact(key, "shopify_import.csv")
                if csv_out.exists():
                    close_logger(self.logger)
                    raise RuntimeError(
                        f"Pilot output exists at {csv_out}. "
                        "Pass --overwrite true to replace."
                    )

        try:
            return self._run_pipeline(input_csv, seeds)
        finally:
            close_logger(self.logger)

    def _run_pipeline(
        self, input_csv: Path, seeds: list[dict[str, str]]
    ) -> dict[str, Any]:
        clear_sitemap_failure_cache()
        self.logger.info("Product Discovery Version: Unified")
        self.logger.info("Output base: %s (domain subfolders + prefixed files)", self.base_output_dir)

        # Overwrite: wipe checkpoint before any crawl work; never resume prior state.
        if self.overwrite:
            self.checkpoint.reset()
            checkpoint_enabled = False
        else:
            checkpoint_enabled = True

        completed_at_start = self.checkpoint.completed_count()
        print(f"Checkpoint: {'enabled' if checkpoint_enabled else 'disabled'}")
        print(f"Checkpoint base: {self.base_output_dir}")
        print(f"Completed products in checkpoint: {completed_at_start}")
        self.logger.info(
            "Checkpoint: %s | base=%s | completed=%s",
            "enabled" if checkpoint_enabled else "disabled",
            self.base_output_dir,
            completed_at_start,
        )

        product_urls = self._discover_all(seeds)
        # Pilot: hard-cap per domain after discovery
        if self.pilot:
            product_urls = self._limit_urls_per_domain(product_urls, self.pilot_size)
        self.logger.info("Discovered %s product URL(s)", len(product_urls))
        print(f"Discovered {len(product_urls)} product URL(s)")

        # Fresh product list when overwrite; otherwise resume with checkpoint products.
        if self.overwrite:
            products: list[dict[str, Any]] = []
        else:
            products = list(self.checkpoint.products())
        completed = set(self.checkpoint.completed) if checkpoint_enabled else set()
        failed_urls = set(self.checkpoint.failed) if checkpoint_enabled else set()
        seen_in_run: set[str] = set()
        processed = 0
        skipped = 0
        failed_count = 0
        resuming = checkpoint_enabled and completed_at_start > 0

        manifest_rows: list[dict[str, Any]] = []

        pending_magento: list[str] = []
        pending_other: list[str] = []
        for url in product_urls:
            if url in seen_in_run:
                skipped += 1
                self.logger.info("Skip [duplicate]: %s", url)
                continue
            seen_in_run.add(url)

            if url in completed:
                skipped += 1
                reason = "checkpoint_resume" if resuming else "already_completed"
                self.logger.info("Skip [%s]: %s", reason, url)
                continue
            if url in failed_urls:
                skipped += 1
                self.logger.info("Skip [already_failed]: %s", url)
                continue
            host = domain_from_url(url)
            if host in self._captcha_blocked_hosts:
                skipped += 1
                self.logger.info("Skip [captcha_site_blocked]: %s", url)
                continue

            # Sequential Magento path (human-like delays) skips production-validation loop.
            if self._is_magento_url(url) and not self.production_validation:
                DecisionCoordinator.remember_platform(url, "Magento")
                pending_magento.append(url)
            else:
                pending_other.append(url)

        magento_total = len(pending_magento)
        if magento_total:
            self.logger.info(
                "[Magento] Sequential extraction: %s product(s), single tab + delays",
                magento_total,
            )
            # Warm the shared single-tab session once.
            shared_magento_pool(logger=self.logger).start()
            magento_done = 0
            for idx, url in enumerate(pending_magento):
                try:
                    url, product, reason = self._extract_magento_one(url)
                    if not product:
                        self.checkpoint.mark_failed(
                            url, reason or "extraction_failed"
                        )
                        self._record_magento_failure(url, reason or "extraction_failed")
                        failed_count += 1
                        if reason and "captcha_timeout" in reason:
                            self._captcha_blocked_hosts.add(domain_from_url(url))
                    else:
                        ok = self._finalize_extracted_product(
                            url, product, manifest_rows
                        )
                        if ok:
                            products.append(product)
                            processed += 1
                            magento_done += 1
                            self._maybe_checkpoint_save(product)
                            if magento_done % 10 == 0 or magento_done == magento_total:
                                self.logger.info(
                                    "[Magento] Extracted %s/%s products...",
                                    magento_done,
                                    magento_total,
                                )
                        else:
                            self._record_magento_failure(
                                url, "extraction_failed", product=product
                            )
                            failed_count += 1
                except CaptchaTimeoutError as exc:
                    reason = (
                        getattr(exc, "reason", None)
                        or "captcha_timeout_site_blocked"
                    )
                    self._captcha_blocked_hosts.add(domain_from_url(url))
                    self.logger.error(
                        "CAPTCHA timeout — site blocked, next URL: %s", url
                    )
                    self.checkpoint.mark_failed(url, reason)
                    self._failed_reasons.append((url, reason))
                    self._record_magento_failure(url, reason)
                    failed_count += 1
                except Exception as exc:  # noqa: BLE001
                    self.logger.error("FAIL %s: %s", url, exc)
                    self.checkpoint.mark_failed(url, str(exc))
                    self._failed_reasons.append((url, str(exc)))
                    self._record_magento_failure(url, str(exc))
                    failed_count += 1

                # Human-like pacing between products (not after the last one).
                if idx < magento_total - 1:
                    delay = random.uniform(8, 15)
                    self.logger.info(
                        "[Magento] Waiting %.1fs before next product...", delay
                    )
                    time.sleep(delay)
                    # Extra cool-down every 20 products to avoid Sucuri IDS.
                    if (idx + 1) % 20 == 0:
                        pause = random.uniform(30, 40)
                        self.logger.info(
                            "[Magento] Cool-down after %s products — pausing %.1fs",
                            idx + 1,
                            pause,
                        )
                        time.sleep(pause)

            self._write_magento_failed_report()

        for url in pending_other:
            try:
                if self.production_validation:
                    product, val_row = self._extract_and_validate_product(url)
                    self._validation_rows.append(val_row)
                    if not product:
                        reason = str(
                            val_row.get("Failure Reason") or "validation_failed"
                        )
                        self.checkpoint.mark_failed(url, reason)
                        failed_count += 1
                        if "captcha_timeout" in reason:
                            self._captcha_blocked_hosts.add(domain_from_url(url))
                        self.bug_report.record(
                            url=url,
                            reason=reason,
                            stage="Production Validation",
                            retry_count=int(val_row.get("Retry Count") or 0),
                        )
                        continue
                else:
                    product, _reason = self.extract_one(url)
                    if not product:
                        reason = "extraction_failed"
                        for u, r in reversed(self._failed_reasons):
                            if u == url:
                                reason = r
                                break
                        self.checkpoint.mark_failed(url, reason)
                        failed_count += 1
                        if "captcha_timeout" in reason:
                            self._captcha_blocked_hosts.add(domain_from_url(url))
                        continue
                    product = normalize_product(product, base_url=url)
                    meta = self._url_meta.get(
                        canonicalize_product_url(url)
                    ) or self._url_meta.get(url) or {}
                    product = apply_seed_metadata(product, meta)
                    from sentivo_extractor.core.confidence import apply_confidence

                    product = apply_confidence(product)
                if self._finalize_extracted_product(url, product, manifest_rows):
                    products.append(product)
                    processed += 1
                    self.logger.info(
                        "OK [%s/%.2f] %s",
                        product.get("confidence_band"),
                        float(product.get("confidence_score") or 0),
                        product.get("title"),
                    )
                else:
                    failed_count += 1
            except CaptchaTimeoutError as exc:
                reason = getattr(exc, "reason", None) or "captcha_timeout_site_blocked"
                self._captcha_blocked_hosts.add(domain_from_url(url))
                self.logger.error("CAPTCHA timeout — site blocked, next URL: %s", url)
                self.checkpoint.mark_failed(url, reason)
                self._failed_reasons.append((url, reason))
                failed_count += 1
                continue
            except CaptchaBlockedError as exc:
                reason = getattr(exc, "reason", None) or str(exc)
                self.logger.error(
                    "CAPTCHA leftover — failing URL and continuing: %s", url
                )
                self.checkpoint.mark_failed(url, reason)
                self._failed_reasons.append((url, reason))
                failed_count += 1
                continue
            except Exception as exc:  # noqa: BLE001
                self.logger.error("FAIL %s: %s", url, exc)
                self.checkpoint.mark_failed(url, str(exc))
                self._failed_reasons.append((url, str(exc)))
                failed_count += 1
                html_snip = ""
                try:
                    html_snip = self.http.get_text(url)
                except Exception:
                    html_snip = ""
                self.bug_report.record(
                    url=url,
                    reason=str(exc),
                    stage="PDP Extraction",
                    html=html_snip,
                )

        try:
            self._extract_pool.shutdown(wait=False)
        except Exception:
            pass
        DecisionCoordinator.close_shared_session()

        self._crawl_stats = {
            "discovered": len(product_urls),
            "processed": processed,
            "skipped": skipped,
            "failed": failed_count,
        }
        print(f"Products discovered: {len(product_urls)}")
        print(f"Products processed: {processed}")
        print(f"Products skipped: {skipped}")
        print(f"Products failed: {failed_count}")
        self.logger.info(
            "Crawl end: discovered=%s processed=%s skipped=%s failed=%s",
            len(product_urls),
            processed,
            skipped,
            failed_count,
        )

        products = self._dedupe_products(products)
        if self.vendor_override:
            for product in products:
                product["vendor"] = self.vendor_override

        # Merge same-base-title simple PDPs into one Shopify product with Size variants
        before_merge = len(products)
        products = merge_products_by_base_title(
            products, log=self.logger, http=self.http
        )
        if len(products) != before_merge:
            self.logger.info(
                "Post-process variant merger: %s → %s product(s)",
                before_merge,
                len(products),
            )

        # Duplicate SKU policy
        products, sku_failed, sku_warnings = apply_duplicate_sku_policy(
            products, self.duplicate_sku_policy
        )
        for w in sku_warnings:
            self.logger.warning(w)

        report = validate_products(products)
        failed = list(report["failed"]) + sku_failed + self._extraction_failed

        # Image accessibility (global, then split per domain on export)
        image_issues: list[dict[str, Any]] = []
        if self.check_image_urls:
            image_issues = check_product_images(
                products,
                session=self.http.session,
                timeout=float(self.options.get("timeout") or 10),
            )

        coverage_rows = build_coverage_report(
            seeds=seeds,
            discovered_urls=product_urls,
            products=products,
            min_coverage_percent=self.min_coverage_percent,
        )

        # Bug report rows (written per-domain below)
        for p in report.get("failed") or []:
            src = str(p.get("source_url") or "")
            if not src:
                continue
            self.bug_report.record(
                url=src,
                reason=str(p.get("_fail_reason") or "validation_failed"),
                stage="Validation",
            )
        for p in sku_failed:
            src = str(p.get("source_url") or "")
            if src:
                self.bug_report.record(
                    url=src,
                    reason=str(p.get("_fail_reason") or "duplicate_sku"),
                    stage="Validation",
                )
        self.bug_report.extend_from_retry_queue(self._retry_queue)

        summary = self._export_domain_outputs(
            seeds=seeds,
            product_urls=product_urls,
            products=products,
            report=report,
            failed=failed,
            sku_failed=sku_failed,
            sku_warnings=sku_warnings,
            manifest_rows=manifest_rows,
            image_issues=image_issues,
            coverage_rows=coverage_rows,
        )

        print_failure_groups(self.bug_report.records)
        self._print_summary(summary)

        if coverage_enforcement_failed(
            coverage_rows, enforce=self.enforce_expected_count
        ):
            summary["coverage_enforcement"] = "failed"
            self.logger.warning(
                "Coverage below --min-coverage-percent for one or more domains"
            )
        return summary

    def _limit_urls_per_domain(self, urls: list[str], limit: int) -> list[str]:
        counts: dict[str, int] = defaultdict(int)
        out: list[str] = []
        for url in urls:
            dom = domain_from_url(url)
            if counts[dom] >= limit:
                continue
            counts[dom] += 1
            out.append(url)
        return out

    def _remaining_for_domain(self, domain: str, already: int) -> int | None:
        """Products still allowed for this domain, or None when uncapped."""
        del domain
        if self.max_products_per_domain <= 0:
            return None
        return self.max_products_per_domain - int(already or 0)

    def _domain_summary_rows(
        self,
        products: list[dict[str, Any]],
        discovered: list[str],
        coverage_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_dom_products: dict[str, list] = defaultdict(list)
        for p in products:
            by_dom_products[domain_from_url(str(p.get("source_url") or ""))].append(p)
        cov_map = {r["domain"]: r for r in coverage_rows}
        rows = []
        domains = sorted(
            set(by_dom_products)
            | set(cov_map)
            | {domain_from_url(u) for u in discovered}
        )
        for dom in domains:
            if not dom:
                continue
            items = by_dom_products.get(dom) or []
            bands = Counter(
                confidence_band(float(p.get("confidence_score") or 0)) for p in items
            )
            cov = cov_map.get(dom) or {}
            rows.append(
                {
                    "domain": dom,
                    "discovered": cov.get(
                        "discovered_count",
                        sum(1 for u in discovered if domain_from_url(u) == dom),
                    ),
                    "extracted": len(items),
                    "green": bands.get("green", 0),
                    "yellow": bands.get("yellow", 0),
                    "red": bands.get("red", 0),
                    "expected_count": cov.get("expected_count", ""),
                    "coverage_percent": cov.get("coverage_percent", ""),
                    "coverage_status": cov.get("coverage_status", ""),
                    "risk_level": cov.get("risk_level", ""),
                }
            )
        return rows

    def _is_magento_url(self, url: str) -> bool:
        host = domain_from_url(url)
        if host in self._magento_domains:
            return True
        pinned = DecisionCoordinator.shared_selector().platform_for(url)
        return pinned == "Magento"

    @staticmethod
    def _product_has_sku(product: dict[str, Any]) -> bool:
        if str(product.get("sku") or product.get("variant_sku") or "").strip():
            return True
        for v in product.get("variants") or []:
            if isinstance(v, dict) and str(v.get("sku") or "").strip():
                return True
        return False

    @staticmethod
    def _product_image_count(product: dict[str, Any]) -> int:
        n = 0
        for img in product.get("images") or []:
            if isinstance(img, dict) and str(img.get("src") or "").strip():
                n += 1
            elif isinstance(img, str) and img.strip():
                n += 1
        return n

    @staticmethod
    def _normalize_magento_fail_reason(
        reason: str, *, product: dict[str, Any] | None = None
    ) -> str:
        text = (reason or "").strip()
        low = text.lower()
        if any(tok in low for tok in ("sucuri", "website firewall", "access denied")):
            return "Sucuri block"
        if "502" in low or "bad gateway" in low:
            return "502 error"
        if "low_confidence" in low or "low confidence" in low:
            conf = None
            if product is not None:
                try:
                    conf = float(product.get("confidence_score") or 0)
                except (TypeError, ValueError):
                    conf = None
            if conf is None:
                m = re.search(r"(\d+(?:\.\d+)?)", text)
                conf = float(m.group(1)) if m else None
                if conf is not None and conf > 1:
                    conf = conf / 100.0
            if conf is not None:
                return f"Low confidence: {conf:.2f}"
            return "Low confidence"
        if "image" in low:
            return "No images"
        if "sku" in low:
            return "No SKU"
        if product is not None:
            try:
                conf = float(product.get("confidence_score") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            if conf < 0.75:
                return f"Low confidence: {conf:.2f}"
            if UniversalCrawler._product_image_count(product) < 1:
                return "No images"
            if not UniversalCrawler._product_has_sku(product):
                return "No SKU"
        return text[:120] or "extraction_failed"

    def _record_magento_failure(
        self,
        url: str,
        reason: str,
        *,
        product: dict[str, Any] | None = None,
    ) -> None:
        conf = ""
        if product is not None:
            try:
                conf = f"{float(product.get('confidence_score') or 0):.2f}"
            except (TypeError, ValueError):
                conf = ""
        self._magento_failed_rows.append(
            {
                "URL": url,
                "Fail Reason": self._normalize_magento_fail_reason(
                    reason, product=product
                ),
                "Confidence Score": conf,
            }
        )

    def _maybe_checkpoint_save(self, product: dict[str, Any]) -> bool:
        """
        Real-time Shopify CSV append when Magento product meets quality gates.
        """
        try:
            conf = float(product.get("confidence_score") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        title = str(product.get("title") or "").strip()
        title_low = title.lower()
        if conf < 0.75:
            return False
        if not title:
            return False
        if any(
            bad in title_low
            for bad in ("bad gateway", "access denied", "sucuri")
        ):
            return False
        if self._product_image_count(product) < 1:
            return False
        if not self._product_has_sku(product):
            return False

        handle = str(product.get("handle") or "").strip()
        if handle and handle in self._checkpoint_handles:
            return False

        url = str(product.get("source_url") or "")
        domain_key = domain_folder_name(url)
        path = self._artifact(domain_key, "shopify_import.csv")
        try:
            append_shopify_product_csv(product, path)
            if handle:
                self._checkpoint_handles.add(handle)
            self.logger.info(
                "[Checkpoint] Saved: %s (conf=%.2f)", title, conf
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[Checkpoint] Save failed for %s: %s", title, exc)
            return False

    def _write_magento_failed_report(self) -> None:
        if not self._magento_failed_rows:
            return
        # Group by domain folder so multi-domain Magento runs stay separated.
        by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self._magento_failed_rows:
            key = domain_folder_name(str(row.get("URL") or ""))
            by_domain[key].append(row)
        for key, rows in by_domain.items():
            path = self._artifact(key, f"{key}_failed.csv")
            export_magento_failed_csv(rows, path)
            self.logger.info(
                "[Failed] %s products saved to %s",
                len(rows),
                path.name,
            )

    def _finalize_extracted_product(
        self,
        url: str,
        product: dict[str, Any],
        manifest_rows: list[dict[str, Any]],
    ) -> bool:
        """Persist images/checkpoint for a successfully extracted product."""
        try:
            domain_key = domain_folder_name(url)
            domain_dir = self._domain_dir(domain_key)
            rows = process_product_images(
                product,
                download=bool(self.options.get("download_images")),
                convert=True,
                images_dir=domain_dir / "images"
                if self.options.get("download_images")
                else None,
                session=self.http.session,
            )
            for row in rows:
                row["domain"] = domain_key
            manifest_rows.extend(rows)
            raw_dir = domain_dir / "raw_json_backup"
            raw_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                raw_dir / f"{product.get('handle') or 'product'}.json", product
            )
            self.checkpoint.mark_done(url, product)
            self.logger.info(
                "OK [%s/%.2f] %s",
                product.get("confidence_band"),
                float(product.get("confidence_score") or 0),
                product.get("title"),
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Finalize FAIL %s: %s", url, exc)
            self.checkpoint.mark_failed(url, str(exc))
            self._failed_reasons.append((url, str(exc)))
            return False

    def _extract_magento_one(
        self, url: str
    ) -> tuple[str, dict[str, Any] | None, str]:
        """Fetch one Magento PDP on the shared single tab, then extract."""
        pool = shared_magento_pool(logger=self.logger)
        html = pool.fetch_one(url, timeout_ms=45000)
        product, reason = self.extract_one(
            url,
            queue_on_failure=True,
            html=html,
            platform="Magento",
        )
        if product:
            product = normalize_product(product, base_url=url)
            meta = self._url_meta.get(
                canonicalize_product_url(url)
            ) or self._url_meta.get(url) or {}
            product = apply_seed_metadata(product, meta)
            from sentivo_extractor.core.confidence import apply_confidence

            product = apply_confidence(product)
        return url, product, reason

    def extract_one(
        self,
        url: str,
        *,
        queue_on_failure: bool = True,
        html: str | None = None,
        platform: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        timeout_sec = float(self.options.get("product_timeout_sec") or 60)
        timeout_sec += float(SOLVE_TIMEOUT_SEC) + 20

        # Pre-fetched Magento HTML: parse on calling thread (pool already parallel).
        if html is not None and platform == "Magento":
            return self._extract_one_impl(
                url,
                queue_on_failure=queue_on_failure,
                html=html,
                platform=platform,
            )

        future = self._extract_pool.submit(
            self._extract_one_impl,
            url,
            queue_on_failure=queue_on_failure,
            html=html,
            platform=platform,
        )
        try:
            return future.result(timeout=timeout_sec)
        except CaptchaTimeoutError:
            raise
        except CaptchaBlockedError:
            raise
        except FuturesTimeout:
            reason = f"product_timeout:{int(timeout_sec)}s"
            self.logger.error(
                "Product timeout (%ss) — marking failed, moving on: %s",
                int(timeout_sec),
                url,
            )
            if queue_on_failure:
                self._failed_reasons.append((url, reason))
                self._retry_queue.append(
                    {
                        "url": url,
                        "reason": reason,
                        "missing_fields": [],
                        "methods_attempted": [],
                        "field_sources": {},
                    }
                )
                self._extraction_failed.append(
                    {"source_url": url, "_fail_reason": reason}
                )
            self.bug_report.record(
                url=url, reason=reason, stage="PDP Extraction"
            )
            return None, reason

    def _extract_one_impl(
        self,
        url: str,
        *,
        queue_on_failure: bool = True,
        html: str | None = None,
        platform: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        coordinator = DecisionCoordinator(
            http=self.http,
            options=self.options,
            registry=self.registry,
            site_rules=self._rules_for_url(url),
            logger=self.logger,
        )
        try:
            outcome = coordinator.extract(url, html=html, platform=platform)
        except (CaptchaBlockedError, CaptchaTimeoutError):
            raise
        report = outcome.get("report")
        if report:
            self._pdp_reports.append(report)
        variant_report = outcome.get("variant_report")
        if variant_report:
            self._variant_reports.append(variant_report)
        image_report = outcome.get("image_report")
        if image_report:
            self._image_reports.append(image_report)

        if not outcome.get("success"):
            failed = outcome.get("failed_record") or {}
            reason = str(outcome.get("reason") or "pdp_extraction_failed")
            failed["_fail_reason"] = reason
            failed.setdefault("source_url", url)
            debug = outcome.get("debug_artifacts") or {}
            self.bug_report.record(
                url=url,
                reason=reason,
                stage="PDP Extraction",
                html=str(debug.get("html") or ""),
                screenshot_png=debug.get("screenshot_png"),
                network_json=list(debug.get("network_json") or []),
            )
            if queue_on_failure:
                self._extraction_failed.append(failed)
                self._failed_reasons.append((url, reason))
                self._retry_queue.append(
                    {
                        "url": url,
                        "reason": reason,
                        "missing_fields": failed.get("missing_fields") or [],
                        "methods_attempted": (report or {}).get("methods_attempted") or [],
                        "field_sources": failed.get("field_sources") or {},
                    }
                )
            self.logger.error("PDP FAIL %s: %s", url, reason)
            return None, reason

        product = outcome.get("product")
        if not product:
            self.bug_report.record(url=url, reason="empty_product", stage="PDP Extraction")
            return None, "empty_product"
        return product, ""

    def _extract_and_validate_product(
        self, url: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        max_attempts = 2
        retry_count = 0
        last_reason = "extraction_failed"
        last_fields: dict[str, str] = {}

        for attempt in range(max_attempts):
            queue_fail = attempt == max_attempts - 1
            try:
                product, reason = self.extract_one(url, queue_on_failure=queue_fail)
            except (CaptchaBlockedError, CaptchaTimeoutError):
                raise
            if not product:
                last_reason = reason or last_reason
                if attempt < max_attempts - 1:
                    retry_count += 1
                    self.logger.info(
                        "Production validation retry (extract) %s", url
                    )
                    continue
                return None, validation_row(
                    url=url,
                    product=None,
                    status=STATUS_FAILED,
                    retry_count=retry_count,
                    failure_reason=last_reason,
                )

            product = normalize_product(product, base_url=url)
            meta = self._url_meta.get(canonicalize_product_url(url)) or self._url_meta.get(
                url
            ) or {}
            product = apply_seed_metadata(product, meta)
            from sentivo_extractor.core.confidence import apply_confidence

            product = apply_confidence(product)
            check = assess_production_fields(product)
            last_fields = check["fields"]
            if not check["missing"]:
                status = STATUS_RECOVERED if retry_count else STATUS_SUCCESS
                return product, validation_row(
                    url=url,
                    product=product,
                    status=status,
                    retry_count=retry_count,
                    field_status=check["fields"],
                )

            last_reason = check["failure_reason"]
            if attempt < max_attempts - 1:
                retry_count += 1
                self.logger.info(
                    "Production validation retry (fields) %s: %s",
                    url,
                    last_reason,
                )
                continue

            self._upsert_retry_queue(
                url,
                reason=last_reason,
                missing_fields=check["missing"],
            )
            html_snip = ""
            try:
                html_snip = self.http.get_text(url)
            except Exception:
                html_snip = ""
            self.bug_report.record(
                url=url,
                reason=last_reason,
                stage="Production Validation",
                html=html_snip,
                retry_count=retry_count,
            )
            return None, validation_row(
                url=url,
                product=product,
                status=STATUS_FAILED,
                retry_count=retry_count,
                failure_reason=last_reason,
                field_status=last_fields,
            )

        return None, validation_row(
            url=url,
            product=None,
            status=STATUS_FAILED,
            retry_count=retry_count,
            failure_reason=last_reason,
        )

    def _upsert_retry_queue(
        self, url: str, *, reason: str, missing_fields: list[str]
    ) -> None:
        entry = {
            "url": url,
            "reason": reason,
            "missing_fields": missing_fields,
            "methods_attempted": [],
            "field_sources": {},
        }
        for idx, item in enumerate(self._retry_queue):
            if item.get("url") == url:
                self._retry_queue[idx] = entry
                return
        self._retry_queue.append(entry)
        self._failed_reasons.append((url, reason))

    def _discover_all(self, seeds: list[dict[str, str]]) -> list[str]:
        urls: list[str] = []
        per_domain_count: dict[str, int] = defaultdict(int)

        for seed in seeds:
            url = (seed.get("url") or "").strip()
            if not url:
                continue
            kind = (seed.get("type") or "auto").strip().lower()
            meta = seed_metadata(seed)
            domain = domain_from_url(url)

            if kind in ("product", "pdp"):
                cu = canonicalize_product_url(url)
                urls.append(cu)
                self._url_meta[cu] = meta
                per_domain_count[domain] += 1
                continue

            remaining = self._remaining_for_domain(domain, per_domain_count[domain])
            if remaining is not None and remaining <= 0:
                self.logger.info(
                    "max_products_per_domain reached for %s — skipping more discovery",
                    domain,
                )
                continue

            card_sel = None
            rules = self._rules_for_url(url)
            sels = rules.get("selectors") or {}
            if isinstance(sels.get("product_card"), list) and sels["product_card"]:
                card_sel = sels["product_card"][0]

            try:
                # Platform detection BEFORE discovery (live Magento fallback included).
                if hasattr(self.http, "apply_browser_headers"):
                    # Magento/Hyva storefronts often 403 without browser-like headers.
                    self.http.apply_browser_headers()
                detected = detect_platform(
                    url,
                    html="",
                    session=getattr(self.http, "session", None),
                    live_fallback=True,
                )
                plat = str(detected.get("platform") or "Custom")
                self.logger.info("Detected platform: %s", plat)
                if plat == "Magento":
                    self._magento_domains.add(domain)
                    DecisionCoordinator.remember_platform(url, "Magento")

                # Magento: skip sitemap AND robots.txt gates — nav crawl only.
                prev_robots = getattr(self.http, "respect_robots", True)
                if plat == "Magento":
                    self.http.respect_robots = False

                try:
                    disc = discover_domain_products(
                        url,
                        self.http.get_text,
                        max_products=remaining if remaining is not None else 10**9,
                        # Magento must never be blocked by stale sitemap failure cache.
                        follow_sitemaps=(plat != "Magento"),
                        card_selector=card_sel,
                        platform=plat,
                        logger=self.logger,
                    )
                finally:
                    if plat == "Magento":
                        self.http.respect_robots = prev_robots
                found = disc["product_urls"]
                if not found:
                    reason = f"discovery_empty: {url}"
                    self.logger.error(reason)
                    self._failed_reasons.append((url, reason))
                    continue
                for pu in found:
                    self._url_meta[pu] = meta
                urls.extend(found)
                per_domain_count[domain] += len(found)
            except Exception as exc:
                self.logger.warning("Discovery failed for %s: %s", url, exc)
                reason = f"discovery_failed: {exc}"
                self._failed_reasons.append((url, reason))

        return unique_preserve(urls)

    def _dedupe_products(self, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        best: dict[str, dict[str, Any]] = {}
        for p in products:
            handle = (p.get("handle") or p.get("source_url") or "").strip()
            if not handle:
                continue
            prev = best.get(handle)
            if prev is None or float(p.get("confidence_score") or 0) >= float(
                prev.get("confidence_score") or 0
            ):
                best[handle] = p
        return list(best.values())

    def _export_domain_outputs(
        self,
        *,
        seeds: list[dict[str, str]],
        product_urls: list[str],
        products: list[dict[str, Any]],
        report: dict[str, Any],
        failed: list[dict[str, Any]],
        sku_failed: list[dict[str, Any]],
        sku_warnings: list[str],
        manifest_rows: list[dict[str, Any]],
        image_issues: list[dict[str, Any]],
        coverage_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Write all prefixed artifacts into per-domain subfolders."""
        passed = list(report.get("passed") or [])

        domain_keys: list[str] = []
        for seed in seeds:
            key = domain_folder_name(str(seed.get("url") or ""))
            if key not in domain_keys:
                domain_keys.append(key)
        for url in product_urls:
            key = domain_folder_name(url)
            if key not in domain_keys:
                domain_keys.append(key)
        for product in products + failed:
            key = self._product_domain_key(product)
            if key not in domain_keys:
                domain_keys.append(key)
        if not domain_keys:
            domain_keys = ["unknown"]

        allow_dup = self.duplicate_sku_policy in ("warn", "suffix", "blank")
        root_out = Path(__file__).resolve().parents[2] / "output"
        domain_summaries: dict[str, Any] = {}
        primary_summary: dict[str, Any] = {}
        bug_paths: list[str] = []

        for key in domain_keys:
            domain_dir = self._domain_dir(key)
            self.logger.info("Writing domain outputs → %s", domain_dir)
            print(f"Writing outputs → {domain_dir}")

            def _in_domain_url(url: str, k: str = key) -> bool:
                return domain_folder_name(url) == k

            def _in_domain_product(p: dict[str, Any], k: str = key) -> bool:
                return self._product_domain_key(p) == k

            d_passed = [p for p in passed if _in_domain_product(p)]
            d_failed = [p for p in failed if _in_domain_product(p)]
            d_products = [p for p in products if _in_domain_product(p)]
            d_urls = [u for u in product_urls if _in_domain_url(u)]
            d_seeds = [
                s for s in seeds if domain_folder_name(str(s.get("url") or "")) == key
            ]
            d_issues = [
                i
                for i in (report.get("issues") or [])
                if _in_domain_url(str(i.get("source_url") or ""))
            ]
            d_manifest = [
                r
                for r in manifest_rows
                if str(r.get("domain") or "") == key
                or _in_domain_url(str(r.get("source_url") or ""))
                or (
                    not r.get("domain")
                    and not r.get("source_url")
                    and len(domain_keys) == 1
                )
            ]
            d_pdp = [
                r
                for r in self._pdp_reports
                if _in_domain_url(str(r.get("url") or r.get("source_url") or ""))
            ]
            d_retry = [
                r
                for r in self._retry_queue
                if _in_domain_url(str(r.get("url") or ""))
            ]
            d_variant = [
                r
                for r in self._variant_reports
                if _in_domain_url(str(r.get("url") or r.get("source_url") or ""))
            ]
            d_image_rep = [
                r
                for r in self._image_reports
                if _in_domain_url(str(r.get("url") or r.get("source_url") or ""))
            ]
            d_image_issues = [
                i
                for i in image_issues
                if _in_domain_url(str(i.get("source_url") or i.get("url") or ""))
            ]
            d_coverage = [
                row
                for row in coverage_rows
                if domain_folder_name(str(row.get("domain") or row.get("seed_url") or ""))
                == key
                or str(row.get("domain") or "").endswith(
                    domain_from_url(d_seeds[0]["url"]) if d_seeds else "__none__"
                )
                or (
                    len(domain_keys) == 1
                    and not row.get("domain")
                )
            ]
            # Coverage rows typically use full host in "domain" — match folder label too.
            if not d_coverage:
                d_coverage = [
                    row
                    for row in coverage_rows
                    if domain_folder_name(str(row.get("domain") or "")) == key
                ]
            d_failed_reasons = [
                (u, e) for u, e in self._failed_reasons if _in_domain_url(u)
            ]
            d_validation_rows = [
                r
                for r in self._validation_rows
                if _in_domain_url(str(r.get("URL") or r.get("url") or ""))
            ]

            csv_out = self._artifact(key, "shopify_import.csv")
            export_shopify_csv(d_passed, csv_out)
            export_failed_csv(d_failed, self._artifact(key, "failed_products.csv"))

            debug_dir = domain_dir / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            write_pdp_extraction_report(
                d_pdp, debug_dir / "product_extraction_report.json"
            )
            write_retry_queue(d_retry, self._artifact(key, "retry_queue.json"))
            write_variant_report(d_variant, debug_dir / "variant_report.json")
            write_image_report(d_image_rep, debug_dir / "image_report.json")

            export_validation_report(
                d_issues, self._artifact(key, "validation_report.xlsx")
            )
            export_images_manifest(
                d_manifest, self._artifact(key, "images_manifest.csv")
            )

            preimport = validate_shopify_csv(csv_out, allow_duplicate_sku=allow_dup)
            write_preimport_validation_report(
                preimport,
                self._artifact(key, "shopify_pre_import_validation.xlsx"),
            )

            qa_rows = sample_products_for_qa(
                d_products,
                per_domain=self.qa_sample_size,
                seed=self.qa_random_seed,
            )
            qa_dir = domain_dir / "qa"
            write_qa_sample_workbook(qa_rows, qa_dir / "sample_review.xlsx")

            d_report = {
                "passed": d_passed,
                "failed": [p for p in (report.get("failed") or []) if _in_domain_product(p)],
                "issues": d_issues,
                "summary": {
                    "errors": sum(
                        1 for i in d_issues if i.get("severity") == "error"
                    ),
                    "warnings": sum(
                        1 for i in d_issues if i.get("severity") == "warning"
                    ),
                },
            }
            # Temporarily scope failed reasons for summary helper
            saved_reasons = self._failed_reasons
            saved_stats = dict(self._crawl_stats)
            self._failed_reasons = d_failed_reasons
            self._crawl_stats = {
                "discovered": len(d_urls),
                "processed": len(d_products),
                "skipped": saved_stats.get("skipped", 0),
                "failed": len(d_failed_reasons),
            }
            d_summary = self._build_production_summary(
                seeds=d_seeds or seeds,
                discovered=d_urls,
                products=d_products,
                report=d_report,
                coverage_rows=d_coverage,
                preimport=preimport,
                image_issues=d_image_issues,
                sku_warnings=sku_warnings,
            )
            self._failed_reasons = saved_reasons
            self._crawl_stats = saved_stats
            d_summary["domain"] = key
            d_summary["output_dir"] = str(domain_dir)
            d_summary["shopify_import_csv"] = str(csv_out)

            yellow_items = [
                {
                    "handle": p.get("handle"),
                    "title": p.get("title"),
                    "source_url": p.get("source_url"),
                    "confidence_score": p.get("confidence_score"),
                }
                for p in d_products
                if confidence_band(float(p.get("confidence_score") or 0)) == "yellow"
            ]
            red_items = [
                {
                    "handle": p.get("handle"),
                    "title": p.get("title"),
                    "source_url": p.get("source_url"),
                    "confidence_score": p.get("confidence_score"),
                }
                for p in d_products
                if confidence_band(float(p.get("confidence_score") or 0)) == "red"
            ]
            domain_summary_rows = self._domain_summary_rows(
                d_products, d_urls, d_coverage
            )
            extraction_errors = [
                {"url": u, "domain": domain_from_url(u), "error": e}
                for u, e in d_failed_reasons
            ]
            validation_errors = [
                i for i in d_issues if i.get("severity") == "error"
            ] + [
                i
                for i in (preimport.get("issues") or [])
                if i.get("severity") == "error"
            ]

            write_production_summary_workbook(
                self._artifact(key, "production_summary.xlsx"),
                overview=d_summary,
                domain_summary=domain_summary_rows,
                extraction_errors=extraction_errors,
                validation_errors=validation_errors,
                yellow_items=yellow_items,
                red_items=red_items,
                image_issues=d_image_issues,
                coverage=d_coverage,
            )
            write_json(self._artifact(key, "run_summary.json"), d_summary)

            if self.production_validation and d_validation_rows:
                val_summary = build_validation_summary(d_validation_rows)
                write_final_validation_report(
                    self._artifact(key, "final_validation_report.xlsx"),
                    d_validation_rows,
                    val_summary,
                )
                d_summary["production_validation"] = val_summary

            # Optional mirror under project output/<domain>/
            if root_out.resolve() != self.base_output_dir.resolve():
                try:
                    mirror = ensure_domain_dir(root_out, key)
                    export_failed_csv(
                        d_failed,
                        domain_artifact_path(root_out, key, "failed_products.csv"),
                    )
                    write_retry_queue(
                        d_retry,
                        domain_artifact_path(root_out, key, "retry_queue.json"),
                    )
                    del mirror
                except Exception:
                    pass

            domain_summaries[key] = d_summary
            if not primary_summary:
                primary_summary = d_summary

        bug_written = self.bug_report.write_by_domain(
            retry_queue=self._retry_queue,
            domain_keys=domain_keys,
        )
        bug_paths = [str(p) for p in bug_written]
        for path in bug_written:
            self.logger.info("Bug report: %s", path)

        summary = dict(primary_summary) if primary_summary else {}
        summary["output_dir"] = str(self.base_output_dir)
        summary["domain_output_dirs"] = {
            k: str(self._domain_dir(k)) for k in domain_keys
        }
        summary["domain_summaries"] = domain_summaries
        summary["bug_report"] = bug_paths[0] if bug_paths else ""
        summary["bug_reports"] = bug_paths
        summary["bug_failures"] = len(self.bug_report.records)
        summary["total_input_urls"] = len(seeds)
        summary["total_discovered_product_urls"] = len(product_urls)
        summary["total_extracted_products"] = len(products)
        summary["products_discovered"] = self._crawl_stats.get("discovered", 0)
        summary["products_processed"] = self._crawl_stats.get("processed", 0)
        summary["products_skipped"] = self._crawl_stats.get("skipped", 0)
        summary["products_failed_crawl"] = self._crawl_stats.get("failed", 0)
        if self.production_validation and self._validation_rows:
            summary["production_validation"] = build_validation_summary(
                self._validation_rows
            )
        return summary

    def _build_production_summary(
        self,
        *,
        seeds: list[dict[str, str]],
        discovered: list[str],
        products: list[dict[str, Any]],
        report: dict[str, Any],
        coverage_rows: list[dict[str, Any]] | None = None,
        preimport: dict[str, Any] | None = None,
        image_issues: list[dict[str, Any]] | None = None,
        sku_warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        bands = Counter(
            confidence_band(float(p.get("confidence_score") or 0)) for p in products
        )
        by_domain: dict[str, Counter] = defaultdict(Counter)
        for url, reason in self._failed_reasons:
            by_domain[domain_from_url(url)][reason[:120]] += 1
        for p in report.get("failed") or []:
            by_domain[domain_from_url(str(p.get("source_url") or ""))][
                "validation_failed"
            ] += 1

        top_errors = {
            domain: [{"reason": r, "count": c} for r, c in counter.most_common(5)]
            for domain, counter in by_domain.items()
            if counter
        }

        coverage_rows = coverage_rows or []
        preimport = preimport or {"summary": {}}
        return {
            "total_input_urls": len(seeds),
            "total_discovered_product_urls": len(discovered),
            "total_extracted_products": len(products),
            "green": bands.get("green", 0),
            "yellow": bands.get("yellow", 0),
            "red": bands.get("red", 0),
            "failed_urls": len(self._failed_reasons),
            "validation_failed": len(report.get("failed") or []),
            "validation_errors": report.get("summary", {}).get("errors", 0),
            "validation_warnings": report.get("summary", {}).get("warnings", 0),
            "preimport_errors": (preimport.get("summary") or {}).get("errors", 0),
            "preimport_warnings": (preimport.get("summary") or {}).get("warnings", 0),
            "image_issues": len(image_issues or []),
            "sku_warnings": len(sku_warnings or []),
            "coverage": coverage_rows,
            "pilot": self.pilot,
            "top_error_reasons_by_domain": top_errors,
            "output_dir": str(self.base_output_dir),
            "products_discovered": self._crawl_stats.get("discovered", 0),
            "products_processed": self._crawl_stats.get("processed", 0),
            "products_skipped": self._crawl_stats.get("skipped", 0),
            "products_failed_crawl": self._crawl_stats.get("failed", 0),
        }

    def _print_summary(self, summary: dict[str, Any]) -> None:
        lines = [
            "=== Production Extraction Summary ===",
            f"Total input URLs: {summary.get('total_input_urls')}",
            f"Total discovered product URLs: {summary.get('total_discovered_product_urls')}",
            f"Total extracted products: {summary.get('total_extracted_products')}",
            f"Green (0.90+): {summary.get('green')}",
            f"Yellow (0.70–0.89): {summary.get('yellow')}",
            f"Red (<0.70): {summary.get('red')}",
            f"Failed URLs: {summary.get('failed_urls')}",
            f"Pre-import errors: {summary.get('preimport_errors')}",
            f"Image issues: {summary.get('image_issues')}",
        ]
        if summary.get("pilot"):
            lines.append("Mode: PILOT")
        cov = summary.get("coverage") or []
        if cov:
            lines.append("Coverage by domain:")
            for row in cov[:15]:
                lines.append(
                    f"  {row.get('domain')}: expected={row.get('expected_count')} "
                    f"extracted={row.get('extracted_count')} "
                    f"coverage={row.get('coverage_percent')}% "
                    f"status={row.get('coverage_status')}"
                )
        top = summary.get("top_error_reasons_by_domain") or {}
        if top:
            lines.append("Top error reasons by domain:")
            for domain, items in list(top.items())[:10]:
                lines.append(f"  {domain}:")
                for item in items:
                    lines.append(f"    - {item['reason']} ({item['count']})")
        text = "\n".join(lines)
        print(text, flush=True)
        for line in lines:
            self.logger.info(line)
