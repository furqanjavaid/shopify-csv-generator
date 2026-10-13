"""
Merge multiple CSV/Excel sources into one Shopify product CSV.

Supports scraper output, client files, supplier price lists, and Shopify exports.
Backend only — no UI.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd
from rapidfuzz import fuzz

logger = logging.getLogger("sentivo_extractor.csv_merger")

# Columns we auto-detect / map into (user-facing Shopify fields).
MAPPABLE_COLUMNS: list[str] = [
    "Handle",
    "Title",
    "Body (HTML)",
    "Vendor",
    "Type",
    "Tags",
    "Published",
    "Variant SKU",
    "Variant Price",
    "Variant Compare At Price",
    "Variant Inventory Qty",
    "Image Src",
    "Image Position",
    "Option1 Name",
    "Option1 Value",
    "Option2 Name",
    "Option2 Value",
    "Option3 Name",
    "Option3 Value",
]

# Full classic Shopify product CSV header used on export.
SHOPIFY_EXPORT_COLUMNS: list[str] = [
    "Handle",
    "Title",
    "Body (HTML)",
    "Vendor",
    "Product Category",
    "Type",
    "Tags",
    "Published",
    "Option1 Name",
    "Option1 Value",
    "Option2 Name",
    "Option2 Value",
    "Option3 Name",
    "Option3 Value",
    "Variant SKU",
    "Variant Barcode",
    "Variant Grams",
    "Variant Inventory Tracker",
    "Variant Inventory Qty",
    "Variant Inventory Policy",
    "Variant Fulfillment Service",
    "Variant Price",
    "Variant Compare At Price",
    "Variant Requires Shipping",
    "Variant Taxable",
    "Image Src",
    "Image Position",
    "Image Alt Text",
    "Gift Card",
    "Variant Image",
    "Status",
]

# Known aliases → Shopify column (lowercase keys).
_FIELD_ALIASES: dict[str, list[str]] = {
    "Title": [
        "title",
        "product name",
        "product title",
        "name",
        "item name",
        "item",
        "product",
    ],
    "Body (HTML)": [
        "body (html)",
        "body html",
        "body_html",
        "body",
        "description",
        "product description",
        "desc",
        "details",
        "html description",
    ],
    "Vendor": [
        "vendor",
        "brand",
        "manufacturer",
        "supplier",
        "vendor name",
    ],
    "Type": [
        "type",
        "product type",
        "category",
        "product category",
    ],
    "Tags": [
        "tags",
        "tag",
        "keywords",
        "labels",
    ],
    "Published": [
        "published",
        "published on online store",
        "status",
        "active",
    ],
    "Variant SKU": [
        "variant sku",
        "sku",
        "product sku",
        "item sku",
        "article number",
        "article",
        "part number",
        "part no",
        "mpn",
    ],
    "Variant Price": [
        "variant price",
        "price",
        "sale price",
        "retail price",
        "unit price",
        "cost",
        "amount",
    ],
    "Variant Compare At Price": [
        "variant compare at price",
        "compare at price",
        "compare-at price",
        "compare at",
        "rrp",
        "msrp",
        "was price",
        "list price",
    ],
    "Variant Inventory Qty": [
        "variant inventory qty",
        "inventory qty",
        "inventory quantity",
        "inventory",
        "stock",
        "qty",
        "quantity",
        "available",
        "stock level",
    ],
    "Image Src": [
        "image src",
        "image url",
        "product image url",
        "image",
        "photo url",
        "image link",
        "main image",
        "featured image",
    ],
    "Image Position": [
        "image position",
        "image pos",
        "position",
    ],
    "Handle": [
        "handle",
        "url handle",
        "slug",
        "product handle",
        "url",
        "seo handle",
    ],
    "Option1 Name": ["option1 name", "option 1 name", "option name"],
    "Option1 Value": ["option1 value", "option 1 value", "option value", "size", "colour", "color"],
    "Option2 Name": ["option2 name", "option 2 name"],
    "Option2 Value": ["option2 value", "option 2 value"],
    "Option3 Name": ["option3 name", "option 3 name"],
    "Option3 Value": ["option3 value", "option 3 value"],
}

_FUZZY_THRESHOLD = 80
_VALID_MODES = frozenset({"append", "update", "replace", "smart"})

# Product-level fields (first variant row carries these).
_PRODUCT_FIELDS = frozenset(
    {
        "Handle",
        "Title",
        "Body (HTML)",
        "Vendor",
        "Product Category",
        "Type",
        "Tags",
        "Published",
        "Status",
        "Gift Card",
    }
)


def _norm_header(name: str) -> str:
    text = str(name or "").strip().lower()
    text = re.sub(r"[\s_\-]+", " ", text)
    return text.strip()


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _has_value(value: Any) -> bool:
    return bool(_cell_str(value))


def load_tabular_file(path: str | Path) -> pd.DataFrame:
    """Load CSV or Excel into a DataFrame (all cells as strings where possible)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")
    suffix = p.suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        df = pd.read_excel(p, dtype=object, engine="openpyxl" if suffix != ".xls" else None)
    elif suffix in {".csv", ".txt"}:
        df = pd.read_csv(p, dtype=object, encoding="utf-8-sig")
    else:
        # Try CSV then Excel.
        try:
            df = pd.read_csv(p, dtype=object, encoding="utf-8-sig")
        except Exception:
            df = pd.read_excel(p, dtype=object, engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]
    return df.fillna("")


