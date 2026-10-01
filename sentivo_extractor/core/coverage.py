"""Expected-count coverage metrics for audit/extract."""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import urlparse


def domain_of(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def coverage_row(
    *,
    domain: str,
    expected_count: int | None,
    discovered_count: int,
    extracted_count: int,
    min_coverage_percent: float = 90.0,
) -> dict[str, Any]:
    expected = int(expected_count) if expected_count not in (None, "") else None
    discovered = int(discovered_count or 0)
    extracted = int(extracted_count or 0)
    missing = max(0, (expected or 0) - extracted) if expected is not None else 0
    over = max(0, discovered - expected) if expected is not None else 0
    if expected and expected > 0:
        coverage = round(100.0 * extracted / expected, 2)
    else:
        coverage = None

    risk = "n/a"
    status = "ok"
    if expected is not None and expected > 0 and coverage is not None:
        if coverage < float(min_coverage_percent):
            risk = "high"
            status = "fail" if coverage < float(min_coverage_percent) * 0.75 else "warning"
        elif coverage < 100:
            risk = "medium"
            status = "warning"
        else:
            risk = "low"
            status = "ok"

    return {
        "domain": domain,
        "expected_count": expected if expected is not None else "",
        "discovered_count": discovered,
        "extracted_count": extracted,
        "missing_count": missing if expected is not None else "",
        "over_discovered_count": over if expected is not None else "",
        "coverage_percent": coverage if coverage is not None else "",
        "min_coverage_percent": min_coverage_percent,
        "coverage_status": status,
        "risk_level": risk,
    }


def build_coverage_report(
    *,
    seeds: list[dict[str, str]],
    discovered_urls: list[str],
    products: list[dict[str, Any]],
    min_coverage_percent: float = 90.0,
) -> list[dict[str, Any]]:
    """Aggregate expected/discovered/extracted coverage per domain."""
    expected_by_domain: dict[str, int] = {}
    for seed in seeds:
        dom = domain_of(seed.get("url") or "")
        raw = (seed.get("expected_count") or "").strip()
        if not raw or not dom:
            continue
        try:
            n = int(float(raw))
        except ValueError:
            continue
        # Sum expected across seeds for same domain (or take max if duplicates)
        expected_by_domain[dom] = expected_by_domain.get(dom, 0) + n

    discovered_by_domain: dict[str, int] = defaultdict(int)
    for url in discovered_urls:
        discovered_by_domain[domain_of(url)] += 1

    extracted_by_domain: dict[str, int] = defaultdict(int)
    for p in products:
        extracted_by_domain[domain_of(str(p.get("source_url") or ""))] += 1

    domains = sorted(
        set(expected_by_domain)
        | set(discovered_by_domain)
        | set(extracted_by_domain)
    )
    rows = []
    for dom in domains:
        if not dom:
            continue
        rows.append(
            coverage_row(
                domain=dom,
                expected_count=expected_by_domain.get(dom),
                discovered_count=discovered_by_domain.get(dom, 0),
                extracted_count=extracted_by_domain.get(dom, 0),
                min_coverage_percent=min_coverage_percent,
            )
        )
    return rows


def coverage_enforcement_failed(
    rows: list[dict[str, Any]], *, enforce: bool
) -> bool:
    if not enforce:
        return False
    return any(r.get("coverage_status") in ("warning", "fail") for r in rows)
