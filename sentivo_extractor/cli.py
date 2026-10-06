"""CLI for Universal Product Extractor (extract + audit)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sentivo_extractor.core.utils import DEFAULT_USER_AGENT, configure_stdio_utf8


def _parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--input",
        required=True,
        help="CSV of seed URLs (url, type, optional metadata columns)",
    )
    p.add_argument("--output", default="output", help="Output directory")
    p.add_argument("--download-images", default="false", help="true/false")
    p.add_argument(
        "--use-playwright",
        default="false",
        help="Deprecated/ignored: Playwright auto-activates only after HTTP extractors fail",
    )
    p.add_argument("--delay", default="1.0", help="Seconds between requests")
    p.add_argument("--timeout", default="25", help="HTTP timeout seconds")
    p.add_argument("--retries", default="3", help="Retry count")
    p.add_argument("--respect-robots", default="true", help="true/false")
    p.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    p.add_argument("--site-rules-dir", default="", help="Directory of site YAML rules")
    p.add_argument(
        "--max-products-per-domain",
        default="500",
        help="Cap discovered product URLs per domain",
    )
    # Coverage / expected count
    p.add_argument("--enforce-expected-count", default="false", help="true/false")
    p.add_argument("--min-coverage-percent", default="90", help="Coverage threshold")
    # QA
    p.add_argument("--qa-sample-size-per-domain", default="20")
    p.add_argument("--qa-random-seed", default="42")
    # SKU / images
    p.add_argument(
        "--duplicate-sku-policy",
        default="warn",
        choices=["warn", "suffix", "blank", "fail"],
    )
    p.add_argument("--check-image-urls", default="false", help="true/false")
    p.add_argument(
        "--verify-product-images",
        default="true",
        help="Verify images during PDP extraction (HTTP 200, type, dimensions)",
    )
    p.add_argument("--min-image-width", default="50", help="Minimum image width in pixels")
    p.add_argument("--min-image-height", default="50", help="Minimum image height in pixels")
    # Pilot
    p.add_argument("--pilot", default="false", help="true/false")
    p.add_argument("--pilot-size-per-domain", default="20")
    p.add_argument("--overwrite", default="false", help="true/false")
    p.add_argument(
        "--production-validation",
        default="false",
        help="Validate each product (title, price, images, variants, description, SKU)",
    )
    p.add_argument(
        "--strict",
        default="false",
        help="Fail with exit code if production validation success rate < 95%%",
    )
    p.add_argument(
        "--vendor",
        default="",
        help="Override Vendor for all extracted products in CSV output",
    )


def _options_from_args(args: argparse.Namespace) -> dict:
    return {
        "output": args.output,
        "download_images": _parse_bool(args.download_images),
        "use_playwright": _parse_bool(args.use_playwright),
        "delay": float(args.delay),
        "timeout": float(args.timeout),
        "retries": int(args.retries),
        "respect_robots": _parse_bool(args.respect_robots),
        "user_agent": args.user_agent,
        "site_rules_dir": args.site_rules_dir or None,
        "max_products_per_domain": int(args.max_products_per_domain),
        "sample_size": int(getattr(args, "sample_size", 5) or 5),
        "enforce_expected_count": _parse_bool(
            getattr(args, "enforce_expected_count", "false")
        ),
        "min_coverage_percent": float(getattr(args, "min_coverage_percent", 90) or 90),
        "qa_sample_size_per_domain": int(
            getattr(args, "qa_sample_size_per_domain", 20) or 20
        ),
        "qa_random_seed": int(getattr(args, "qa_random_seed", 42) or 42),
        "duplicate_sku_policy": str(
            getattr(args, "duplicate_sku_policy", "warn") or "warn"
        ),
        "check_image_urls": _parse_bool(getattr(args, "check_image_urls", "false")),
        "verify_product_images": _parse_bool(
            getattr(args, "verify_product_images", "true")
        ),
        "min_image_width": int(getattr(args, "min_image_width", 50) or 50),
        "min_image_height": int(getattr(args, "min_image_height", 50) or 50),
        "pilot": _parse_bool(getattr(args, "pilot", "false")),
        "pilot_size_per_domain": int(getattr(args, "pilot_size_per_domain", 20) or 20),
        "overwrite": _parse_bool(getattr(args, "overwrite", "false")),
        "production_validation": _parse_bool(
            getattr(args, "production_validation", "false")
        ),
        "strict": _parse_bool(getattr(args, "strict", "false")),
        "vendor": str(getattr(args, "vendor", "") or "").strip(),
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Universal Product Extractor + Shopify CSV Exporter",
    )
    sub = p.add_subparsers(dest="command")

    extract_p = sub.add_parser("extract", help="Run full extraction pipeline")
    _add_common_args(extract_p)

    audit_p = sub.add_parser("audit", help="Audit domains before full extraction")
    _add_common_args(audit_p)
    audit_p.add_argument(
        "--sample-size",
        default="5",
        help="Sample products to test per domain",
    )

    health_p = sub.add_parser(
        "site-health",
        help="Audit → pilot → full → validation + site health report",
    )
    _add_common_args(health_p)
    health_p.set_defaults(use_playwright="false")

    _add_common_args(p)
    return p


def run_site_health(args: argparse.Namespace) -> int:
    from sentivo_extractor.core.site_health import (
        build_site_health_report,
        run_site_health_pipeline,
        write_site_health_outputs,
    )

    input_csv = Path(args.input)
    output_root = Path(args.output or "output")
    options = _options_from_args(args)

    if _parse_bool(getattr(args, "report_only", "false")):
        report = build_site_health_report(
            output_root=output_root,
            input_csv=input_csv,
        )
        xlsx, js = write_site_health_outputs(output_root, report)
        print(f"Site health report: {xlsx}")
        print(f"Site health summary: {js}")
        return 0

    try:
        report = run_site_health_pipeline(input_csv, output_root, options)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 2

    xlsx = output_root / "site_health_report.xlsx"
    js = output_root / "site_health_summary.json"
    print("\n=== Site health complete ===")
    print(f"Report: {xlsx}")
    print(f"Summary: {js}")
    for row in report.get("domains") or []:
        print(
            f"  {row.get('Domain')}: {row.get('Recommended Action')} "
            f"(success {row.get('Success Rate')}%)"
        )
    return 0


def run_extract(args: argparse.Namespace) -> int:
    from sentivo_extractor.core.crawler import UniversalCrawler
    from sentivo_extractor.core.production_validation import strict_validation_passed

    options = _options_from_args(args)
    if options.get("strict"):
        options["production_validation"] = True
    if options.get("pilot") and (not args.output or args.output == "output"):
        options["output"] = "output/pilot"
    crawler = UniversalCrawler(options)
    try:
        summary = crawler.run(Path(args.input))
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 2
    print("\n=== Extract complete ===")
    out = Path(options["output"])
    domain_dirs = summary.get("domain_output_dirs") or {}
    if domain_dirs:
        for domain, path in domain_dirs.items():
            d = Path(path)
            print(f"[{domain}] dir: {d}")
            print(f"[{domain}] CSV: {d / f'{domain}_shopify_import.csv'}")
            print(f"[{domain}] Summary: {d / f'{domain}_production_summary.xlsx'}")
            print(f"[{domain}] Run JSON: {d / f'{domain}_run_summary.json'}")
            if options.get("production_validation"):
                print(
                    f"[{domain}] Validation: "
                    f"{d / f'{domain}_final_validation_report.xlsx'}"
                )
    else:
        print(f"Output base: {out}")
        print("(domain subfolders with prefixed filenames)")
    if summary.get("coverage_enforcement") == "failed":
        return 3
    if options.get("strict"):
        pv = summary.get("production_validation") or {}
        if strict_validation_passed(pv):
            print("PRODUCTION VALIDATION PASSED")
            return 0
        print("PRODUCTION VALIDATION FAILED")
        return 4
    return 0


def run_audit(args: argparse.Namespace) -> int:
    from sentivo_extractor.core.audit import DomainAuditor

    options = _options_from_args(args)
    if not options.get("output") or options["output"] == "output":
        options["output"] = "output/audit"
    auditor = DomainAuditor(options)
    summary = auditor.run(Path(args.input))
    print("\n=== Audit complete ===")
    for k, v in summary.items():
        print(f"{k}: {v}")
    print(f"Domain report: {Path(options['output']) / 'domain_audit.xlsx'}")
    print(f"Discovery report: {Path(options['output']) / 'product_discovery_report.csv'}")
    return 0


def run_cli(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    argv = list(argv if argv is not None else sys.argv[1:])
    command = None
    if argv and argv[0] in ("audit", "extract", "site-health"):
        command = argv.pop(0)

    parser = build_parser()
    if command:
        focused = argparse.ArgumentParser(description=f"sentivo_extractor {command}")
        _add_common_args(focused)
        if command == "audit":
            focused.add_argument("--sample-size", default="5")
        if command == "site-health":
            focused.add_argument(
                "--report-only",
                default="false",
                help="Rebuild site_health_report from existing phase outputs only",
            )
        args = focused.parse_args(argv)
        args.command = command
    else:
        args = parser.parse_args(argv)
        if not getattr(args, "command", None):
            args.command = "extract"

    if args.command == "audit":
        return run_audit(args)
    if args.command == "site-health":
        return run_site_health(args)
    return run_extract(args)


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