def _clean_title_handle_suffixes(df: pd.DataFrame) -> pd.DataFrame:
    """Strip store-name suffixes from Title and Handle before merge."""
    out = df.copy()
    if "Title" in out.columns:
        out["Title"] = (
            out["Title"]
            .astype(str)
            .str.replace(r"\s*\|.*$", "", regex=True)
            .str.strip()
        )
    if "Handle" in out.columns:
        out["Handle"] = (
            out["Handle"]
            .astype(str)
            .str.replace(r"-(the-)?[a-z]+-store$", "", regex=True)
            .str.strip()
        )
        out["Handle"] = (
            out["Handle"]
            .astype(str)
            .str.replace(r"-the-polycarbonate-store$", "", regex=True)
            .str.strip()
        )
    return out


def auto_map_columns(
    columns: list[str],
    *,
    fuzzy_threshold: int = _FUZZY_THRESHOLD,
) -> tuple[dict[str, str], list[str]]:
    """
    Map source column names → Shopify columns.

    Returns (mapping source_col → shopify_col, unmapped source columns).
    Each Shopify target is used at most once (best match wins).
    """
    mapping: dict[str, str] = {}
    used_targets: set[str] = set()
    unmapped: list[str] = []

    # Exact / alias pass first.
    pending: list[str] = []
    alias_lookup: dict[str, str] = {}
    for target, aliases in _FIELD_ALIASES.items():
        alias_lookup[_norm_header(target)] = target
        for alias in aliases:
            alias_lookup[_norm_header(alias)] = target

    for col in columns:
        key = _norm_header(col)
        if not key:
            continue
        if key in alias_lookup:
            target = alias_lookup[key]
            if target not in used_targets:
                mapping[col] = target
                used_targets.add(target)
                continue
        # Exact Shopify header (case-insensitive) for export columns too.
        for target in MAPPABLE_COLUMNS + SHOPIFY_EXPORT_COLUMNS:
            if _norm_header(target) == key and target not in used_targets:
                mapping[col] = target
                used_targets.add(target)
                break
        else:
            pending.append(col)

    # Fuzzy pass for remaining columns.
    for col in pending:
        key = _norm_header(col)
        best_target = ""
        best_score = 0
        for target in MAPPABLE_COLUMNS:
            if target in used_targets:
                continue
            candidates = [_norm_header(target)] + [
                _norm_header(a) for a in _FIELD_ALIASES.get(target, [])
            ]
            score = max(fuzz.ratio(key, c) for c in candidates)
            if score > best_score:
                best_score = score
                best_target = target
        if best_target and best_score >= fuzzy_threshold:
            mapping[col] = best_target
            used_targets.add(best_target)
        else:
            unmapped.append(col)

    return mapping, unmapped


