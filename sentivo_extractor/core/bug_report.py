"""Production bug report — capture failures accurately, never auto-fix."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sentivo_extractor.core.output_layout import (
    domain_artifact_path,
    domain_folder_name,
    ensure_domain_dir,
)
from sentivo_extractor.core.site_rule_suggester import domain_from_url
from sentivo_extractor.core.utils import write_json

STAGE_AUDIT = "Audit"
STAGE_PILOT = "Pilot"
STAGE_FULL = "Full"
STAGE_VALIDATION = "Production Validation"
STAGE_PDP = "PDP Extraction"
STAGE_DISCOVERY = "Discovery"
STAGE_FETCH = "Fetch"
STAGE_IMAGE = "Image Engine"
STAGE_VARIANT = "Variant Engine"
STAGE_VALIDATE = "Validation"

REASON_LABELS = (
    ("timeout", "Timeout"),
    ("timed out", "Timeout"),
    ("fetch_failed", "Fetch Failed"),
    ("selector", "Selector Failed"),
    ("missing_production_fields", "Required Fields Missing"),
    ("missing_required", "Required Fields Missing"),
    ("missing_valid_images", "Image Failed"),
    ("variant", "Variant Failed"),
    ("price", "Price Missing"),
    ("sku", "SKU Missing"),
    ("title", "Title Missing"),
    ("description", "Description Missing"),
    ("images", "Image Failed"),
    ("duplicate_sku", "Duplicate SKU"),
    ("validation", "Validation Failed"),
)


@dataclass
class FailureRecord:
    product_url: str
    domain: str
    failure_stage: str
    failure_reason: str
    reason_group: str
    html_path: str = ""
    screenshot_path: str = ""
    network_log_path: str = ""
    run_mode: str = ""
    retry_count: int = 0
    notes: str = ""

    def to_row(self) -> dict[str, Any]:
        return {
            "Product URL": self.product_url,
            "Domain": self.domain,
            "Failure Stage": self.failure_stage,
            "Failure Reason": self.failure_reason,
            "Reason Group": self.reason_group,
            "HTML Path": self.html_path,
            "Screenshot Path": self.screenshot_path,
            "Network Log Path": self.network_log_path,
            "Run Mode": self.run_mode,
            "Retry Count": self.retry_count,
            "Notes": self.notes,
        }


def classify_reason_group(reason: str) -> str:
    low = (reason or "").lower()
    for needle, label in REASON_LABELS:
        if needle in low:
            return label
    if low.strip():
        # Short grouped label from first token
        token = re.split(r"[:\s,]+", low.strip())[0]
        return token.replace("_", " ").title()[:40] or "Unknown"
    return "Unknown"


def infer_stage(reason: str, *, run_mode: str = "", default: str = STAGE_PDP) -> str:
    low = (reason or "").lower()
    mode = (run_mode or "").lower()
    if "fetch" in low or "timeout" in low or "403" in low or "404" in low:
        return STAGE_FETCH
    if "image" in low:
        return STAGE_IMAGE
    if "variant" in low:
        return STAGE_VARIANT
    if "missing_production" in low or mode in {"validation", "production_validation"}:
        return STAGE_VALIDATION
    if mode == "audit":
        return STAGE_AUDIT
    if mode == "pilot":
        return STAGE_PILOT
    if mode == "full":
        return STAGE_FULL
    return default


def _safe_slug(url: str, max_len: int = 80) -> str:
    path = urlparse(url).path.strip("/").replace("/", "_") or "product"
    path = re.sub(r"[^a-zA-Z0-9._-]+", "-", path)
    return path[:max_len]


class BugReportCollector:
    """Collect per-product failure artifacts and write bug_report.xlsx."""

    def __init__(self, output_dir: Path, *, run_mode: str = "") -> None:
        # output_dir is the user-selected base path; artifacts go under domain subfolders.
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.run_mode = run_mode
        self.failures_dir = self.output_dir / "bug_failures"  # legacy alias
        self.records: list[FailureRecord] = []
        self._seen: set[tuple[str, str]] = set()

    def record(
        self,
        *,
        url: str,
        reason: str,
        stage: str = "",
        html: str = "",
        screenshot_png: bytes | None = None,
        network_json: list[Any] | None = None,
        retry_count: int = 0,
        notes: str = "",
        run_mode: str = "",
    ) -> FailureRecord:
        mode = run_mode or self.run_mode
        reason = str(reason or "unknown_failure")
        stage = stage or infer_stage(reason, run_mode=mode)
        domain = domain_from_url(url)
        folder = domain_folder_name(url)
        key = (url, reason)
        if key in self._seen:
            # Still allow artifact refresh if missing, but don't duplicate rows
            for existing in self.records:
                if existing.product_url == url and existing.failure_reason == reason:
                    return existing
        self._seen.add(key)

        slug = _safe_slug(url)
        base = ensure_domain_dir(self.output_dir, folder) / "bug_failures"
        base.mkdir(parents=True, exist_ok=True)
        stem = f"{len(self.records)+1:04d}_{slug}"

        html_path = ""
        if html:
            hp = base / f"{stem}.html"
            hp.write_text(html, encoding="utf-8-sig", errors="replace")
            html_path = str(hp)

        screenshot_path = ""
        if screenshot_png:
            sp = base / f"{stem}.png"
            sp.write_bytes(screenshot_png)
            screenshot_path = str(sp)

        network_log_path = ""
        if network_json:
            np = base / f"{stem}_network.json"
            write_json(np, {"url": url, "responses": network_json})
            network_log_path = str(np)

        rec = FailureRecord(
            product_url=url,
            domain=domain,
            failure_stage=stage,
            failure_reason=reason,
            reason_group=classify_reason_group(reason),
            html_path=html_path,
            screenshot_path=screenshot_path,
            network_log_path=network_log_path,
            run_mode=mode,
            retry_count=retry_count,
            notes=notes,
        )
        self.records.append(rec)
        return rec

    def extend_from_retry_queue(self, items: list[dict[str, Any]]) -> None:
        for item in items or []:
            url = str(item.get("url") or "")
            if not url:
                continue
            reason = str(item.get("reason") or "retry_queue")
            if (url, reason) in self._seen:
                continue
            self.record(
                url=url,
                reason=reason,
                stage=infer_stage(reason, run_mode=self.run_mode),
                notes="from_retry_queue",
                retry_count=1,
            )

    def reason_counts(self) -> list[tuple[str, int]]:
        counter = Counter(r.reason_group for r in self.records)
        return sorted(counter.items(), key=lambda x: (-x[1], x[0]))

    def summary(self) -> dict[str, Any]:
        by_domain: dict[str, int] = defaultdict(int)
        for r in self.records:
            by_domain[r.domain] += 1
        return {
            "total_failures": len(self.records),
            "unique_domains": len(by_domain),
            "top_failure_reasons": [
                {"reason": reason, "count": count}
                for reason, count in self.reason_counts()
            ],
            "failures_by_domain": dict(sorted(by_domain.items(), key=lambda x: -x[1])),
            "run_mode": self.run_mode,
        }

    def write(
        self,
        path: Path | None = None,
        *,
        retry_queue: list[dict[str, Any]] | None = None,
    ) -> Path:
        path = Path(path or (self.output_dir / "bug_report.xlsx"))
        return write_bug_report_xlsx(
            path,
            records=self.records,
            retry_queue=retry_queue or [],
            summary=self.summary(),
        )

    def write_by_domain(
        self,
        *,
        retry_queue: list[dict[str, Any]] | None = None,
        domain_keys: list[str] | None = None,
    ) -> list[Path]:
        """Write prefixed bug_report.xlsx (+ .json) under each domain folder."""
        retry_queue = retry_queue or []
        by_domain: dict[str, list[FailureRecord]] = {}
        for record in self.records:
            key = domain_folder_name(record.product_url)
            by_domain.setdefault(key, []).append(record)
        keys = list(domain_keys or [])
        for key in by_domain:
            if key not in keys:
                keys.append(key)
        paths: list[Path] = []
        for key in keys:
            recs = by_domain.get(key) or []
            domain_retries = [
                item
                for item in retry_queue
                if domain_folder_name(str((item or {}).get("url") or "")) == key
            ]
            path = domain_artifact_path(self.output_dir, key, "bug_report.xlsx")
            write_bug_report_xlsx(
                path,
                records=recs,
                retry_queue=domain_retries,
                summary={
                    "total_failures": len(recs),
                    "domain": key,
                    "run_mode": self.run_mode,
                },
            )
            paths.append(path)
        return paths


def write_bug_report_xlsx(
    path: Path,
    *,
    records: list[FailureRecord],
    retry_queue: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = summary or {
        "total_failures": len(records),
        "top_failure_reasons": [],
    }
    retry_queue = retry_queue or []
    reason_rows = summary.get("top_failure_reasons") or []
    if not reason_rows:
        counter = Counter(r.reason_group for r in records)
        reason_rows = [
            {"reason": k, "count": v}
            for k, v in sorted(counter.items(), key=lambda x: (-x[1], x[0]))
        ]

    domain_counter: Counter[str] = Counter(r.domain for r in records)
    domain_rows = [
        {"Domain": d, "Failed Products": c, "Top Reason": _top_reason_for_domain(records, d)}
        for d, c in domain_counter.most_common()
    ]

    try:
        import openpyxl

        wb = openpyxl.Workbook()

        ws = wb.active
        ws.title = "Failure Summary"
        ws.append(["metric", "value"])
        ws.append(["Total Failures", summary.get("total_failures", len(records))])
        ws.append(["Unique Domains", summary.get("unique_domains", len(domain_counter))])
        ws.append(["Run Mode", summary.get("run_mode", "")])
        ws.append([])
        ws.append(["Reason Group", "Count"])
        for row in reason_rows:
            ws.append([row.get("reason"), row.get("count")])

        ws_fp = wb.create_sheet("Failed Products")
        fp_fields = [
            "Product URL",
            "Domain",
            "Failure Stage",
            "Failure Reason",
            "Reason Group",
            "HTML Path",
            "Screenshot Path",
            "Network Log Path",
            "Run Mode",
            "Retry Count",
            "Notes",
        ]
        ws_fp.append(fp_fields)
        for rec in records:
            row = rec.to_row()
            ws_fp.append([row.get(f, "") for f in fp_fields])

        ws_rq = wb.create_sheet("Retry Queue")
        rq_fields = ["url", "reason", "missing_fields", "methods_attempted"]
        ws_rq.append(rq_fields)
        for item in retry_queue:
            ws_rq.append(
                [
                    item.get("url", ""),
                    item.get("reason", ""),
                    ", ".join(item.get("missing_fields") or [])
                    if isinstance(item.get("missing_fields"), list)
                    else item.get("missing_fields", ""),
                    ", ".join(item.get("methods_attempted") or [])
                    if isinstance(item.get("methods_attempted"), list)
                    else item.get("methods_attempted", ""),
                ]
            )

        ws_top = wb.create_sheet("Top Failure Reasons")
        ws_top.append(["Reason", "Count", "Rank"])
        for idx, row in enumerate(reason_rows, start=1):
            ws_top.append([row.get("reason"), row.get("count"), idx])

        ws_dom = wb.create_sheet("Domains")
        ws_dom.append(["Domain", "Failed Products", "Top Reason"])
        for row in domain_rows:
            ws_dom.append([row["Domain"], row["Failed Products"], row["Top Reason"]])

        wb.save(path)
        wb.close()
        # Machine-readable companion
        write_json(
            path.with_suffix(".json"),
            {
                "summary": summary,
                "failures": [asdict(r) for r in records],
                "retry_queue": retry_queue,
            },
        )
        return path
    except Exception:
        # CSV fallbacks
        import csv

        csv_path = path.with_suffix(".csv")
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "Product URL",
                    "Domain",
                    "Failure Stage",
                    "Failure Reason",
                    "Reason Group",
                    "HTML Path",
                    "Screenshot Path",
                    "Network Log Path",
                    "Run Mode",
                    "Retry Count",
                    "Notes",
                ],
            )
            writer.writeheader()
            for rec in records:
                writer.writerow(rec.to_row())
        write_json(
            path.with_suffix(".json"),
            {
                "summary": summary,
                "failures": [asdict(r) for r in records],
                "retry_queue": retry_queue,
            },
        )
        return csv_path


def _top_reason_for_domain(records: list[FailureRecord], domain: str) -> str:
    counter = Counter(
        r.reason_group for r in records if r.domain == domain
    )
    if not counter:
        return ""
    return counter.most_common(1)[0][0]


def print_failure_groups(records: list[FailureRecord]) -> None:
    counter = Counter(r.reason_group for r in records)
    if not counter:
        print("No failures recorded.")
        return
    print("\n=== Failure Groups ===")
    width = max(len(k) for k in counter) + 2
    for reason, count in sorted(counter.items(), key=lambda x: (-x[1], x[0])):
        dots = "." * max(2, 30 - len(reason))
        print(f"{reason} {dots} {count}")
