"""Audit-first workflow: discover + sample extract + domain reports."""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sentivo_extractor.core.bug_report import BugReportCollector, print_failure_groups
from sentivo_extractor.core.confidence import apply_confidence, confidence_band
from sentivo_extractor.core.coverage import coverage_row
from sentivo_extractor.core.http_client import HttpClient
from sentivo_extractor.core.input_csv import apply_seed_metadata, read_seed_csv, seed_metadata
from sentivo_extractor.core.normalizer import normalize_product
from sentivo_extractor.core.platform_detector import detect_platform
from sentivo_extractor.core.product_discovery import (
    clear_sitemap_failure_cache,
    discover_domain_products,
)
from sentivo_extractor.core.site_rule_suggester import (
    domain_from_url,
    write_site_rule_suggestion,
)
from sentivo_extractor.core.utils import DEFAULT_USER_AGENT, close_logger, setup_logger, write_json
from sentivo_extractor.extractors import build_default_registry


def _rate(success: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(success / total, 3)


class DomainAuditor:
    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options
        self.output_dir = Path(options.get("output") or "output/audit")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logger("sentivo_extractor.audit", self.output_dir / "logs")
        self.http = HttpClient(
            user_agent=options.get("user_agent") or DEFAULT_USER_AGENT,
            timeout=float(options.get("timeout") or 25),
            delay_sec=float(options.get("delay") or 1.0),
            retries=int(options.get("retries") or 3),
            respect_robots=bool(options.get("respect_robots", True)),
            logger=self.logger,
        )
        self.registry = build_default_registry()
        self.sample_size = int(options.get("sample_size") or 5)
        self.max_products = int(options.get("max_products_per_domain") or 200)
        self.min_coverage_percent = float(options.get("min_coverage_percent") or 90)
        self.bug_report = BugReportCollector(self.output_dir, run_mode="audit")

    def run(self, input_csv: Path) -> dict[str, Any]:
        clear_sitemap_failure_cache()
        self.logger.info("Product Discovery Version: Unified")
        seeds = read_seed_csv(input_csv)
        discovery_rows: list[dict[str, Any]] = []
        domain_rows: list[dict[str, Any]] = []
        sample_dir = self.output_dir / "sample_products_raw"
        suggestions_dir = self.output_dir / "site_rule_suggestions"
        sample_dir.mkdir(parents=True, exist_ok=True)
        suggestions_dir.mkdir(parents=True, exist_ok=True)

        # Group seeds by domain while preserving order
        by_domain: dict[str, list[dict[str, str]]] = defaultdict(list)
        for seed in seeds:
            by_domain[domain_from_url(seed["url"])].append(seed)

        for domain, domain_seeds in by_domain.items():
            self.logger.info("Auditing domain %s (%s seeds)", domain, len(domain_seeds))
            row = self._audit_domain(domain, domain_seeds, sample_dir, suggestions_dir)
            domain_rows.append(row["domain_row"])
            discovery_rows.extend(row["discovery_rows"])

        self._write_discovery_csv(discovery_rows)
        self._write_domain_xlsx(domain_rows)
        summary = {
            "domains": len(domain_rows),
            "seeds": len(seeds),
            "product_urls_found": sum(int(r.get("product_urls_found") or 0) for r in domain_rows),
            "output_dir": str(self.output_dir),
        }
        write_json(self.output_dir / "audit_summary.json", summary)
        bug_path = self.bug_report.write(
            self.output_dir / "bug_report.xlsx",
            retry_queue=[],
        )
        try:
            root_out = Path(__file__).resolve().parents[2] / "output"
            if root_out.resolve() != self.output_dir.resolve():
                self.bug_report.write(root_out / "bug_report.xlsx", retry_queue=[])
        except Exception:
            pass
        print_failure_groups(self.bug_report.records)
        summary["bug_report"] = str(bug_path)
        summary["bug_failures"] = len(self.bug_report.records)
        self.logger.info("Audit summary: %s", summary)
        close_logger(self.logger)
        return summary

    def _audit_domain(
        self,
        domain: str,
        seeds: list[dict[str, str]],
        sample_dir: Path,
        suggestions_dir: Path,
    ) -> dict[str, Any]:
        all_product_urls: list[str] = []
        discovery_rows: list[dict[str, Any]] = []
        platform = "Unknown"
        html_samples: list[str] = []
        notes: list[str] = []
        primary = seeds[0]
        meta = seed_metadata(primary)

        for seed in seeds:
            url = seed["url"]
            kind = (seed.get("type") or "auto").lower()
            try:
                html = self.http.get_text(url)
                html_samples.append(html[:200_000])
                detected = detect_platform(url, html=html, session=self.http.session)
                platform = detected.get("platform") or platform
            except Exception as exc:
                notes.append(f"seed_fetch_failed:{url}:{exc}")
                html = ""

            if kind in ("product", "pdp"):
                products = [url]
                disc_notes: list[str] = []
            else:
                disc = discover_domain_products(
                    url,
                    self.http.get_text,
                    max_products=self.max_products,
                    follow_sitemaps=True,
                    platform=platform,
                    logger=self.logger,
                )
                products = disc["product_urls"]
                disc_notes = disc.get("notes") or []
                if not products:
                    disc_notes.append(f"discovery_empty:{url}")
                    self.logger.error("discovery_empty: %s", url)

            for pu in products:
                discovery_rows.append(
                    {
                        "domain": domain,
                        "seed_url": url,
                        "seed_type": kind,
                        "department": seed.get("department") or meta.get("department") or "",
                        "product_url": pu,
                        "platform_detected": platform,
                    }
                )
            all_product_urls.extend(products)
            notes.extend(disc_notes)

        # Deduplicate product urls
        seen = set()
        unique_urls = []
        for u in all_product_urls:
            if u not in seen:
                seen.add(u)
                unique_urls.append(u)

        sample_urls = unique_urls[: self.sample_size]
        stats = {
            "title": 0,
            "price": 0,
            "image": 0,
            "description": 0,
            "variant": 0,
            "tested": 0,
        }
        methods: list[str] = []
        confidences: list[float] = []
        needs_playwright = False

        for i, purl in enumerate(sample_urls):
            try:
                product, used_pw = self._extract_sample(purl, meta)
            except Exception as exc:
                notes.append(f"sample_failed:{purl}:{exc}")
                needs_playwright = True
                html_snip = ""
                try:
                    html_snip = self.http.get_text(purl)
                except Exception:
                    html_snip = ""
                self.bug_report.record(
                    url=purl,
                    reason=str(exc),
                    stage="Audit",
                    html=html_snip,
                )
                continue
            if used_pw:
                needs_playwright = True
            if not product:
                needs_playwright = True
                html_snip = ""
                try:
                    html_snip = self.http.get_text(purl)
                except Exception:
                    html_snip = ""
                self.bug_report.record(
                    url=purl,
                    reason="audit_sample_extract_failed",
                    stage="Audit",
                    html=html_snip,
                )
                continue
            product = normalize_product(product, base_url=purl)
            product = apply_seed_metadata(product, meta)
            product = apply_confidence(product)
            stats["tested"] += 1
            if product.get("title"):
                stats["title"] += 1
            if any(str(v.get("price") or "") for v in product.get("variants") or []) or product.get(
                "price"
            ):
                stats["price"] += 1
            if product.get("images"):
                stats["image"] += 1
            if product.get("description_html"):
                stats["description"] += 1
            opts = product.get("options") or []
            variants = product.get("variants") or []
            if len(variants) > 1 or any(
                (o.get("name") or "") not in ("", "Title") for o in opts
            ):
                stats["variant"] += 1
            methods.append(str(product.get("extraction_method") or ""))
            confidences.append(float(product.get("confidence_score") or 0))
            write_json(sample_dir / f"{domain}_{i+1}_{product.get('handle') or 'product'}.json", product)
            if float(product.get("confidence_score") or 0) < 0.7:
                needs_playwright = True

        tested = max(stats["tested"], 1) if sample_urls else 0
        avg_conf = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
        recommended = self._recommend_extractor(platform, methods, avg_conf, needs_playwright)
        needs_yaml = avg_conf < 0.7 or stats["tested"] == 0 or _rate(stats["title"], tested) < 0.8
        if needs_yaml and html_samples:
            write_site_rule_suggestion(
                domain,
                html_samples[:3],
                suggestions_dir / f"{domain}.yaml",
                platform=platform,
                notes=notes[:10],
            )

        risk = "low"
        if avg_conf < 0.7 or stats["tested"] == 0:
            risk = "high"
        elif avg_conf < 0.9 or needs_playwright:
            risk = "medium"

        # Expected count coverage (discovered vs expected; sample extract used as proxy)
        expected = meta.get("expected_count")
        if expected is None:
            for s in seeds:
                sm = seed_metadata(s)
                if sm.get("expected_count") is not None:
                    expected = (expected or 0) + int(sm["expected_count"])
        cov = coverage_row(
            domain=domain,
            expected_count=int(expected) if expected not in (None, "") else None,
            discovered_count=len(unique_urls),
            extracted_count=stats["tested"],  # audit uses sample extract count
            min_coverage_percent=self.min_coverage_percent,
        )
        if cov.get("risk_level") == "high":
            risk = "high"
        elif cov.get("risk_level") == "medium" and risk == "low":
            risk = "medium"

        domain_row = {
            "domain": domain,
            "platform_detected": platform,
            "input_url": primary.get("url") or "",
            "input_type": primary.get("type") or "auto",
            "department": primary.get("department") or meta.get("department") or "",
            "product_urls_found": len(unique_urls),
            "sample_products_tested": stats["tested"],
            "title_success_rate": _rate(stats["title"], tested) if sample_urls else 0.0,
            "price_success_rate": _rate(stats["price"], tested) if sample_urls else 0.0,
            "image_success_rate": _rate(stats["image"], tested) if sample_urls else 0.0,
            "description_success_rate": _rate(stats["description"], tested) if sample_urls else 0.0,
            "variant_success_rate": _rate(stats["variant"], tested) if sample_urls else 0.0,
            "avg_confidence": avg_conf,
            "recommended_extractor": recommended,
            "needs_yaml_rules": "yes" if needs_yaml else "no",
            "needs_playwright": "yes" if needs_playwright else "no",
            "risk_level": risk,
            "expected_count": cov.get("expected_count", ""),
            "discovered_count": cov.get("discovered_count", ""),
            "extracted_count": cov.get("extracted_count", ""),
            "missing_count": cov.get("missing_count", ""),
            "over_discovered_count": cov.get("over_discovered_count", ""),
            "coverage_percent": cov.get("coverage_percent", ""),
            "coverage_status": cov.get("coverage_status", ""),
            "notes": "; ".join(notes[:8]),
        }
        return {"domain_row": domain_row, "discovery_rows": discovery_rows}

    def _extract_sample(self, url: str, meta: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
        html = ""
        used_pw = False
        fetch_blocked = False
        try:
            html = self.http.get_text(url)
        except Exception:
            fetch_blocked = True
        detected = detect_platform(url, html=html, session=self.http.session)
        platform = detected.get("platform") or "Custom"
        context = {
            "http": self.http,
            "platform": platform,
            # HTTP-first; Playwright only as last resort below.
            "use_playwright": False,
            "timeout_ms": int(float(self.options.get("timeout") or 25) * 1000),
            "capture_network": True,
            # Render-only Playwright — no variant clicking.
            "probe_variants": False,
        }
        best = None
        for extractor in self.registry.for_platform(platform):
            if extractor.name == "playwright":
                continue
            try:
                result = extractor.extract(url, html, context=context)
            except Exception:
                continue
            if result and result.get("title"):
                best = result
                if float(result.get("confidence_score") or 0) >= 0.85:
                    break
        # Playwright only when HTTP extractors empty/blocked.
        if best is None or not best.get("title") or (
            fetch_blocked and not (html or "").strip()
        ):
            from sentivo_extractor.extractors.playwright_extractor import PlaywrightExtractor

            used_pw = True
            context["use_playwright"] = True
            best = PlaywrightExtractor().extract(url, html, context=context)
        return best, used_pw

    def _recommend_extractor(
        self, platform: str, methods: list[str], avg_conf: float, needs_pw: bool
    ) -> str:
        if methods:
            # most common method
            from collections import Counter

            top = Counter(methods).most_common(1)[0][0]
            if top:
                return top
        if platform == "Shopify":
            return "shopify"
        if platform == "WooCommerce":
            return "woocommerce"
        if needs_pw or avg_conf < 0.7:
            return "playwright"
        if platform in ("Next.js", "Nuxt", "React"):
            return "nextjs"
        return "html+jsonld"

    def _write_discovery_csv(self, rows: list[dict[str, Any]]) -> Path:
        path = self.output_dir / "product_discovery_report.csv"
        fields = [
            "domain",
            "seed_url",
            "seed_type",
            "department",
            "product_url",
            "platform_detected",
        ]
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fields})
        return path

    def _write_domain_xlsx(self, rows: list[dict[str, Any]]) -> Path:
        path = self.output_dir / "domain_audit.xlsx"
        fields = [
            "domain",
            "platform_detected",
            "input_url",
            "input_type",
            "department",
            "product_urls_found",
            "sample_products_tested",
            "title_success_rate",
            "price_success_rate",
            "image_success_rate",
            "description_success_rate",
            "variant_success_rate",
            "avg_confidence",
            "recommended_extractor",
            "needs_yaml_rules",
            "needs_playwright",
            "risk_level",
            "expected_count",
            "discovered_count",
            "extracted_count",
            "missing_count",
            "over_discovered_count",
            "coverage_percent",
            "coverage_status",
            "notes",
        ]
        try:
            import openpyxl

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Domain Audit"
            ws.append(fields)
            for row in rows:
                ws.append([row.get(f, "") for f in fields])
            wb.save(path)
        except Exception:
            csv_path = path.with_suffix(".csv")
            with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                for row in rows:
                    writer.writerow({k: row.get(k, "") for k in fields})
            return csv_path
        return path