def _normalize_dataframe(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """Rename mapped columns to Shopify names; keep extra mapped export cols."""
    rename = {src: dst for src, dst in mapping.items() if src in df.columns}
    out = df.rename(columns=rename).copy()
    # Drop duplicate Shopify columns if source had both alias and exact name.
    out = out.loc[:, ~out.columns.duplicated()]
    for col in SHOPIFY_EXPORT_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    # Coerce to string cells.
    for col in out.columns:
        out[col] = out[col].map(_cell_str)
    return out


def _slugify_handle(title: str) -> str:
    text = (title or "").lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:255]


def _ensure_handles(df: pd.DataFrame) -> pd.DataFrame:
    """Fill missing handles from title so product grouping works."""
    out = df.copy()
    handles: list[str] = []
    last_handle = ""
    for _, row in out.iterrows():
        handle = _cell_str(row.get("Handle"))
        title = _cell_str(row.get("Title"))
        if handle:
            last_handle = handle
        elif title:
            last_handle = _slugify_handle(title)
            handle = last_handle
        else:
            handle = last_handle
        handles.append(handle)
    out["Handle"] = handles
    return out


def _group_products(df: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Group rows into products.

    Same Handle → same product. Rows with same Title but different SKU and
    empty/different Handle are merged as variants under one product.
    """
    if df.empty:
        return []

    work = _ensure_handles(df)
    products: list[dict[str, Any]] = []
    # First pass: group by Handle.
    by_handle: dict[str, list[int]] = {}
    order: list[str] = []
    for idx, row in work.iterrows():
        handle = _cell_str(row.get("Handle")) or f"__row_{idx}"
        if handle not in by_handle:
            by_handle[handle] = []
            order.append(handle)
        by_handle[handle].append(int(idx) if not isinstance(idx, int) else idx)

    # Build product dicts; then merge products that share Title but differ only by SKU.
    title_index: dict[str, int] = {}
    for handle in order:
        idxs = by_handle[handle]
        rows = [work.loc[i].to_dict() for i in idxs]
        title = ""
        for r in rows:
            if _cell_str(r.get("Title")):
                title = _cell_str(r.get("Title"))
                break
        title_key = title.lower()
        skus = {_cell_str(r.get("Variant SKU")) for r in rows if _cell_str(r.get("Variant SKU"))}

        if title_key and title_key in title_index:
            existing = products[title_index[title_key]]
            existing_skus = {
                _cell_str(r.get("Variant SKU"))
                for r in existing["rows"]
                if _cell_str(r.get("Variant SKU"))
            }
            # Variant intelligence: same title, different SKUs → merge variants.
            if skus and existing_skus and skus.isdisjoint(existing_skus):
                existing["rows"].extend(rows)
                continue
            # Same title + overlapping/empty SKUs → treat as same product update.
            if not skus or not existing_skus or not skus.isdisjoint(existing_skus):
                existing["rows"].extend(rows)
                continue

        product = {
            "handle": handle if not handle.startswith("__row_") else _slugify_handle(title),
            "title": title,
            "rows": rows,
            "skus": skus,
        }
        title_index[title_key] = len(products)
        products.append(product)

    # Normalize handles on merged variant groups.
    for product in products:
        handle = _cell_str(product.get("handle")) or _slugify_handle(product.get("title") or "")
        product["handle"] = handle
        for i, row in enumerate(product["rows"]):
            row["Handle"] = handle
            if i > 0 and not _cell_str(row.get("Title")):
                # Variant continuation rows often leave Title blank in Shopify exports.
                pass
            skus = product.setdefault("skus", set())
            sku = _cell_str(row.get("Variant SKU"))
            if sku:
                skus.add(sku)
    return products


def _product_match_key(product: dict[str, Any]) -> tuple[str, str, str]:
    skus = sorted(s for s in (product.get("skus") or set()) if s)
    return (
        skus[0] if skus else "",
        _cell_str(product.get("handle")).lower(),
        _cell_str(product.get("title")).lower(),
    )


def find_matching_product(
    product: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> int | None:
    """
    Find index of matching product in catalog.

    Match ONLY on Variant SKU (exact, case-insensitive, stripped).
    Empty/NaN SKU → always a new product. Handle/Title are never used.
    """
    skus = {
        _cell_str(s).strip().lower()
        for s in (product.get("skus") or set())
        if _cell_str(s).strip()
    }
    if not skus:
        return None

    for i, other in enumerate(catalog):
        other_skus = {
            _cell_str(s).strip().lower()
            for s in (other.get("skus") or set())
            if _cell_str(s).strip()
        }
        if skus & other_skus:
            return i

    return None


def _row_by_sku(rows: list[dict[str, Any]], sku: str) -> dict[str, Any] | None:
    sku_l = sku.lower()
    for row in rows:
        if _cell_str(row.get("Variant SKU")).lower() == sku_l:
            return row
    return None


def _blank_shopify_row() -> dict[str, str]:
    return {col: "" for col in SHOPIFY_EXPORT_COLUMNS}


def _merge_row_fields(
    base: dict[str, Any],
    incoming: dict[str, Any],
    *,
    mode: str,
    source_label: str,
    product_title: str,
    conflicts: list[dict[str, str]],
    incoming_wins: bool,
) -> dict[str, str]:
    """Merge two Shopify rows field-by-field according to mode / priority."""
    out = _blank_shopify_row()
    for col in SHOPIFY_EXPORT_COLUMNS:
        base_val = _cell_str(base.get(col))
        inc_val = _cell_str(incoming.get(col))
        if mode == "replace":
            chosen = inc_val if incoming_wins else (inc_val or base_val)
            if incoming_wins and inc_val:
                chosen = inc_val
            elif not incoming_wins and base_val:
                chosen = base_val
            else:
                chosen = inc_val or base_val
        elif mode == "smart":
            if incoming_wins:
                chosen = inc_val if _has_value(inc_val) else base_val
            else:
                chosen = base_val if _has_value(base_val) else inc_val
        else:
            # update / append field merge when updating a match
            if incoming_wins:
                chosen = inc_val if _has_value(inc_val) else base_val
            else:
                chosen = base_val if _has_value(base_val) else inc_val

        if (
            _has_value(base_val)
            and _has_value(inc_val)
            and base_val != inc_val
            and col
            not in {
                "Image Position",
                "Option1 Value",
                "Option2 Value",
                "Option3 Value",
            }
        ):
            kept = chosen
            discarded = inc_val if chosen == base_val else base_val
            conflicts.append(
                {
                    "field": col,
                    "title": product_title or _cell_str(base.get("Title")) or _cell_str(incoming.get("Title")),
                    "kept": kept,
                    "discarded": discarded,
                    "source": source_label,
                }
            )
        out[col] = chosen
    return out


def _merge_product(
    base: dict[str, Any],
    incoming: dict[str, Any],
    *,
    mode: str,
    source_label: str,
    incoming_wins: bool,
    conflicts: list[dict[str, str]],
) -> dict[str, Any]:
    """Merge variant rows of two products; preserve variant structure on update."""
    base_rows = list(base.get("rows") or [])
    inc_rows = list(incoming.get("rows") or [])
    if not base_rows:
        return {
            "handle": incoming.get("handle"),
            "title": incoming.get("title"),
            "rows": inc_rows,
            "skus": set(incoming.get("skus") or set()),
        }
    if not inc_rows:
        return base

    if mode == "replace" and incoming_wins:
        # Full product replace from incoming, keep handle if incoming lacks one.
        rows = []
        for r in inc_rows:
            row = _blank_shopify_row()
            for col in SHOPIFY_EXPORT_COLUMNS:
                row[col] = _cell_str(r.get(col))
            if not row["Handle"]:
                row["Handle"] = _cell_str(base.get("handle"))
            rows.append(row)
        title = _cell_str(incoming.get("title")) or _cell_str(base.get("title"))
        handle = _cell_str(incoming.get("handle")) or _cell_str(base.get("handle"))
        skus = {_cell_str(r.get("Variant SKU")) for r in rows if _cell_str(r.get("Variant SKU"))}
        return {"handle": handle, "title": title, "rows": rows, "skus": skus}

    # Match variants by SKU; unmatched incoming variants are appended.
    merged_rows: list[dict[str, str]] = []
    used_inc: set[int] = set()
    title = _cell_str(base.get("title")) or _cell_str(incoming.get("title"))

    for b_row in base_rows:
        sku = _cell_str(b_row.get("Variant SKU"))
        match_row = None
        match_idx = None
        if sku:
            for j, i_row in enumerate(inc_rows):
                if j in used_inc:
                    continue
                if _cell_str(i_row.get("Variant SKU")).lower() == sku.lower():
                    match_row = i_row
                    match_idx = j
                    break
        if match_row is None and len(base_rows) == 1 and len(inc_rows) == 1:
            match_row = inc_rows[0]
            match_idx = 0

        if match_row is not None and match_idx is not None:
            used_inc.add(match_idx)
            merged_rows.append(
                _merge_row_fields(
                    b_row,
                    match_row,
                    mode=mode,
                    source_label=source_label,
                    product_title=title,
                    conflicts=conflicts,
                    incoming_wins=incoming_wins,
                )
            )
        else:
            # Preserve existing variant structure.
            row = _blank_shopify_row()
            for col in SHOPIFY_EXPORT_COLUMNS:
                row[col] = _cell_str(b_row.get(col))
            merged_rows.append(row)

    for j, i_row in enumerate(inc_rows):
        if j in used_inc:
            continue
        row = _blank_shopify_row()
        for col in SHOPIFY_EXPORT_COLUMNS:
            row[col] = _cell_str(i_row.get(col))
        if not row["Handle"]:
            row["Handle"] = _cell_str(base.get("handle"))
        # Product fields on extra variant rows: keep first-row values if empty.
        if merged_rows:
            for field in _PRODUCT_FIELDS:
                if not row[field]:
                    row[field] = merged_rows[0].get(field, "")
        merged_rows.append(row)

    handle = _cell_str(merged_rows[0].get("Handle")) if merged_rows else _cell_str(base.get("handle"))
    if incoming_wins and _cell_str(incoming.get("handle")):
        handle = _cell_str(incoming.get("handle"))
    title = _cell_str(merged_rows[0].get("Title")) if merged_rows else title
    skus = {_cell_str(r.get("Variant SKU")) for r in merged_rows if _cell_str(r.get("Variant SKU"))}
    return {"handle": handle, "title": title, "rows": merged_rows, "skus": skus}


def _products_to_dataframe(products: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for product in products:
        product_rows = product.get("rows") or []
        for i, raw in enumerate(product_rows):
            row = _blank_shopify_row()
            for col in SHOPIFY_EXPORT_COLUMNS:
                row[col] = _cell_str(raw.get(col))
            row["Handle"] = _cell_str(product.get("handle")) or row["Handle"]
            if i == 0 and not row["Title"]:
                row["Title"] = _cell_str(product.get("title"))
            # Shopify style: title/body only on first row of the product group.
            if i > 0:
                for field in ("Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published"):
                    # Keep values if present; blank is also valid for continuation rows.
                    pass
            rows.append(row)
    if not rows:
        return pd.DataFrame(columns=SHOPIFY_EXPORT_COLUMNS)
    return pd.DataFrame(rows, columns=SHOPIFY_EXPORT_COLUMNS)


def _priority_rank(source_type: str, source_priority: list[str]) -> int:
    """Lower number = higher priority. Unknown types sink to the end."""
    try:
        return source_priority.index(source_type)
    except ValueError:
        return len(source_priority) + 100


def merge_csvs(
    sources: list[dict],
    merge_mode: str = "smart",
    source_priority: list | None = None,
) -> dict:
    """
    Merge multiple CSV/Excel sources into one Shopify product table.

    sources = [
      {"path": "...", "source_type": "scraper|shopify_export|supplier|client", "label": "..."}
    ]
    """
    mode = (merge_mode or "smart").strip().lower()
    if mode not in _VALID_MODES:
        return {
            "merged_df": pd.DataFrame(columns=SHOPIFY_EXPORT_COLUMNS),
            "summary": {
                "total": 0,
                "added": 0,
                "updated": 0,
                "skipped": 0,
                "conflicts": 0,
            },
            "conflicts": [],
            "unmapped_columns": {},
            "errors": [f"Invalid merge_mode: {merge_mode!r}. Use append|update|replace|smart."],
        }

    priority = list(
        source_priority
        or ["shopify_export", "scraper", "supplier", "client"]
    )
    conflicts: list[dict[str, str]] = []
    unmapped_columns: dict[str, list[str]] = {}
    errors: list[str] = []
    added = 0
    updated = 0
    skipped = 0

    # Load + normalize each source, sort so lowest priority is applied first.
    loaded: list[dict[str, Any]] = []
    for src in sources or []:
        path = str((src or {}).get("path") or "").strip()
        label = str((src or {}).get("label") or Path(path).name or "source")
        source_type = str((src or {}).get("source_type") or "client").strip().lower()
        if not path:
            errors.append(f"{label}: missing path")
            continue
        try:
            raw_df = load_tabular_file(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: failed to load ({exc})")
            continue
        if raw_df.empty:
            errors.append(f"{label}: empty file")
            continue
        mapping, unmapped = auto_map_columns([str(c) for c in raw_df.columns])
        unmapped_columns[label] = unmapped
        if "Title" not in mapping.values() and "Variant SKU" not in mapping.values() and "Handle" not in mapping.values():
            errors.append(
                f"{label}: could not map Title, Handle, or Variant SKU — check column headers"
            )
        norm = _normalize_dataframe(raw_df, mapping)
        norm = _clean_title_handle_suffixes(norm)
        products = _group_products(norm)
        loaded.append(
            {
                "label": label,
                "source_type": source_type,
                "rank": _priority_rank(source_type, priority),
                "products": products,
                "path": path,
            }
        )

    if not loaded:
        return {
            "merged_df": pd.DataFrame(columns=SHOPIFY_EXPORT_COLUMNS),
            "summary": {
                "total": 0,
                "added": 0,
                "updated": 0,
                "skipped": 0,
                "conflicts": 0,
            },
            "conflicts": conflicts,
            "unmapped_columns": unmapped_columns,
            "errors": errors or ["No valid sources to merge."],
        }

    loaded.sort(key=lambda s: s["rank"], reverse=True)  # lowest priority first

    catalog: list[dict[str, Any]] = []

    for src in loaded:
        label = src["label"]
        for product in src["products"]:
            match_idx = find_matching_product(product, catalog)
            if match_idx is None:
                if mode == "update":
                    skipped += 1
                    continue
                # append / replace / smart → add new
                catalog.append(
                    {
                        "handle": product.get("handle"),
                        "title": product.get("title"),
                        "rows": [
                            {
                                **_blank_shopify_row(),
                                **{c: _cell_str(r.get(c)) for c in SHOPIFY_EXPORT_COLUMNS},
                            }
                            for r in (product.get("rows") or [])
                        ],
                        "skus": set(product.get("skus") or set()),
                    }
                )
                added += 1
                continue

            if mode == "append":
                skipped += 1
                continue

            # update / replace / smart on existing — later sources in loop are
            # higher priority (we sorted lowest-first), so incoming wins.
            incoming_wins = True
            catalog[match_idx] = _merge_product(
                catalog[match_idx],
                product,
                mode=mode,
                source_label=label,
                incoming_wins=incoming_wins,
                conflicts=conflicts,
            )
            updated += 1

    merged_df = _products_to_dataframe(catalog)
    summary = {
        "total": len(catalog),
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "conflicts": len(conflicts),
    }
    logger.info(
        "CSV merge complete mode=%s products=%s added=%s updated=%s skipped=%s conflicts=%s",
        mode,
        summary["total"],
        added,
        updated,
        skipped,
        summary["conflicts"],
    )
    return {
        "merged_df": merged_df,
        "summary": summary,
        "conflicts": conflicts,
        "unmapped_columns": unmapped_columns,
        "errors": errors,
    }


def export_to_shopify_csv(merged_df: pd.DataFrame, output_path: str) -> str:
    """
    Export merged DataFrame as a valid Shopify product CSV.
    Returns the output file path.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if merged_df is None:
        df = pd.DataFrame(columns=SHOPIFY_EXPORT_COLUMNS)
    else:
        df = merged_df.copy()

    for col in SHOPIFY_EXPORT_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df.reindex(columns=SHOPIFY_EXPORT_COLUMNS)
    for col in df.columns:
        df[col] = df[col].map(_cell_str)

    df.to_csv(out, index=False, encoding="utf-8-sig")
    logger.info("Shopify CSV written: %s (%s rows)", out, len(df))
    return str(out.resolve())
