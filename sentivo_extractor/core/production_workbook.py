"""Multi-sheet production_summary.xlsx workbook."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _write_sheet(wb, title: str, rows: list[dict[str, Any]], fields: list[str] | None = None):
    ws = wb.create_sheet(title)
    if not rows:
        ws.append(["(empty)"])
        return ws
    fields = fields or list(rows[0].keys())
    ws.append(fields)
    for row in rows:
        ws.append([row.get(f, "") for f in fields])
    return ws


def write_production_summary_workbook(
    path: Path,
    *,
    overview: dict[str, Any],
    domain_summary: list[dict[str, Any]],
    extraction_errors: list[dict[str, Any]],
    validation_errors: list[dict[str, Any]],
    yellow_items: list[dict[str, Any]],
    red_items: list[dict[str, Any]],
    image_issues: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import openpyxl

        wb = openpyxl.Workbook()
        # Overview
        ws = wb.active
        ws.title = "Overview"
        ws.append(["metric", "value"])
        for k, v in overview.items():
            if isinstance(v, (dict, list)):
                continue
            ws.append([k, v])

        _write_sheet(wb, "Domain Summary", domain_summary)
        _write_sheet(wb, "Extraction Errors", extraction_errors)
        _write_sheet(wb, "Validation Errors", validation_errors)
        _write_sheet(wb, "Yellow Review Items", yellow_items)
        _write_sheet(wb, "Red Failed Items", red_items)
        _write_sheet(wb, "Image Issues", image_issues)
        _write_sheet(wb, "Coverage", coverage)
        wb.save(path)
        return path
    except Exception:
        # Fallback: write coverage + overview as CSV siblings
        import csv
        import json

        path.with_suffix(".json").write_text(
            json.dumps(
                {
                    "overview": overview,
                    "domain_summary": domain_summary,
                    "coverage": coverage,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        cov_csv = path.with_name("coverage.csv")
        if coverage:
            with cov_csv.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(coverage[0].keys()))
                writer.writeheader()
                writer.writerows(coverage)
        return path.with_suffix(".json")
