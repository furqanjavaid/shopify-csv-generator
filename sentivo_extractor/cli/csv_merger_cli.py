"""
Electron bridge for CSV Merger.

Reads one JSON object from stdin:

  {"action": "analyse", "sources": [...], "settings": {...}}
  {"action": "merge", "sources": [...], "settings": {...}, "column_mappings": {...}}

analyse → prints a single JSON result object on stdout
merge   → streams [INFO]/[WARNING]/[ERROR] log lines, then a final
          <<<RESULT>>>{...json...} line
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# Ensure project root is importable when launched as a script.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _log(level: str, msg: str) -> None:
    text = f"[{level}] {msg}"
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def _read_request() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("No JSON payload on stdin.")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit("Payload must be a JSON object.")
    return data


def _source_type_key(label: str) -> str:
    text = (label or "").strip().lower().replace(" ", "_")
    aliases = {
        "scraper_output": "scraper",
        "scraper": "scraper",
        "shopify_export": "shopify_export",
        "shopify": "shopify_export",
        "supplier_list": "supplier",
        "supplier": "supplier",
        "client_file": "client",
        "client": "client",
    }
    return aliases.get(text, text or "client")


def _normalize_sources(sources: list[dict] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for src in sources or []:
        path = str((src or {}).get("path") or "").strip()
        label = str((src or {}).get("label") or Path(path).name or "source")
        source_type = _source_type_key(
            str((src or {}).get("source_type") or (src or {}).get("type") or "client")
        )
        out.append({"path": path, "label": label, "source_type": source_type})
    return out


def _priority_from_sources(sources: list[dict[str, Any]]) -> list[str]:
    """UI list order = priority (first file = highest). Unique source_types."""
    ordered: list[str] = []
    for src in sources:
        st = src["source_type"]
        if st not in ordered:
            ordered.append(st)
    return ordered or ["shopify_export", "scraper", "supplier", "client"]


def _merge_mode(settings: dict[str, Any] | None) -> str:
    raw = str((settings or {}).get("merge_mode") or (settings or {}).get("mode") or "smart")
    key = raw.strip().lower().replace(" ", "_")
    aliases = {
        "smart": "smart",
        "smart_merge": "smart",
        "append": "append",
        "update": "update",
        "replace": "replace",
        "full_replace": "replace",
    }
    return aliases.get(key, "smart")


def action_analyse(payload: dict[str, Any]) -> dict[str, Any]:
    from sentivo_extractor.core.csv_merger import (
        MAPPABLE_COLUMNS,
        SHOPIFY_EXPORT_COLUMNS,
        auto_map_columns,
        load_tabular_file,
    )

    sources = _normalize_sources(payload.get("sources"))
    unmapped_columns: dict[str, list[str]] = {}
    mapped_columns: dict[str, dict[str, str]] = {}
    errors: list[str] = []

    for src in sources:
        path = src["path"]
        label = src["label"]
        if not path:
            errors.append(f"{label}: missing path")
            continue
        try:
            df = load_tabular_file(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: failed to load ({exc})")
            continue
        cols = [str(c) for c in df.columns]
        mapping, unmapped = auto_map_columns(cols)
        mapped_columns[label] = mapping
        unmapped_columns[label] = unmapped
        _log("INFO", f"Analysed {label}: {len(mapping)} mapped, {len(unmapped)} unmapped")

    shopify_columns = list(dict.fromkeys([*MAPPABLE_COLUMNS, *SHOPIFY_EXPORT_COLUMNS]))
    return {
        "ok": len(errors) == 0,
        "unmapped_columns": unmapped_columns,
        "mapped_columns": mapped_columns,
        "shopify_columns": shopify_columns,
        "errors": errors,
    }


def _apply_user_mappings(
    sources: list[dict[str, Any]],
    column_mappings: dict[str, Any] | None,
    temp_dir: Path,
) -> list[dict[str, Any]]:
    """
    Apply UI column mappings by writing temp CSVs with renamed/dropped columns.
    Does not modify csv_merger.py — preprocess only.
    """
    from sentivo_extractor.core.csv_merger import auto_map_columns, load_tabular_file

    mappings = column_mappings or {}
    prepared: list[dict[str, Any]] = []

    for src in sources:
        path = src["path"]
        label = src["label"]
        df = load_tabular_file(path)
        auto_map, _unmapped = auto_map_columns([str(c) for c in df.columns])

        # Start from auto-map, then overlay user choices for this file label.
        rename: dict[str, str] = dict(auto_map)
        drop: set[str] = set()
        file_map = mappings.get(label) or mappings.get(src["path"]) or {}
        if isinstance(file_map, dict):
            for original, target in file_map.items():
                original = str(original)
                target_s = str(target or "").strip()
                if not target_s or target_s.lower() in {"ignore", "__ignore__", "— skip —"}:
                    drop.add(original)
                    rename.pop(original, None)
                    continue
                rename[original] = target_s

        work = df.copy()
        # Drop ignored columns that still exist.
        drop_existing = [c for c in drop if c in work.columns]
        if drop_existing:
            work = work.drop(columns=drop_existing)
        # Rename — avoid duplicate Shopify targets by keeping first.
        applied: dict[str, str] = {}
        used_targets: set[str] = set()
        for src_col, dst_col in rename.items():
            if src_col not in work.columns:
                continue
            if dst_col in used_targets:
                continue
            applied[src_col] = dst_col
            used_targets.add(dst_col)
        work = work.rename(columns=applied)

        temp_path = temp_dir / f"{len(prepared):02d}_{Path(path).stem}.csv"
        work.to_csv(temp_path, index=False, encoding="utf-8-sig")
        prepared.append(
            {
                "path": str(temp_path),
                "label": label,
                "source_type": src["source_type"],
            }
        )
        _log("INFO", f"Prepared {label} -> {temp_path.name}")

    return prepared


def action_merge(payload: dict[str, Any]) -> dict[str, Any]:
    from sentivo_extractor.core.csv_merger import export_to_shopify_csv, merge_csvs

    sources = _normalize_sources(payload.get("sources"))
    settings = payload.get("settings") or {}
    column_mappings = payload.get("column_mappings") or payload.get("mappings") or {}

    if len(sources) < 2:
        _log("ERROR", "At least 2 source files are required.")
        return {"ok": False, "errors": ["At least 2 source files are required."]}

    output_folder = str(settings.get("output_folder") or settings.get("outputFolder") or "").strip()
    output_filename = str(
        settings.get("output_filename") or settings.get("outputFilename") or "merged_output.csv"
    ).strip() or "merged_output.csv"
    if not output_filename.lower().endswith(".csv"):
        output_filename += ".csv"
    if not output_folder:
        _log("ERROR", "Output folder is required.")
        return {"ok": False, "errors": ["Output folder is required."]}

    out_path = str(Path(output_folder) / output_filename)
    mode = _merge_mode(settings)
    priority = _priority_from_sources(sources)

    _log("INFO", f"Merge mode: {mode}")
    _log("INFO", f"Source priority: {', '.join(priority)}")
    _log("INFO", f"Output: {out_path}")

    with tempfile.TemporaryDirectory(prefix="sentivo_merge_") as tmp:
        prepared = _apply_user_mappings(sources, column_mappings, Path(tmp))
        if len(prepared) < 2:
            _log("ERROR", "Could not prepare enough source files for merge.")
            return {
                "ok": False,
                "errors": ["Could not prepare enough source files for merge."],
            }

        _log("INFO", "Running merge_csvs...")
        result = merge_csvs(prepared, merge_mode=mode, source_priority=priority)
        errors = list(result.get("errors") or [])
        for err in errors:
            _log("ERROR", err)

        summary = result.get("summary") or {}
        _log(
            "INFO",
            "Summary: total={total} added={added} updated={updated} "
            "skipped={skipped} conflicts={conflicts}".format(
                total=summary.get("total", 0),
                added=summary.get("added", 0),
                updated=summary.get("updated", 0),
                skipped=summary.get("skipped", 0),
                conflicts=summary.get("conflicts", 0),
            ),
        )

        conflicts = result.get("conflicts") or []
        for c in conflicts[:50]:
            _log(
                "WARNING",
                "Conflict {field} on '{title}': kept={kept!r} discarded={discarded!r}".format(
                    field=c.get("field"),
                    title=c.get("title"),
                    kept=c.get("kept"),
                    discarded=c.get("discarded"),
                ),
            )
        if len(conflicts) > 50:
            _log("WARNING", f"…and {len(conflicts) - 50} more conflicts")

        if errors and (result.get("merged_df") is None or getattr(result["merged_df"], "empty", True)):
            return {
                "ok": False,
                "errors": errors,
                "summary": summary,
                "conflicts": conflicts,
            }

        written = export_to_shopify_csv(result["merged_df"], out_path)
        _log("INFO", f"Output Path: {written}")
        _log("INFO", "Merge complete.")
        return {
            "ok": True,
            "output_path": written,
            "summary": summary,
            "conflicts": conflicts,
            "unmapped_columns": result.get("unmapped_columns") or {},
            "errors": errors,
        }


def main() -> int:
    try:
        payload = _read_request()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "errors": [str(exc)]}), flush=True)
        return 2

    action = str(payload.get("action") or "").strip().lower()
    try:
        if action == "analyse":
            result = action_analyse(payload)
            print(json.dumps(result), flush=True)
            return 0 if result.get("ok") else 1
        if action == "merge":
            result = action_merge(payload)
            print("<<<RESULT>>>" + json.dumps(result), flush=True)
            return 0 if result.get("ok") else 1
        err = {"ok": False, "errors": [f"Unknown action: {action!r}"]}
        print(json.dumps(err), flush=True)
        return 2
    except Exception as exc:  # noqa: BLE001
        _log("ERROR", f"Fatal: {exc}")
        if action == "merge":
            print("<<<RESULT>>>" + json.dumps({"ok": False, "errors": [str(exc)]}), flush=True)
        else:
            print(json.dumps({"ok": False, "errors": [str(exc)]}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
