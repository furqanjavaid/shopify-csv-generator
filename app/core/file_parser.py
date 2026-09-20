"""Parse client CSV and Excel product files — resilient to messy sheets."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pandas as pd

from app.utils.helpers import clean_value

# Prefer these sheet names when present (case-insensitive, substring OK)
_PREFERRED_SHEET_KEYWORDS = (
    "raw merged data",
    "raw merged",
    "merged data",
    "products",
    "product data",
    "data",
)

# Sheet names containing any of these (case-insensitive) are skipped unless preferred
_SKIP_SHEET_KEYWORDS = (
    "overview",
    "summary",
    "notes",
    "instructions",
    "readme",
    "info",
    "template",
    "product review",
    "missing data",
    "brand & category",
    "brand and category",
)


class FileParser:
    """Parse CSV or Excel files into a normalized dict structure."""

    def parse(self, filepath: str) -> dict[str, Any]:
        path = Path(filepath)
        if not path.exists():
            raise ValueError(f"File not found: {filepath}")

        suffix = path.suffix.lower()
        if suffix == ".csv":
            raw = self._read_csv_raw(path)
            print(f"[FileParser] Reading CSV: {path.name}")
        elif suffix in {".xlsx", ".xls"}:
            raw = self._read_excel_selected_sheet(path)
        else:
            raise ValueError("Unsupported file type. Please use .csv, .xlsx, or .xls")

        if raw is None or raw.empty:
            raise ValueError("File is empty or has no data rows.")

        header_idx = self._detect_header_row(raw)
        df = self._apply_header(raw, header_idx)
        df = self._cleanup_columns(df)

        if df is None or df.empty:
            raise ValueError("File is empty or has no data rows after cleanup.")

        if not any(str(c).strip() for c in df.columns):
            raise ValueError("File has no readable column headers.")

        df = df.fillna("")
        df.columns = [clean_value(col) for col in df.columns]
        for col in df.columns:
            df[col] = df[col].map(clean_value)

        # Drop fully-blank rows after cleanup (keep all columns)
        df = df[df.apply(lambda r: any(str(v).strip() for v in r), axis=1)]

        headers = list(df.columns)
        rows = df.to_dict(orient="records")

        if not headers or not rows:
            raise ValueError("File is empty or has no data rows after cleanup.")

        print(f"[FileParser] Columns passed to mapping screen ({len(headers)}):")
        for i, h in enumerate(headers, start=1):
            print(f"  {i:3d}. {h!r}")

        return {
            "headers": headers,
            "rows": rows,
            "row_count": len(rows),
        }

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------

    def _read_csv_raw(self, path: Path) -> pd.DataFrame:
        """Read CSV without assuming row 0 is the header.

        Uses the csv module so title/junk rows with fewer columns than the
        real header still parse cleanly (pandas C engine would fail).
        """
        last_error: Exception | None = None
        for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
            try:
                with path.open("r", encoding=encoding, newline="") as fh:
                    # Sniff delimiter when possible; fall back to comma
                    sample = fh.read(8192)
                    fh.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=",\t|;")
                    except csv.Error:
                        dialect = csv.excel

                    reader = csv.reader(fh, dialect)
                    rows = [list(row) for row in reader]

                if not rows:
                    raise ValueError("CSV file is empty.")

                width = max(len(r) for r in rows)
                padded = [r + [""] * (width - len(r)) for r in rows]
                return pd.DataFrame(padded, dtype=object)
            except Exception as exc:
                last_error = exc
        raise ValueError(f"Unable to read CSV file: {last_error}") from last_error

    def _read_excel_selected_sheet(self, path: Path) -> pd.DataFrame:
        """Read all sheets, pick the best product sheet, return raw (no header)."""
        try:
            sheets: dict[str, pd.DataFrame] = pd.read_excel(
                path,
                sheet_name=None,
                header=None,
                dtype=object,
            )
        except Exception as exc:
            raise ValueError(f"Unable to read Excel file: {exc}") from exc

        if not sheets:
            raise ValueError("Excel file has no sheets.")

        print(f"[FileParser] Excel sheets found: {list(sheets.keys())}")
        selected_name = self._select_sheet(sheets)
        print(f"[FileParser] Selected sheet: {selected_name!r}")
        return sheets[selected_name]

    def _select_sheet(self, sheets: dict[str, pd.DataFrame]) -> str:
        """
        Prefer 'Raw Merged Data' (and similar). Skip overview/review sheets.
        Fallback: sheet with the most columns and data rows.
        """

        def col_count(df: pd.DataFrame) -> int:
            if df is None or df.empty:
                return 0
            return int(df.shape[1])

        def data_row_count(df: pd.DataFrame) -> int:
            if df is None or df.empty:
                return 0
            nonempty = df.apply(
                lambda r: any(self._is_nonempty(v) for v in r),
                axis=1,
            )
            return int(nonempty.sum())

        # 1) Exact / preferred sheet names first
        for name in sheets:
            lower = str(name).strip().lower()
            if lower == "raw merged data" or "raw merged" in lower:
                print(f"[FileParser] Preferring sheet by name: {name!r}")
                return name

        for pref in _PREFERRED_SHEET_KEYWORDS:
            for name in sheets:
                if pref in str(name).strip().lower():
                    print(f"[FileParser] Preferring sheet keyword {pref!r}: {name!r}")
                    return name

        candidates: list[tuple[str, pd.DataFrame]] = []
        for name, df in sheets.items():
            lower = str(name).lower()
            if any(kw in lower for kw in _SKIP_SHEET_KEYWORDS):
                continue
            candidates.append((name, df))

        pool = candidates if candidates else list(sheets.items())

        with_data = [(n, d) for n, d in pool if data_row_count(d) >= 2]
        search = with_data if with_data else pool

        best_name, best_cols, best_rows = None, -1, -1
        for name, df in search:
            cols = col_count(df)
            rows = data_row_count(df)
            if cols > best_cols or (cols == best_cols and rows > best_rows):
                best_name, best_cols, best_rows = name, cols, rows

        if best_name is None:
            raise ValueError("No usable sheet found in Excel file.")
        return best_name

    # ------------------------------------------------------------------
    # Header detection
    # ------------------------------------------------------------------

    def _detect_header_row(self, df: pd.DataFrame) -> int:
        """
        Header = first row in 0..9 where >= 50% of cells are non-empty strings
        (not pure numbers).
        """
        if df is None or df.empty:
            return 0

        max_scan = min(10, len(df))
        n_cols = max(df.shape[1], 1)

        for idx in range(max_scan):
            row = df.iloc[idx]
            string_cells = sum(1 for v in row if self._is_header_string(v))
            if string_cells / n_cols >= 0.5:
                return idx

        return 0

    def _apply_header(self, raw: pd.DataFrame, header_idx: int) -> pd.DataFrame:
        """Use row header_idx as column names; drop all rows above it."""
        if raw.empty:
            return raw

        header_idx = max(0, min(header_idx, len(raw) - 1))
        headers = [clean_value(v) if self._is_nonempty(v) else "" for v in raw.iloc[header_idx]]

        # Ensure unique column names
        headers = self._unique_headers(headers)

        body = raw.iloc[header_idx + 1 :].copy()
        body.columns = headers
        body = body.reset_index(drop=True)
        return body

    @staticmethod
    def _unique_headers(headers: list[str]) -> list[str]:
        seen: dict[str, int] = {}
        result: list[str] = []
        for i, h in enumerate(headers):
            name = h if h else f"Column_{i + 1}"
            if name in seen:
                seen[name] += 1
                result.append(f"{name}_{seen[name]}")
            else:
                seen[name] = 0
                result.append(name)
        return result

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _cleanup_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Keep ALL real columns for the mapping screen.

        Do NOT drop columns for:
        - names containing /, &, or spaces
        - sparse / mostly-empty data
        - looking like internal or review columns

        Only drop true Excel placeholder columns named Unnamed:*.
        """
        if df is None or df.empty:
            return df

        keep_cols: list[Any] = []
        for col in df.columns:
            name = clean_value(col)
            if name.lower().startswith("unnamed"):
                continue
            keep_cols.append(col)

        if not keep_cols:
            # Fall back: keep everything including Unnamed if that's all we have
            keep_cols = list(df.columns)

        if not keep_cols:
            return df.iloc[0:0]

        return df.loc[:, keep_cols].copy()

    # ------------------------------------------------------------------
    # Cell helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_nonempty(value: Any) -> bool:
        if value is None:
            return False
        try:
            if pd.isna(value):
                return False
        except (TypeError, ValueError):
            pass
        text = str(value).strip()
        return text != "" and text.lower() not in {"nan", "none", "nat"}

    def _is_header_string(self, value: Any) -> bool:
        """True if cell looks like a column label (non-empty string, not a number)."""
        if not self._is_nonempty(value):
            return False

        # Numeric types from Excel
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return False

        text = str(value).strip()
        # Reject pure numeric strings (e.g. "19.99", "100")
        try:
            float(text.replace(",", ""))
            return False
        except ValueError:
            return True
